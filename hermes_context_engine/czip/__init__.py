# -*- coding: utf-8 -*-
"""czip context engine for Hermes — lossless compaction.

The built-in ContextCompressor summarises (or prunes) older turns and throws
the originals away. This engine IS the built-in compressor — every rule it has
(head/tail protection, tool-pair sanitising, anti-thrashing, lean tails,
proactive prune) still runs — but whatever leaves the live context is first
archived VERBATIM into a per-session HKP1 pack, and the agent gets four tools
to pull it back on demand:

  czip_search(query)  keyword hits in archived messages (index + snippet)
  czip_range(range)   archived messages a..b, verbatim
  czip_map()          pack overview (requests, tool histogram, size)
  czip_find(query)    indexed jobs/plans/archive maps, across sessions

Uses the Hermes ``ContextCompressor`` interface: it wraps ``compress()`` and
``prune_tool_results_only()`` entry points and diffs the message list before /
after. Check compatibility against your installed Hermes version before activation.

Modes (config.yaml -> czip_context.mode):
  hybrid  LLM summary as before + czip section (default). If the summary call
          fails, the static fallback still gets the czip section, so nothing
          is lost either way.
  map     no LLM summary call: the czip section is the handoff. Cheapest on
          the local model (no summarisation request competing for slots).

Other keys under czip_context: threshold_tokens, threshold_percent,
protect_first_n, protect_last_n, target_ratio (fall back to `compression:`).

Install: symlink this directory to <hermes>/plugins/context_engine/czip and
set `context: {engine: czip}`. Without the symlink Hermes logs a warning and
uses the built-in compressor.
"""

from __future__ import annotations

import collections
import contextlib
import importlib.util
import inspect
import json
import logging
import os
import re
import threading
import time
import hashlib
import tempfile
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.context_compressor import ContextCompressor, SUMMARY_PREFIX

logger = logging.getLogger(__name__)

ENGINE_NAME = "czip"
CZIP_HOME = Path(os.environ.get("CZIP_HOME", Path(__file__).resolve().parents[2]))
PACK_DIR = Path(os.path.expanduser(
    os.environ.get("CZIP_CTX_DIR", str(Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser() / "czip/context"))))

SECTION_TITLE = "## Lossless archive (czip)"
MAP_MARKER = "␟CZIP-SECTION␞"
SUMMARY_MARKS = (SUMMARY_PREFIX[:40], "[CONTEXT SUMMARY]", SECTION_TITLE)
RANGE_MAX_MESSAGES = 20
RANGE_MAX_CHARS = 24000
FIELD_MAX_CHARS = 8000
SEARCH_MAX_HITS = 12
MAP_MAX_REQUESTS = 25

_hkp = None
_hkp_lock = threading.Lock()


def _load_hkp():
    """Load Czip's hkp.py by path under a private module name."""
    global _hkp
    with _hkp_lock:
        if _hkp is None:
            spec = importlib.util.spec_from_file_location("czip_ctx_hkp", str(CZIP_HOME / "hkp.py"))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _hkp = mod
    return _hkp


def _config() -> Dict[str, Any]:
    try:
        from hermes_cli.config import load_config
        cfg = load_config() or {}
    except Exception:
        cfg = {}
    return {"compression": cfg.get("compression") or {}, "czip": cfg.get("czip_context") or {}}


