#!/usr/bin/env python3
"""Czip job/source retrieval: incremental local FTS, explicit evidence and coverage."""
from __future__ import annotations
import argparse, contextlib, hashlib, json, os, pathlib, re, sqlite3, subprocess, shutil, time, unicodedata, importlib.util

HOME=pathlib.Path(os.environ.get('CZIP_HERMES_ROOT',os.environ.get('HERMES_HOME','~/.hermes'))).expanduser()
SYSTEM=pathlib.Path(os.environ.get('CZIP_SYSTEM_ROOT',str(HOME/'system'))).expanduser()
INDEX=pathlib.Path(os.environ.get('CZIP_JOB_INDEX',str(HOME/'runtime/czip-is-kaynagi/index.db'))).expanduser()
TOPICS={'yangin':['yangin','kundaklama','sabotaj','sigorta'], 'ecrimisil':['ecrimisil'], 'kamulastirma':['kamulastirma'], 'imar':['imar'], 'kira':['kira'], 'icra':['icra'], 'udf':['udf','uyap']}
STOP=set('daha hizli arama yapman icin rag de ne yapilmali czipte neler olmali senin neden uzun surdu surecinide yaz yazdik yazildi var mi raporu durumu ile ilgili olusan sonuc bir bu da ve olan olarak bul bana demek istedim hasari'.split())
SCHEMA='''
CREATE TABLE IF NOT EXISTS sources(key TEXT PRIMARY KEY,kind TEXT NOT NULL,title TEXT NOT NULL,text TEXT NOT NULL,metadata TEXT NOT NULL,sha256 TEXT NOT NULL,updated REAL NOT NULL);
CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5(key UNINDEXED,title,tags,text,tokenize='unicode61 remove_diacritics 2');
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
'''

def dump(x):return json.dumps(x,ensure_ascii=False,sort_keys=True)
def fold(s):
 s=str(s).casefold().replace('ı','i')
 return ''.join(c for c in unicodedata.normalize('NFKD',s) if not unicodedata.combining(c))
def safe(s):
 s=re.sub(r'(?i)(bearer\s+)\S+',r'\1[REDACTED]',str(s))
 return re.sub(r'(?i)((?:api[_-]?key|(?:access_|refresh_)?token|password|secret)["\x27]?\s*[=:]\s*["\x27]?)[^\s,;"\x27]+',r'\1[REDACTED]',s)
def terms(q):
 q=fold(q);q=re.sub(r'\byandin\b','yangin',q)
 out=[]
 for w in re.findall(r'[a-z0-9]+',q):
  if w in {'or','and','not'}:continue  # Query syntax alone is not a search term.
  if w=='uyap':w='udf'
  if (len(w)<2 or w in STOP) and not w.isdigit():continue
  if w.startswith('rapor'):w='rapor'
  elif w.startswith('yangin'):w='yangin'
  elif w.startswith('hasar'):w='hasar'
  if w not in out:out.append(w)
 return out[:16]

def metadata(text,**extra):
 low=fold(text)
 # Calendar dates such as 2021-10-22 must never become case 2021/10.
 case_text=re.sub(r'\b20\d{2}-\d{2}-\d{2}\b','',text)
 cases=sorted(set(re.findall(r'\b(20\d{2})[/-](\d{1,6})\b',case_text)))
 cases=[y+'/'+n for y,n in cases]
 cards=sorted(set(re.findall(r'\bt_[a-f0-9]{8}\b',text)))
 paths=[]
 for value in re.findall(r'`([^`\n]{4,1200})`',text):
  if value.startswith(('/', 'G:\\','D:\\','C:\\','gdrive:')):paths.append(value)
 courts=re.findall(r'(?:[A-ZÇĞİÖŞÜ][\wÇĞİÖŞÜçğıöşü]+\s+)?\d+\.\s+Asliye\s+(?:Ticaret|Hukuk)(?:\s+Mahkemesi)?',text)
 return {'esaslar':cases,'mahkeme':sorted(set(courts)),'taraf':None,'konu_etiketleri':[k for k,words in TOPICS.items() if any(w in low for w in words)],'arama_terimleri':sorted({w for line in text.splitlines() for w in terms(line)})[:500],'kart_idleri':cards,'dosya_kokleri':list(dict.fromkeys(paths))[:30],**extra}

