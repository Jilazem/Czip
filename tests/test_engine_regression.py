import importlib.util,json,pathlib,sys,tempfile,unittest,os
# Portable adapter tests. Stub the Hermes superclass only; HKP and SQLite are real.
# Native integration checks belong in hermes_context_engine/test_czip_engine.py.
import types
ROOT=pathlib.Path(__file__).resolve().parents[1]
class ContextCompressor:
 def __init__(self,**kw):pass
 def on_session_start(self,*a,**kw):pass
 def on_session_reset(self):pass
 def compress(self,messages,*a,**kw):return messages
 def prune_tool_results_only(self,messages,*a,**kw):return messages,0
 def get_status(self):return {}
SUMMARY_PREFIX='[CONTEXT COMPACTION — REFERENCE ONLY]'
stub=types.ModuleType('agent.context_compressor');stub.ContextCompressor=ContextCompressor;stub.SUMMARY_PREFIX=SUMMARY_PREFIX
previous=sys.modules.get('agent.context_compressor');sys.modules['agent.context_compressor']=stub
tmp=tempfile.TemporaryDirectory();os.environ['CZIP_CTX_DIR']=tmp.name
spec=importlib.util.spec_from_file_location('engine_test',ROOT/'hermes_context_engine/czip/__init__.py');mod=importlib.util.module_from_spec(spec)
try:spec.loader.exec_module(mod)
finally:
 if previous is None:sys.modules.pop('agent.context_compressor',None)
 else:sys.modules['agent.context_compressor']=previous
import atexit
atexit.register(tmp.cleanup)
mod.PACK_DIR=pathlib.Path(tmp.name)
mod._config=lambda:{'czip':{'mode':'map'},'compression':{}}

def write_from_process(folder, value):
 mod.PACK_DIR=pathlib.Path(folder)
 e=mod.CzipContextEngine();e.on_session_start('parallel')
 for i in range(5):e._czip_store([{'role':'user','content':value+str(i)}])

