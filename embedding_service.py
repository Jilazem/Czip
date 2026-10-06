"""Resident, loopback-only EmbeddingGemma text retrieval; incremental local vectors.

Install optional requirements in a separate environment. Source databases stay
read-only; only the vector database is written. A stalled service never replaces
the keyword index. Embeddings are search candidates, never delivery evidence.
"""
import argparse
import contextlib
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
import subprocess
import sys

MODEL = 'google/embeddinggemma-2'
REVISION = '914f7f89142e33e77833254d9c9b90c3cef7303b'
DIM = 512
CHUNK_CHARS = 1200
OVERLAP = 160


def memory_pressure():
    if sys.platform != 'darwin':
        return 'unknown'
    try:
        value = subprocess.check_output(['sysctl', '-n', 'kern.memorystatus_vm_pressure_level'], timeout=1, text=True).strip()
        return {'1': 'normal', '2': 'warning', '4': 'critical'}.get(value, 'unknown')
    except (OSError, subprocess.SubprocessError):
        return 'unknown'


def chunks(text):
    """Preserve coverage of long source text; offsets refer to original characters."""
    start = 0
    while start < len(text):
        end = min(len(text), start+CHUNK_CHARS)
        if end < len(text):
            boundary = text.rfind('\n', start+CHUNK_CHARS//2, end)
            if boundary > start:
                end = boundary+1
        yield start, end, text[start:end]
        if end == len(text):
            break
        start = max(start+1, end-OVERLAP)


def readonly(path):
    con = sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro', uri=True, timeout=3)
    con.row_factory = sqlite3.Row
    return con


class Store:
    def __init__(self, sources, vectors):
        self.sources = Path(sources)
        self.vectors = Path(vectors)
        self.vectors.parent.mkdir(parents=True, exist_ok=True)
        with self.writer() as con:
            con.executescript('''CREATE TABLE IF NOT EXISTS vectors(
                key TEXT, part INTEGER, source_sha TEXT, start INTEGER, end INTEGER,
                vector BLOB, PRIMARY KEY(key,part));
                CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);''')
            signature = f'{MODEL}@{REVISION}:{DIM}:{CHUNK_CHARS}:{OVERLAP}:SearchQuery:Document'
            old = con.execute("SELECT value FROM settings WHERE key='signature'").fetchone()
            if old and old[0] != signature:
                con.execute('DELETE FROM vectors')
            con.execute("INSERT OR REPLACE INTO settings VALUES('signature',?)", (signature,))
        self.vectors.chmod(0o600)

    @contextlib.contextmanager
    def writer(self):
        con = sqlite3.connect(self.vectors, timeout=10)
        try:
            con.execute('PRAGMA journal_mode=WAL')
            with con:
                yield con
        finally:
            con.close()

    def snapshot(self):
        with contextlib.closing(readonly(self.sources)) as con:
            return [dict(row) for row in con.execute('SELECT key,title,text,metadata,sha256,kind FROM sources')]

    def current(self):
        with contextlib.closing(readonly(self.vectors)) as con:
            return dict(con.execute('SELECT key,source_sha FROM vectors GROUP BY key'))

    def put(self, source, pieces, vectors):
        with self.writer() as con:
            con.execute('DELETE FROM vectors WHERE key=?', (source['key'],))
            con.executemany('INSERT INTO vectors VALUES(?,?,?,?,?,?)',
                [(source['key'], i, source['sha256'], begin, end, vector.astype('<f4').tobytes())
                 for i, ((begin, end, _), vector) in enumerate(zip(pieces, vectors))])

    def prune(self, keys):
        with self.writer() as con:
            old = [row[0] for row in con.execute('SELECT DISTINCT key FROM vectors')]
            con.executemany('DELETE FROM vectors WHERE key=?', [(key,) for key in old if key not in keys])

    def rank(self, query_vector, limit, kind=None, case=None):
        import numpy as np
        sources = {row['key']: row for row in self.snapshot()}
        candidates = {}
        with contextlib.closing(readonly(self.vectors)) as con:
            for row in con.execute('SELECT * FROM vectors'):
                source = sources.get(row['key'])
                if not source or source['sha256'] != row['source_sha'] or (kind and source['kind'] != kind):
                    continue
                if case and case not in json.loads(source['metadata']).get('esaslar', []):
                    continue
                vector = np.frombuffer(row['vector'], dtype='<f4')
                if len(vector) != DIM:
                    continue
                score = float(vector @ query_vector)
                if not (-1 <= score <= 1.001):
                    continue
                hit = {'key': row['key'], 'source_sha': row['source_sha'], 'score': round(score, 6), 'start': row['start'], 'end': row['end']}
                if score > candidates.get(row['key'], {}).get('score', -2):
                    candidates[row['key']] = hit
        return sorted(candidates.values(), key=lambda item: -item['score'])[:limit]


class Resident:
    def __init__(self, store, device, batch_size=1):
        self.store = store
        self.device = device
        self.batch_size = batch_size
        self.model = None
        self.condition = threading.Condition()
        self.busy = False
        self.waiting_queries = 0
        self.pending = threading.Event()
        self.state = {'status': 'loading', 'indexed_sources': 0, 'total_sources': 0, 'memory_pressure': 'unknown', 'index_paused': False}
        self.started = time.monotonic()

    def encode(self, texts, prompt, background=False):
        pressure = memory_pressure()
        self.state['memory_pressure'] = pressure
        if not background and pressure == 'critical':
            raise MemoryError('Critical host memory pressure; use keyword retrieval')
        # Bound contention: search fails back to FTS instead of waiting indefinitely.
        deadline=time.monotonic()+(30 if background else 2)
        with self.condition:
            if not background:self.waiting_queries+=1
            try:
                while self.busy or (background and self.waiting_queries):
                    remaining=deadline-time.monotonic()
                    if remaining<=0:raise TimeoutError('Embedding batch busy')
                    self.condition.wait(remaining)
                self.busy=True
            finally:
                if not background:self.waiting_queries-=1
        try:
            prompt_args = {'prompt_name':prompt} if prompt=='SearchQuery' else {'prompt':''}
            return self.model.encode(texts, **prompt_args, truncate_dim=DIM,
                normalize_embeddings=True, batch_size=self.batch_size, show_progress_bar=False)
        finally:
            try:
                if self.device == 'mps':
                    import torch
                    # Release unused buffers without unloading the resident model.
                    torch.mps.synchronize()
                    torch.mps.empty_cache()
                    self.state.update(mps_allocated_bytes=torch.mps.current_allocated_memory(), mps_driver_bytes=torch.mps.driver_allocated_memory())
            finally:
                with self.condition:
                    self.busy=False
                    self.condition.notify_all()

    def worker(self):
        try:
            # Wait instead of leaving a failed daemon after a pressured login.
            while memory_pressure() in {'warning', 'critical'}:
                self.state.update(status='waiting_memory', memory_pressure=memory_pressure(), index_paused=True)
                time.sleep(5)
            self.state.update(status='loading', index_paused=False)
            import torch
            from sentence_transformers import SentenceTransformer
            torch.set_num_threads(2)
            if self.device == 'mps':
                budget = int(os.environ.get('CZIP_MPS_BUDGET_BYTES', 3*1024**3))
                recommended = torch.mps.recommended_max_memory()
                torch.mps.set_per_process_memory_fraction(min(1., budget/recommended))
                self.state['mps_budget_bytes'] = min(budget, recommended)
            self.model = SentenceTransformer(MODEL, revision=REVISION, device=self.device,
                config_kwargs={'vision_config': None, 'audio_config': None},
                model_kwargs={'dtype': torch.float32}, trust_remote_code=False)
            while True:
                try:
                    self.encode(['Czip yerel arama'], 'SearchQuery')
                    break
                except MemoryError:
                    self.state.update(status='waiting_memory', index_paused=True)
                    time.sleep(5)
            self.state.update(status='ready', index_paused=False, parameters=sum(p.numel() for p in self.model.parameters()), load_seconds=round(time.monotonic()-self.started, 2))
        except Exception as error:
            self.state.update(status='failed', error=type(error).__name__)
            return
        while True:
            try:
                self.index()
            except Exception as error:
                self.state.update(index_error=type(error).__name__, index_complete=False)
            self.pending.wait(300)
            self.pending.clear()

    def index(self):
        rows = self.store.snapshot()
        old = self.store.current()
        self.state.update(total_sources=len(rows), index_complete=False, index_error=None)
        done = 0
        changed = 0
        for row in rows:
            if old.get(row['key']) != row['sha256']:
                pieces = list(chunks(row['text'])) or [(0, 0, '')]
                vectors = []
                for start in range(0, len(pieces), self.batch_size):
                    while True:
                        pressure = memory_pressure()
                        self.state.update(memory_pressure=pressure, index_paused=pressure in {'warning', 'critical'})
                        if not self.state['index_paused']:
                            break
                        time.sleep(5)
                    group = pieces[start:start+self.batch_size]
                    vectors.extend(self.encode([f"title: {row['title'][:300]} | text: {piece[2]}" for piece in group], 'Document',background=True))
                    time.sleep(.01)  # yield the model between background batches
                self.store.put(row, pieces, vectors)
                changed += 1
            done += 1
            self.state.update(indexed_sources=done)
        self.store.prune({row['key'] for row in rows})
        self.state.update(index_complete=True, indexed_sources=done, changed_sources=changed, indexed_at=time.time())

    def search(self, query, limit, kind, case):
        if self.model is None or self.state['status'] != 'ready':
            return {'status': self.state['status'], 'hits': [], 'coverage': dict(self.state)}
        if not query.strip():
            return {'status': 'ready', 'hits': []}
        started = time.monotonic()
        vector = self.encode([query[:6000]], 'SearchQuery')[0]
        hits = self.store.rank(vector, limit, kind, case)
        return {'status': 'ready', 'hits': hits, 'model': MODEL, 'revision': REVISION,
            'dimension': DIM, 'device': self.device, 'elapsed_ms': round((time.monotonic()-started)*1000, 2), 'coverage': dict(self.state)}


def serve(resident, token, port):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # no source queries/tokens in HTTP logs

        def response(self, code, payload):
            body = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def authorized(self):
            value = self.headers.get('Authorization', '')
            return hmac.compare_digest(value.encode(), ('Bearer '+token).encode())

        def do_GET(self):
            if not self.authorized():
                return self.response(401, {'error': 'unauthorized'})
            if self.path != '/health':
                return self.response(404, {'error': 'not_found'})
            self.response(200, {**resident.state, 'model': MODEL, 'revision': REVISION, 'dimension': DIM, 'device': resident.device})

        def do_POST(self):
            if not self.authorized():
                return self.response(401, {'error': 'unauthorized'})
            try:
                length = int(self.headers.get('Content-Length', 0))
                if length <= 0 or length > 32768:
                    return self.response(413, {'error': 'invalid_length'})
                data = json.loads(self.rfile.read(length))
                if self.path == '/index':
                    resident.pending.set()
                    return self.response(202, {'status': 'queued'})
                if self.path == '/compare':
                    query = data.get('query', '')
                    documents = data.get('documents', [])
                    if not isinstance(query,str) or len(query)>6000 or not isinstance(documents,list) or not 1<=len(documents)<=8 or any(not isinstance(text,str) or len(text)>4000 for text in documents):
                        return self.response(400, {'error':'invalid_comparison'})
                    if resident.model is None:
                        return self.response(503, {'status':'loading'})
                    q=resident.encode([query],'SearchQuery')[0]
                    vectors=resident.encode(['title: none | text: '+text for text in documents],'Document')
                    return self.response(200, {'scores':[round(float(vector@q),6) for vector in vectors], 'model':MODEL,'revision':REVISION})
                if self.path != '/search':
                    return self.response(404, {'error': 'not_found'})
                query = data.get('query', '')
                if not isinstance(query, str) or len(query) > 6000:
                    return self.response(400, {'error': 'invalid_query'})
                kind, case = data.get('kind'), data.get('case')
                if kind and kind not in {'plan', 'kanban', 'archive', 'drive'}:
                    return self.response(400, {'error': 'invalid_kind'})
                result = resident.search(query, max(1, min(int(data.get('limit', 20)), 40)), kind, case)
                self.response(200, result)
            except Exception as error:
                self.response(503, {'status': 'unavailable', 'hits': [], 'reason': type(error).__name__})
    ThreadingHTTPServer(('127.0.0.1', port), Handler).serve_forever()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--sources', required=True)
    parser.add_argument('--vectors', required=True)
    parser.add_argument('--token-file', required=True)
    parser.add_argument('--port', type=int, default=19127)
    parser.add_argument('--device', choices=['cpu', 'mps'], default='cpu')
    args = parser.parse_args()
    os.umask(0o077)
    os.environ.setdefault('HF_HUB_OFFLINE', '1')
    os.environ.setdefault('HF_HUB_DISABLE_TELEMETRY', '1')
    os.environ.setdefault('TOKENIZERS_PARALLELISM', 'false')
    token = Path(args.token_file).read_text(encoding='utf-8').strip()
    if len(token) < 32:
        raise ValueError('Use a private random token of at least 32 characters')
    resident = Resident(Store(args.sources, args.vectors), args.device)
    threading.Thread(target=resident.worker, daemon=True, name='czip-vector-index').start()
    serve(resident, token, args.port)


if __name__ == '__main__':
    main()