class JobIndex:
 def __init__(self,path=INDEX):self.path=pathlib.Path(path)
 @contextlib.contextmanager
 def writer(self):
  self.path.parent.mkdir(parents=True,exist_ok=True)
  d=sqlite3.connect(self.path,timeout=5)
  try:
   d.execute('pragma journal_mode=WAL');d.executescript(SCHEMA);self.path.chmod(0o600)
   with d:yield d
  finally:d.close()
 def replace(self,kind,docs,coverage):
  with self.writer() as d:
   previous={r[0]:r[1] for r in d.execute('select key,sha256 from sources where kind=?',(kind,))};changed=0
   for key,title,text,meta in docs:
    text=safe(text);meta['indexed_at']=coverage['at'];encoded=dump({k:v for k,v in meta.items() if k!='indexed_at'});sha=hashlib.sha256((title+text+encoded).encode()).hexdigest()
    if previous.pop(key,None)==sha:continue
    d.execute('delete from search where key=?',(key,));d.execute('insert or replace into sources values(?,?,?,?,?,?,?)',(key,kind,title,text,dump(meta),sha,coverage['at']))
    tags=' '.join(meta.get('konu_etiketleri',[])+meta.get('esaslar',[])+meta.get('kart_idleri',[])+meta.get('arama_terimleri',[]))
    d.execute('insert into search(key,title,tags,text) values(?,?,?,?)',(key,fold(title),fold(tags),fold(text)));changed+=1
   for key in previous:d.execute('delete from sources where key=?',(key,));d.execute('delete from search where key=?',(key,))
   d.execute('insert or replace into meta values(?,?)',(kind,dump({**coverage,'count':len(docs),'changed':changed})))
  return {'kind':kind,'count':len(docs),'changed':changed,'deleted':len(previous)}
 def failure(self,kind,error):
  with self.writer() as d:
   old=d.execute('select value from meta where key=?',(kind,)).fetchone();value=json.loads(old[0]) if old else {'at':None,'count':0}
   value.update(error=safe(error)[:180],failed_at=time.time(),stale=True);d.execute('insert or replace into meta values(?,?)',(kind,dump(value)))
 def _semantic(self,query,limit,kind,case):
  spec=importlib.util.spec_from_file_location('czip_semantic_client',pathlib.Path(__file__).with_name('semantic_client.py'))
  module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
  return module.search(query,limit,kind,case,config_path=os.environ.get('CZIP_SEMANTIC_CONFIG',str(HOME/'runtime/czip-is-kaynagi/semantic.json')))
 def search(self,query,limit=8,kind=None,case=None,use_semantic=True):
  started=time.perf_counter();limit=max(1,min(int(limit),20))
  inferred=metadata(query)['esaslar'];exact_case=case or (inferred[0] if len(inferred)==1 else None)
  result=self._search_lexical(query,limit,kind,exact_case)
  tokens=terms(query);numeric={w for w in tokens if w.isdigit()}
  # Do not loosen arbitrary numeric constraints through vector similarity.
  allowed=set(re.findall(r'\d+',exact_case or ''))
  if not use_semantic or not tokens or (numeric and not numeric.issubset(allowed)) or result['status']=='index_missing':return result
  try:semantic=self._semantic(query,40,kind,exact_case)
  except Exception as error:semantic={'status':'unavailable','hits':[],'reason':type(error).__name__}
  result['semantic']={k:semantic[k] for k in ['status','model','revision','dimension','device','coverage','elapsed_ms','reason'] if k in semantic}
  if not semantic.get('hits'):return result
  candidates={};scores={};origins={}
  def add(hit,rank,origin,weight):
   key=hit['source'];candidates.setdefault(key,hit);scores[key]=scores.get(key,0)+weight/(60+rank);origins.setdefault(key,set()).add(origin)
  for rank,hit in enumerate(result['hits'],1):add(hit,rank,'keyword',1)
  accepted=0
  with contextlib.closing(read_only(self.path)) as db:
   for rank,item in enumerate(semantic['hits'][:40],1):
    if not isinstance(item,dict) or not isinstance(item.get('key'),str):continue
    row=db.execute('select * from sources where key=?',(item['key'],)).fetchone()
    if not row or row['sha256']!=item.get('source_sha') or (kind and row['kind']!=kind):continue
    meta=json.loads(row['metadata'])
    if exact_case and exact_case not in meta.get('esaslar',[]):continue
    # Candidates always cite the current source, never stale embedding payload text.
    visible={k:v for k,v in meta.items() if k in {'esaslar','mahkeme','kart_id','status','assignee','plan_sources','dosya_kokleri','path','line','end_line','session_id','profile','package','scope','group'} and v is not None}
    for field in ['dosya_kokleri','plan_sources','esaslar','mahkeme']:
     if field in visible:visible[field]=visible[field][:3]
    start=max(0,min(int(item.get('start') or 0),len(row['text'])))
    hit={'source':row['key'],'kind':row['kind'],'title':row['title'],'snippet':row['text'][start:start+500],'metadata':visible,'source_sha256':meta.get('source_sha256'),'indexed_at':row['updated'],'semantic_similarity':item.get('score'),'score_kind':'RRF: BM25 + local EmbeddingGemma','evidence_claim':'retrieval candidate; read cited source; artifact existence and delivery need separate verification'}
    if row['key'] in candidates:candidates[row['key']]['semantic_similarity']=item.get('score')
    add(hit,rank,'semantic',.8);accepted+=1
  if not accepted:return result
  hits=[];groups=set()
  for key in sorted(scores,key=lambda k:-scores[k]):
   hit=candidates[key];group=hit['metadata'].get('group') or key
   if group in groups:continue
   groups.add(group);hit['retrieval']=sorted(origins[key]);hits.append(hit)
   if len(hits)>=limit:break
  result.update(hits=hits,status='found',match='hybrid',semantic_embedding=True,elapsed_ms=round((time.perf_counter()-started)*1000,3))
  result['next']='Read cited source first. Semantic similarity and indexed status are not artifact/delivery evidence.'
  return result
 def _search_lexical(self,query,limit=8,kind=None,case=None):
  started=time.perf_counter();tokens=terms(query);limit=max(1,min(int(limit),20))
  if not self.path.exists():return {'status':'index_missing','hits':[],'next':'Run czip find-index; an empty current-session archive is unrelated.'}
  d=sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro',uri=True,timeout=3);d.row_factory=sqlite3.Row
  with contextlib.closing(d):
   coverage={r['key']:json.loads(r['value']) for r in d.execute('select * from meta')};coverage={k:{**v,'stale':v.get('stale',False) or bool(v.get('at') and time.time()-v['at']>86400)} for k,v in coverage.items()}
   where='';params=[]
   if kind:where+=' AND s.kind=?';params.append(kind)
   if case:where+=" AND EXISTS (SELECT 1 FROM json_each(s.metadata,'$.esaslar') WHERE value=?)";params.append(case)
   numeric=[w for w in tokens if w.isdigit()]
   def expression(words,mode):return (' '+mode+' ').join('"'+w+'"'+('' if w.isdigit() else '*') for w in words)
   mode='all';expr=expression(tokens,'AND')
   def run(expr):return d.execute('select s.*,bm25(search,0,4,6,1) rank from search join sources s on search.key=s.key where search match ?'+where+' order by rank, s.updated desc limit ?',[expr]+params+[limit*4]).fetchall() if expr else []
   rows=run(expr)
   if not rows and tokens and not numeric:
    mode='partial';rows=run(expression(tokens,'OR'))
   hits=[];seen=set()
   for row in rows:
    meta=json.loads(row['metadata']);key=meta.get('group') or row['key']
    if key in seen:continue
    seen.add(key);text=row['text'];low=fold(text);offset=next((low.find(w) for w in tokens if w in low),0);offset=max(0,offset-60)
    # Derived search vocabulary stays in the index, never in the model context.
    visible={k:v for k,v in meta.items() if k in {'esaslar','mahkeme','kart_id','status','assignee','plan_sources','dosya_kokleri','path','line','end_line','session_id','profile','package','scope','group'} and v is not None}
    for k in ['dosya_kokleri','plan_sources','esaslar','mahkeme']:
     if k in visible:visible[k]=visible[k][:3]
    hits.append({'source':row['key'],'kind':row['kind'],'title':row['title'],'snippet':text[offset:offset+500],'metadata':visible,'source_sha256':meta.get('source_sha256'),'score_kind':'BM25 + structured tags + Turkish folding','indexed_at':row['updated'],'evidence_claim':'indexed source; file existence/hash and delivery claims need separate verification'})
    if len(hits)>=limit:break
  return {'status':'found' if hits else 'not_found_in_index','query':query,'normalized_terms':tokens,'match':mode,'hits':hits,'coverage':coverage,'elapsed_ms':round((time.perf_counter()-started)*1000,3),'semantic_embedding':False,'next':'Read the cited plan/task first. No hit is not proof that the report was never written.'}