def _compressor_kwargs(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Map config onto whatever this Hermes version's ContextCompressor accepts."""
    comp, own = cfg["compression"], cfg["czip"]

    def pick(key, comp_key=None, default=None):
        if key in own:
            return own[key]
        return comp.get(comp_key or key, default)

    wanted = {
        "model": "",
        "quiet_mode": True,
        "config_context_length": 131072,
        "threshold_percent": float(pick("threshold_percent", "threshold", 0.50)),
        "protect_first_n": int(pick("protect_first_n", default=1)),
        "protect_last_n": int(pick("protect_last_n", default=4)),
        "summary_target_ratio": float(pick("target_ratio", default=0.25)),
        "abort_on_summary_failure": bool(comp.get("abort_on_summary_failure", False)),
        "threshold_tokens_cap": pick("threshold_tokens"),
        "proactive_prune_tokens": int(comp.get("proactive_prune_tokens", 0) or 0),
        "proactive_prune_min_result_chars": int(comp.get("proactive_prune_min_result_chars", 8000)),
        "proactive_prune_min_reclaim_tokens": int(comp.get("proactive_prune_min_reclaim_tokens", 4096)),
        "min_tail_user_messages": int(comp.get("min_tail_user_messages", 1)),
        "tail_mode": str(comp.get("tail_mode", "lean")),
    }
    accepted = inspect.signature(ContextCompressor.__init__).parameters
    return {k: v for k, v in wanted.items() if k in accepted and v is not None}


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(p.get("text", "") for p in content if isinstance(p, dict))
    return "" if content is None else str(content)


def _key(m: Dict[str, Any]) -> str:
    return json.dumps(m, sort_keys=True, ensure_ascii=False, default=str)


def _is_summary(m: Dict[str, Any]) -> bool:
    t = _text(m.get("content"))
    return bool(m.get('_compressed_summary')) or (m.get('role') != 'user' and any(t.lstrip().startswith(s) for s in SUMMARY_MARKS[:2]))


def _fold(s):
    return ''.join(c for c in unicodedata.normalize('NFKD', str(s).casefold().replace('ı', 'i')) if not unicodedata.combining(c))


def _atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=path.name+'.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(value, f, ensure_ascii=False); f.flush(); os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp): os.unlink(temp)


@contextlib.contextmanager
def _archive_lock(path):
    # OS lock serializes read-modify-write across independent engines/processes.
    with path.with_suffix('.lock').open('a+b') as f:
        if os.name == 'nt':
            import msvcrt
            f.seek(0, os.SEEK_END)
            if f.tell() == 0:
                f.write(b'0'); f.flush()
            deadline = time.monotonic() + 30
            while True:
                f.seek(0)
                try:
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Czip archive lock unavailable')
                    time.sleep(0.05)
            try: yield
            finally:
                f.seek(0); msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try: yield
            finally: fcntl.flock(f.fileno(), fcntl.LOCK_UN)



def _clip(s: Any, n: int) -> Any:
    if isinstance(s, str) and len(s) > n:
        return s[:n] + f"…[+{len(s) - n} chars; narrow the range or search]"
    return s


def _safe_id(session_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", session_id or "nosession")[:120]


class CzipContextEngine(ContextCompressor):
    """ContextCompressor + verbatim HKP1 archive of everything it removes."""

    def __init__(self) -> None:
        cfg = _config()
        super().__init__(**_compressor_kwargs(cfg))
        self.quiet_mode = False
        mode = str(cfg["czip"].get("mode", "hybrid")).strip().lower()
        self._czip_mode = mode if mode in ("hybrid", "map") else "hybrid"
        self._czip_fixed_threshold = cfg["czip"].get("threshold_tokens", cfg["compression"].get("threshold_tokens"))
        self._czip_sid: Optional[str] = None
        self._czip_archive: List[Dict[str, Any]] = []
        self._czip_keys: set = set()
        self._czip_lock = threading.Lock()
        self._czip_reload_error = None

    # -- identity ----------------------------------------------------------
    @property
    def name(self) -> str:
        return ENGINE_NAME

    def is_available(self) -> bool:
        return (CZIP_HOME / "hkp.py").exists()

    def clone_for_agent(self):
        return CzipContextEngine()

    def update_model(self, *args, **kwargs) -> None:
        super().update_model(*args, **kwargs)
        # Older Hermes has no threshold_tokens_cap: apply the absolute cap here.
        if self._czip_fixed_threshold and not hasattr(self, "threshold_tokens_cap"):
            from agent.model_metadata import MINIMUM_CONTEXT_LENGTH
            self.threshold_tokens = max(min(int(self._czip_fixed_threshold), self.threshold_tokens),
                                        MINIMUM_CONTEXT_LENGTH)
            self.tail_token_budget = int(self.threshold_tokens * self.summary_target_ratio)

    # -- session / persistence --------------------------------------------
    def _paths(self):
        sid = _safe_id(self._czip_sid or "nosession")
        return PACK_DIR / f"{sid}.jsonl", PACK_DIR / f"{sid}.hkp"

    def on_session_start(self, session_id: str, **kwargs) -> None:
        super().on_session_start(session_id, **kwargs)
        # Keep the first id for the agent's lifetime: Hermes may rotate the
        # session id on compaction, but the archive must stay one pack.
        if self._czip_sid is None:
            sid = _safe_id(session_id)
            old = kwargs.get('old_session_id')
            if old and kwargs.get('carry_over_context'):
                root = _safe_id(old)
                previous_alias = PACK_DIR / 'aliases' / (root+'.json')
                if previous_alias.exists():
                    root = json.loads(previous_alias.read_text(encoding='utf-8')).get('root')
                if not root or root != _safe_id(root): raise ValueError('Invalid Czip continuation root')
                _atomic_json(PACK_DIR/'aliases'/(sid+'.json'),{'root':root,'source':'native carry_over_context transition'})
            alias = PACK_DIR / 'aliases' / (sid + '.json')
            if alias.exists():
                root = json.loads(alias.read_text(encoding='utf-8')).get('root')
                if not root or root != _safe_id(root):
                    raise ValueError('Invalid Czip archive alias')
                sid = root
            self._czip_sid = sid
            jl, _ = self._paths()
            if jl.exists():
                try:
                    with jl.open(encoding="utf-8") as f:
                        self._czip_archive = [json.loads(x) for x in f if x.strip()]
                    self._czip_keys = {_key(m) for m in self._czip_archive}
                except Exception as e:
                    self._czip_reload_error = str(e)
                    logger.warning("czip: could not reload archive %s: %s", jl, e)
        # Native Hermes can rotate the id after a compaction. Persist that exact
        # continuation so a new process can find the original archive.
        if _safe_id(session_id) != _safe_id(self._czip_sid):
            _atomic_json(PACK_DIR / 'aliases' / (_safe_id(session_id)+'.json'), {'root':_safe_id(self._czip_sid),'source':'same engine on_session_start continuation'})

    def on_session_reset(self) -> None:
        super().on_session_reset()
        self._czip_sid = None
        self._czip_archive = []
        self._czip_keys = set()
        self._czip_reload_error = None

    def _czip_store(self, msgs: List[Dict[str, Any]]) -> Optional[int]:
        """Append verbatim copies; returns the archive index of the first, or None."""
        if not msgs:
            return None
        if self._czip_reload_error:
            raise RuntimeError('Previous archive unreadable; refusing to overwrite it')
        if self._czip_sid is None:
            self._czip_sid = f"anon-{int(time.time())}-{os.getpid()}"
        jl, pack = self._paths()
        PACK_DIR.mkdir(parents=True, exist_ok=True)
        with self._czip_lock, _archive_lock(jl):
            if jl.exists():
                with jl.open(encoding='utf-8') as f:
                    self._czip_archive = [json.loads(x) for x in f if x.strip()]
            base = len(self._czip_archive)
            archive = self._czip_archive + msgs
            fd, temporary = tempfile.mkstemp(prefix=jl.stem+'.', dir=PACK_DIR)
            staged = Path(temporary); staged_pack = Path(temporary+'.hkp')
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as f:
                    for m in archive: f.write(json.dumps(m, ensure_ascii=False)+'\n')
                    f.flush(); os.fsync(f.fileno())
                res = _load_hkp().sikistir(archive, str(staged_pack), mod='eksiksiz', baslik=f'hermes ctx {self._czip_sid}')
                if not res.get('ok'): raise RuntimeError(res.get('hata') or 'czip pack failed')
                if _load_hkp().harita(str(staged_pack), son_n=0, istek_max=0).get('toplam') != len(archive):
                    raise RuntimeError('Czip pack message count verification failed')
                # JSONL is the exact source; HKP is the compact/search copy. Raw
                # is committed last. A pack-write failure leaves live context intact.
                staged_pack.chmod(0o600)
                os.replace(staged_pack, pack); os.replace(staged, jl)
                self._czip_archive = archive
                self._czip_keys.update(_key(m) for m in msgs)
            finally:
                for p in (staged, staged_pack):
                    if p.exists(): p.unlink()
        return base

    def _removed(self, before: List[Dict[str, Any]], after: List[Dict[str, Any]]):
        """Original messages (with their positions) that are no longer present verbatim."""
        left = collections.Counter(_key(m) for m in after)
        out = []
        for i, m in enumerate(before):
            k = _key(m)
            if left[k] > 0:
                left[k] -= 1
                continue
            if m.get("role") == "system" or _is_summary(m):
                continue
            out.append((i, m))
        return out

    # -- wrapped entry points ----------------------------------------------
    def compress(self, messages, *args, **kwargs):
        before = json.loads(json.dumps(messages, ensure_ascii=False, default=str))
        after = super().compress(messages, *args, **kwargs)
        try:
            removed = [m for _, m in self._removed(before, after)]
            base = self._czip_store(removed)
            if base is not None:
                self._hint_pruned(after, removed, base)
                self._inject_section(after, base, len(removed))
            else:
                self._strip_marker(after)
        except Exception as e:
            logger.warning("czip: archive failed; original context preserved: %s", e)
            return before
        return after

    def prune_tool_results_only(self, messages, *args, **kwargs):
        before = json.loads(json.dumps(messages, ensure_ascii=False, default=str))
        res = super().prune_tool_results_only(messages, *args, **kwargs)
        pruned = res[0] if isinstance(res, tuple) else res
        try:
            if isinstance(pruned, list) and len(pruned) == len(before):
                removed = self._removed(before, pruned)
                base = self._czip_store([m for _, m in removed])
                if base is not None:
                    for j, (i, _) in enumerate(removed):
                        m = pruned[i]
                        if m.get("role") == "tool" and isinstance(m.get("content"), str):
                            pruned[i] = {**m, "content": m["content"]
                                         + f" [full output archived: czip_range '{base + j}']"}
        except Exception as e:
            logger.warning("czip: prune archive failed; original context preserved: %s", e)
            return (before, 0) if isinstance(res, tuple) else before
        return res

    def _generate_summary(self, turns_to_summarize, *args, **kwargs):
        if self._czip_mode == "map":
            return SUMMARY_PREFIX + "\n" + MAP_MARKER
        return super()._generate_summary(turns_to_summarize, *args, **kwargs)

    # -- handoff text ------------------------------------------------------
    def _inject_section(self, msgs: List[Dict[str, Any]], base: int, n_new: int) -> None:
        section = self._section(base, n_new)
        for i in range(len(msgs) - 1, -1, -1):
            c = msgs[i].get("content")
            if not isinstance(c, str):
                continue
            if not _is_summary(msgs[i]):
                continue
            if SECTION_TITLE in c:
                # Replace the old archive appendix, preserving the native footer.
                start = c.find(SECTION_TITLE)
                end = c.find('\n\n--- END OF CONTEXT SUMMARY', start)
                c = c[:start].rstrip() + (c[end:] if end >= 0 else '')
            if MAP_MARKER in c:
                msgs[i] = {**msgs[i], "content": c.replace(MAP_MARKER, section)}
                return
            if SUMMARY_MARKS[0] in c or SUMMARY_MARKS[1] in c:
                end = "\n\n--- END OF CONTEXT SUMMARY"
                if end in c:
                    c = c.replace(end, "\n\n" + section + end, 1)
                else:
                    c = c + "\n\n" + section
                msgs[i] = {**msgs[i], "content": c}
                return
        logger.warning("czip: no summary message found; archive saved but not announced")

    @staticmethod
    def _hint_pruned(after: List[Dict[str, Any]], removed: List[Dict[str, Any]], base: int) -> None:
        """Tool results that stay in context in pruned form point at their archived original."""
        where = {m.get("tool_call_id"): base + j for j, m in enumerate(removed)
                 if m.get("role") == "tool" and m.get("tool_call_id")}
        for i, m in enumerate(after):
            idx = where.get(m.get("tool_call_id")) if m.get("role") == "tool" else None
            c = m.get("content")
            if idx is not None and isinstance(c, str) and "czip_range" not in c:
                after[i] = {**m, "content": c + f" [full output archived: czip_range '{idx}']"}

    @staticmethod
    def _strip_marker(msgs: List[Dict[str, Any]]) -> None:
        for i, m in enumerate(msgs):
            c = m.get("content")
            if isinstance(c, str) and MAP_MARKER in c:
                msgs[i] = {**m, "content": c.replace(
                    MAP_MARKER, "Earlier turns were removed to free context space.")}

    def _section(self, base: int, n_new: int) -> str:
        total = len(self._czip_archive)
        reqs = []
        for i, m in enumerate(self._czip_archive):
            if m.get("role") == "user":
                c = " ".join(_text(m.get("content")).split())
                if c:
                    reqs.append(f"  #{i}: {c[:110]}")
        tools: Dict[str, int] = {}
        for m in self._czip_archive:
            for tc in m.get("tool_calls") or []:
                n = ((tc or {}).get("function") or {}).get("name") if isinstance(tc, dict) else None
                if n:
                    tools[n] = tools.get(n, 0) + 1
        top = ", ".join(f"{k}×{v}" for k, v in sorted(tools.items(), key=lambda x: -x[1])[:10])
        lines = [
            SECTION_TITLE,
            f"Archived messages #0–#{total - 1} of this session are stored VERBATIM "
            f"(this compaction added #{base}–#{base + n_new - 1}). Exact file contents, "
            "tool outputs, decisions and user wording are recoverable. Before redoing "
            "work or guessing an old detail, call `czip_search` with keywords, then "
            "`czip_range` for the exact messages.",
        ]
        if top:
            lines.append(f"Archived tool use: {top}")
        if reqs:
            lines.append("User requests in the archive (most recent last):")
            lines.extend(reqs[-MAP_MAX_REQUESTS:])
        return "\n".join(lines)

    # -- tools ---------------------------------------------------------------
    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [
            {
                'name':'czip_find',
                'description':'Find previous jobs/reports across CEO plans and native Kanban, with case number, status, paths and source evidence. Use FIRST for work from other sessions. Read-only; not the current-session archive search.',
                'parameters':{'type':'object','properties':{'query':{'type':'string'},'limit':{'type':'integer'},'case':{'type':'string','description':'Exact case number e.g. 2026/123'},'kind':{'type':'string','enum':['plan','kanban','archive','drive']}},'required':['query']}
            },
            {
                "name": "czip_search",
                "description": (
                    "Search this session's losslessly archived earlier messages "
                    "(compacted out of context). Returns message indices with snippets. "
                    "Use before re-doing work or when an old detail is needed. For other jobs/sessions use czip_find first."),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "keywords (all must match; falls back to any)"},
                        "limit": {"type": "integer", "description": f"max hits (default 8, max {SEARCH_MAX_HITS})"},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "czip_range",
                "description": (
                    "Return archived messages verbatim by index range, e.g. '120-126' "
                    f"or '57'. At most {RANGE_MAX_MESSAGES} messages per call."),
                "parameters": {
                    "type": "object",
                    "properties": {"range": {"type": "string", "description": "'a-b' or 'n'"},'offset':{'type':'integer','description':'Character offset for a long single-message field'},'field':{'type':'string','description':'content (default), reasoning or tool_calls'},'chars':{'type':'integer','description':'Page length, max 8000'}},
                    "required": ["range"],
                },
            },
            {
                "name": "czip_map",
                "description": "Overview of this session's czip archive: size, user requests, tool histogram.",
                "parameters": {"type": "object", "properties": {}},
            },
        ]

    def handle_tool_call(self, name: str, args: Dict[str, Any], **kwargs) -> str:
        try:
            return json.dumps(self._tool(name, args or {}), ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    def _tool(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        if name == 'czip_find':
            spec = importlib.util.spec_from_file_location('czip_job_index', str(CZIP_HOME/'is_kaynagi.py'))
            jobs = importlib.util.module_from_spec(spec);spec.loader.exec_module(jobs)
            return jobs.JobIndex().search(str(args.get('query') or ''),args.get('limit') or 8,args.get('kind'),args.get('case'))
        if not self._czip_archive:
            return {"error": "No archived messages yet in THIS session (nothing has been compacted).",'scope':'current_session_only','next':'For a previous job/report use czip_find. Empty here does not mean no report or no archive elsewhere.'}
        _, pack = self._paths()
        hkp = _load_hkp()
        if name == "czip_search":
            q = str(args.get("query") or "").strip()
            limit = max(1, min(int(args.get("limit") or 8), SEARCH_MAX_HITS))
            try:
                hits = hkp.paket_ara(str(pack), q, max_satir=limit) if q else []
            except ValueError:
                hits = []
            mode = "all"
            if not hits:
                hits, mode = self._search_any(q, limit), "any"
            return {"query": q, "match": mode, "hits": hits, "archived": len(self._czip_archive),
                    "next": "czip_range with an index range around the hits"}
        if name == "czip_range":
            requested = str(args.get('range') or '')
            match = re.fullmatch(r'(\d+)(?:-(\d+))?',requested)
            if not match: return {'error':'Expected n or a-b'}
            a=int(match[1]);b=int(match[2] or a)
            if b<a:return {'error':'Range end precedes start'}
            field=str(args.get('field') or 'content')
            if field not in ('content','reasoning','reasoning_content','tool_calls'):return {'error':'Unsupported field'}
            offset=max(0,int(args.get('offset') or 0));chars=max(1,min(int(args.get('chars') or FIELD_MAX_CHARS),FIELD_MAX_CHARS))
            if offset and a!=b:return {'error':'Use one message index for paged reading'}
            items=[];used=0
            for i in range(a,min(b+1,a+RANGE_MAX_MESSAGES,len(self._czip_archive))):
                original=self._czip_archive[i];value=original.get(field,'');text=value if isinstance(value,str) else json.dumps(value,ensure_ascii=False)
                page=text[offset:offset+chars];more=offset+len(page)<len(text)
                item={'i':i,'rol':original.get('role'),'icerik':page,'field':field,'offset':offset,'total_chars':len(text),'sha256':hashlib.sha256(text.encode()).hexdigest(),'exact_source':'raw JSONL','next':{'range':str(i),'field':field,'offset':offset+len(page),'chars':chars} if more else None}
                size=len(json.dumps(item,ensure_ascii=False))
                if items and used+size>RANGE_MAX_CHARS:break
                items.append(item);used+=size
            return {'range':requested,'messages':items,'next_range':f'{a+len(items)}-{b}' if a+len(items)<=b and a+len(items)<len(self._czip_archive) else None}
        if name == "czip_map":
            h = hkp.harita(str(pack), son_n=2, istek_max=MAP_MAX_REQUESTS)
            h.pop("talimat", None)
            for m in h.get("son") or []:
                for k in ("icerik", "reasoning", "reasoning2"):
                    if k in m:
                        m[k] = _clip(m[k], 1500)
            return h
        return {"error": f"unknown czip tool {name}"}

    def _search_any(self, q: str, limit: int) -> List[Dict[str, Any]]:
        words = [w for w in re.findall(r"\w+", _fold(q), re.UNICODE) if len(w) >= 2]
        scored = []
        for i, m in enumerate(self._czip_archive):
            text = _text(m.get("content")) + " " + json.dumps(m.get("tool_calls") or "", ensure_ascii=False)
            low = _fold(text)
            s = sum(1 for w in words if w in low)
            if s:
                p = low.find(next(w for w in words if w in low))
                scored.append((s, i, m.get("role"), text[max(0, p - 40):p + 120].replace("\n", " ")))
        scored.sort(key=lambda x: (-x[0], -x[1]))
        return [{"i": i, "rol": r, "score": s, "eslesme": snip} for s, i, r, snip in scored[:limit]]

    def get_status(self) -> Dict[str, Any]:
        st = super().get_status()
        st.update({"engine": ENGINE_NAME, "mode": self._czip_mode, "archived_messages": len(self._czip_archive)})
        return st


def register(ctx) -> None:
    ctx.register_context_engine(CzipContextEngine())