class RegressionTests(unittest.TestCase):
 def engine(self,sid):
  e=mod.CzipContextEngine();e.on_session_start(sid);return e
 def test_quote_is_not_a_summary(self):
  m={'role':'user','content':'Ekte '+SUMMARY_PREFIX+' hakkında metin var'}
  self.assertFalse(mod._is_summary(m));e=self.engine('quote');self.assertEqual(e._removed([m],[]),[(0,m)])
 def test_repeated_user_instruction_preserved(self):
  e=self.engine('repeat');m={'role':'user','content':'devam et'};e._czip_store([m]);e._czip_store([x for _,x in e._removed([m],[])]);self.assertEqual(e._czip_archive,[m,m])
 def test_rotated_session_reloads_original_archive(self):
  e=self.engine('root');e._czip_store([{'role':'user','content':'Yangın raporu 2026/123'}]);e.on_session_start('continuation');fresh=self.engine('continuation');self.assertEqual(fresh._czip_sid,'root');self.assertEqual(fresh._czip_archive,e._czip_archive)
 def test_exact_long_output_pagination(self):
  e=self.engine('page');text='  Yangın\n\t'+('a  b\n'*5000)+' SON ';e._czip_store([{'role':'tool','content':text,'tool_call_id':'x'}]);parts=[];args={'range':'0','chars':5000}
  while args:
   result=e._tool('czip_range',args)['messages'][0];parts.append(result['icerik']);args=result['next']
  self.assertEqual(''.join(parts),text);self.assertEqual(self.engine('page')._czip_archive[0]['content'],text)
 def test_native_reset_then_carry_transition(self):
  e=self.engine('native-root');e._czip_store([{'role':'user','content':'keep previous'}]);e.on_session_reset();e.on_session_start('native-child',old_session_id='native-root',carry_over_context=True);self.assertEqual(len(e._czip_archive),1);self.assertEqual(self.engine('native-child')._czip_sid,'native-root')
 def test_failed_pack_does_not_commit_raw_or_memory(self):
  e=self.engine('failed');hkp=mod._load_hkp();old=hkp.sikistir
  try:
   hkp.sikistir=lambda *a,**k:{'ok':False,'hata':'simulated disk failure'}
   with self.assertRaises(RuntimeError):e._czip_store([{'role':'user','content':'important'}])
   self.assertFalse(e._paths()[0].exists());self.assertEqual(e._czip_archive,[])
  finally:hkp.sikistir=old
 def test_compaction_archive_failure_preserves_context(self):
  e=self.engine('failed-compression');old=ContextCompressor.compress;before=[{'role':'user','content':'preserve me'}]
  try:
   ContextCompressor.compress=lambda *a,**k:[{'role':'assistant','content':'summary'}];e._czip_store=lambda *a:(_ for _ in ()).throw(OSError('disk'))
   self.assertEqual(e.compress(before),before)
  finally:ContextCompressor.compress=old
 def test_prune_failure_preserves_context_and_reports_zero_reclaim(self):
  e=self.engine('failed-prune');old=ContextCompressor.prune_tool_results_only;before=[{'role':'tool','content':'original'}]
  try:
   ContextCompressor.prune_tool_results_only=lambda *a,**k:([{'role':'tool','content':'pruned'}],15);e._czip_store=lambda *a:(_ for _ in ()).throw(OSError('disk'))
   self.assertEqual(e.prune_tool_results_only(before),(before,0))
  finally:ContextCompressor.prune_tool_results_only=old
 def test_empty_session_points_to_job_search(self):
  e=self.engine('empty');self.assertIn('czip_find',e._tool('czip_search',{'query':'yangin'})['next'])
 def test_current_session_turkish_folding(self):
  e=self.engine('fold');e._czip_store([{'role':'user','content':'Yangın hasar işi'}]);self.assertTrue(e._tool('czip_search',{'query':'yangin'})['hits'])
 def test_one_archive_appendix(self):
  e=self.engine('appendix');e._czip_store([{'role':'user','content':'x'}]);msgs=[{'role':'assistant','content':SUMMARY_PREFIX+'\nold\n\n--- END OF CONTEXT SUMMARY'}];e._inject_section(msgs,0,1);e._inject_section(msgs,0,1);self.assertEqual(msgs[0]['content'].count(mod.SECTION_TITLE),1);self.assertIn('END OF CONTEXT SUMMARY',msgs[0]['content'])
 def test_job_search_works_without_session_archive(self):
  import sqlite3
  spec=importlib.util.spec_from_file_location('fixture_jobs',ROOT/'is_kaynagi.py');jobs=importlib.util.module_from_spec(spec);spec.loader.exec_module(jobs)
  index=pathlib.Path(tmp.name)/'jobs.db'
  jobs.JobIndex(index).replace('kanban',[('kanban:t_1234abcd','Test','Yangın raporu',{'kart_id':'t_1234abcd','esaslar':['2026/123']})],{'at':1})
  old=os.environ.get('CZIP_JOB_INDEX');os.environ['CZIP_JOB_INDEX']=str(index)
  try:
   r=self.engine('find-empty')._tool('czip_find',{'query':'yangın','case':'2026/123'});self.assertEqual(r['status'],'found');self.assertEqual(r['hits'][0]['metadata']['kart_id'],'t_1234abcd')
  finally:
   if old is None:os.environ.pop('CZIP_JOB_INDEX',None)
   else:os.environ['CZIP_JOB_INDEX']=old
 def test_invalid_range_rejected(self):
  e=self.engine('invalid');e._czip_store([{'role':'user','content':'x'}]);self.assertIn('error',e._tool('czip_range',{'range':'../x'}))
 def test_two_engine_writers_preserve_both_messages(self):
  a=self.engine('two-writers');b=self.engine('two-writers');a._czip_store([{'role':'user','content':'a'}]);b._czip_store([{'role':'user','content':'b'}]);self.assertEqual([m['content'] for m in self.engine('two-writers')._czip_archive],['a','b'])
 def test_corrupt_raw_preserved(self):
  p=pathlib.Path(tmp.name)/'corrupt.jsonl';p.write_text('not json');e=self.engine('corrupt')
  with self.assertRaises(RuntimeError):e._czip_store([{'role':'user','content':'x'}])
  self.assertEqual(p.read_text(),'not json')
 def test_independent_process_writers_preserve_all_messages(self):
  import multiprocessing
  ctx=multiprocessing.get_context('spawn')
  with tempfile.TemporaryDirectory() as folder:
   processes=[ctx.Process(target=write_from_process,args=(folder,value)) for value in ['a','b']]
   for p in processes:p.start()
   try:
    for p in processes:p.join(20);self.assertEqual(p.exitcode,0)
    messages=[json.loads(line)['content'] for line in (pathlib.Path(folder)/'parallel.jsonl').read_text(encoding='utf-8').splitlines()]
    self.assertEqual(set(messages),{value+str(i) for value in ['a','b'] for i in range(5)});self.assertEqual(len(messages),10)
   finally:
    for p in processes:
     if p.is_alive():p.terminate();p.join()

if __name__=='__main__':unittest.main()