def read_only(p):
 d=sqlite3.connect(pathlib.Path(p).resolve().as_uri()+'?mode=ro',uri=True,timeout=3);d.row_factory=sqlite3.Row;return d

def build(home=HOME,system=SYSTEM,index=INDEX,drive_cache=None):
 home=pathlib.Path(home);system=pathlib.Path(system);idx=JobIndex(index);results=[];plan_meta={}
 now=time.time();coverage={'at':now,'stale':False}
 docs=[]
 for folder in [system/'ceo-planlar',system/'ders-defteri']:
  if not folder.exists():continue
  for p in sorted(folder.glob('*.md')):
   text=p.read_text(encoding='utf-8-sig');meta=metadata(text,path=str(p.resolve()),source_sha256=hashlib.sha256(p.read_bytes()).hexdigest(),source_mtime=p.stat().st_mtime,group=str(p.resolve()))
   for task in meta['kart_idleri']:plan_meta.setdefault(task,[]).append(meta)
   lines=text.splitlines();start=0;chunk=[];size=0
   for line_no,line in enumerate(lines):
    if chunk and size+len(line)>3500:
     docs.append(('plan:'+str(p.resolve())+'#L'+str(start+1),p.stem,'\n'.join(chunk),{**meta,'line':start+1,'end_line':line_no}));chunk=[];start=line_no;size=0
    chunk.append(line);size+=len(line)+1
   if chunk:docs.append(('plan:'+str(p.resolve())+'#L'+str(start+1),p.stem,'\n'.join(chunk),{**meta,'line':start+1,'end_line':len(lines)}))
 if (system/'ceo-planlar').is_dir():
  results.append(idx.replace('plan',docs,{**coverage,'scope':'ceo-planlar/*.md + ders-defteri/*.md','folders_present':[str(p) for p in [system/'ceo-planlar',system/'ders-defteri'] if p.exists()]}))
 else:
  idx.failure('plan','CEO plan folder unavailable; retained last successful snapshot');results.append({'kind':'plan','error':'folder_unavailable'})
 try:
  with contextlib.closing(read_only(home/'kanban.db')) as db:
   docs=[];tables={r[0] for r in db.execute("select name from sqlite_master where type='table'")}
   attachment_tables={t for t in tables if t in {'attachments','task_attachments'} and 'task_id' in {c[1] for c in db.execute('pragma table_info('+t+')')}}
   for r in db.execute('select * from tasks'):
    task=dict(r);tid=task['id'];text='\n'.join(str(task.get(k) or '') for k in ['title','body','result']);artifacts=[]
    latest=db.execute('select * from task_runs where task_id=? order by id desc limit 1',(tid,)).fetchone()
    if latest:
     run=dict(latest);text+='\n'+str(run.get('summary') or '')+'\n'+str(run.get('metadata') or '')
     try:artifacts=json.loads(run.get('metadata') or '{}').get('artifacts',[])
     except (ValueError,AttributeError):pass
    for table in attachment_tables:
     if table in {'attachments','task_attachments'}:
      for file in db.execute('select * from '+table+' where task_id=?',(tid,)):
       f=dict(file);text+='\n'+dump(f);artifacts.extend([f[k] for k in ['stored_path','path'] if f.get(k)])
    meta=metadata(text,kart_id=tid,status=task['status'],assignee=task.get('assignee'),source_table='tasks+latest task_runs+attachments',artifacts=artifacts,task_updated_at=task.get('updated_at'),source_sha256=hashlib.sha256(dump(task).encode()).hexdigest(),group=tid)
    linked=plan_meta.get(tid,[])
    meta['plan_sources']=[m['path'] for m in linked]
    for field in ['esaslar','mahkeme','konu_etiketleri','dosya_kokleri','arama_terimleri']:
     meta[field]=list(dict.fromkeys(meta[field]+[x for m in linked for x in m[field]]))
    # Derived tags carry their plan provenance; they are never invented by an LLM.
    docs.append(('kanban:'+tid,task['title'],text,meta))
   results.append(idx.replace('kanban',docs,{**coverage,'scope':'native tasks, latest runs and attachment paths; status is indexed snapshot'}))
 except Exception as e:idx.failure('kanban',type(e).__name__);results.append({'kind':'kanban','error':type(e).__name__})
 archive_root=home/'runtime/oturum-arsivcisi/archives'
 if archive_root.is_dir():
  try:
   docs=[]
   for p in archive_root.rglob('map.json'):
    m=json.loads(p.read_text(encoding='utf-8'));receipt=json.loads(p.with_name('receipt.json').read_text(encoding='utf-8'))
    if not receipt.get('verified'):continue
    # Compact map only: source instructions and full packages are excluded.
    title=str(m.get('baslik') or receipt.get('sid'));text=title+'\n'+'\n'.join(str(row[1]) for row in m.get('istekler',[]) if isinstance(row,list) and len(row)>1)
    meta=metadata(text,session_id=receipt.get('sid'),profile=receipt.get('profile'),package=receipt.get('package'),source_map=str(p),source_sha256=hashlib.sha256(p.read_bytes()).hexdigest(),message_count=m.get('toplam'),group=str(p.parent),scope='verified compact map only; retrieve selected original messages from package')
    docs.append(('archive:'+str(p),title,text,meta))
   results.append(idx.replace('archive',docs,{**coverage,'scope':'verified archive maps: titles and compact user requests; no full transcripts'}))
  except Exception as e:idx.failure('archive',type(e).__name__);results.append({'kind':'archive','error':type(e).__name__})
 if drive_cache:
  p=pathlib.Path(drive_cache)
  try:
   data=json.loads(p.read_text());docs=[]
   for item in data['files']:
    path=item['Path'];meta=metadata(path,remote=data['remote'],path=data['remote'].rstrip('/')+'/'+path,group=path)
    docs.append(('drive:'+meta['path'],path,path,meta))
   results.append(idx.replace('drive',docs,{'at':data['at'],'stale':False,'scope':'names/paths only','complete':data.get('complete',False),'max_depth':data.get('max_depth')}))
  except Exception as e:idx.failure('drive',type(e).__name__);results.append({'kind':'drive','error':type(e).__name__})
 return {'index':str(index),'sources':results,'elapsed_ms':round((time.time()-now)*1000,3),'external_embeddings':False}

def refresh_drive(path,remote,timeout=45):
 started=time.time();p=pathlib.Path(path)
 try:
  r=subprocess.run([os.environ.get('CZIP_RCLONE') or shutil.which('rclone') or 'rclone','lsjson',remote,'--recursive','--files-only','--max-depth','4'],capture_output=True,timeout=timeout)
  if r.returncode:raise RuntimeError('rclone failed; previous cache retained')
  files=json.loads(r.stdout);data={'remote':remote,'at':time.time(),'files':[{'Path':x['Path'],'Size':x.get('Size'),'ModTime':x.get('ModTime')} for x in files],'complete':False,'max_depth':4}
  p.parent.mkdir(parents=True,exist_ok=True);tmp=p.with_suffix('.tmp');tmp.write_text(dump(data),encoding='utf-8');tmp.chmod(0o600);os.replace(tmp,p)
  return {'status':'ok','count':len(files),'max_depth':4,'complete':False,'seconds':round(time.time()-started,3)}
 except Exception as e:return {'status':'failed','reason':type(e).__name__,'previous_cache_preserved':p.exists(),'seconds':round(time.time()-started,3)}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('command',choices=['search','index','drive']);ap.add_argument('query',nargs='?',default='');ap.add_argument('--index',default=str(INDEX));ap.add_argument('--home',default=str(HOME));ap.add_argument('--system',default=str(SYSTEM));ap.add_argument('--limit',type=int,default=8);ap.add_argument('--case');ap.add_argument('--kind');ap.add_argument('--remote',help='Explicit rclone remote/path for names-only Drive refresh');ap.add_argument('--drive-cache',default=str(HOME/'runtime/czip-is-kaynagi/drive-tree.json'));a=ap.parse_args()
 if a.command=='search':result=JobIndex(a.index).search(a.query,a.limit,a.kind,a.case)
 elif a.command=='index':result=build(a.home,a.system,a.index,a.drive_cache if pathlib.Path(a.drive_cache).exists() else None)
 else:
  if not a.remote:ap.error('drive requires --remote (no account/path is assumed)')
  result=refresh_drive(a.drive_cache,a.remote)
  if result.get('status')=='failed':JobIndex(a.index).failure('drive',result.get('reason'))
 print(json.dumps(result,ensure_ascii=False));return 1 if result.get('status')=='failed' else 0

if __name__=='__main__':raise SystemExit(main())
