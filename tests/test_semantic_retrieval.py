import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
def load(name):
 spec=importlib.util.spec_from_file_location(name,ROOT/(name+'.py'));module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
jobs=load('is_kaynagi');client=load('semantic_client');service=load('embedding_service')

class SemanticTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.index=jobs.JobIndex(Path(self.tmp.name)/'sources.db')
  self.index.replace('plan',[('a','Kaza','Bina hasarı belgelendi',{'esaslar':['2026/123'],'source_sha256':'evidence-a'}),('b','Diğer','Bina hasarı',{'esaslar':['2026/999']})],{'at':1})
  db=sqlite3.connect(self.index.path)
  try:self.shas=dict(db.execute('select key,sha256 from sources'))
  finally:db.close()
 def tearDown(self):self.tmp.cleanup()
 def backend(self,hits):return patch.object(jobs.JobIndex,'_semantic',return_value={'status':'ready','hits':hits,'model':'test-encoder'})
 def hit(self,key,**extra):return {'key':key,'source_sha':self.shas[key],'score':.8,'start':0,**extra}
 def test_semantic_source_without_keyword_overlap(self):
  with self.backend([self.hit('a')]):
   result=self.index.search('deprem zarar tespiti');self.assertEqual(result['hits'][0]['source'],'a');self.assertTrue(result['semantic_embedding']);self.assertEqual(result['hits'][0]['source_sha256'],'evidence-a')
 def test_stale_vectors_rejected(self):
  with self.backend([self.hit('a',source_sha='stale')]):self.assertEqual(self.index.search('deprem')['hits'],[])
 def test_exact_case_rechecked_locally(self):
  with self.backend([self.hit('b'),self.hit('a')]):self.assertEqual([h['source'] for h in self.index.search('deprem',case='2026/123')['hits']],['a'])
 def test_case_embedded_in_query_does_not_cross_cases(self):
  with self.backend([self.hit('b'),self.hit('a')]):self.assertEqual([h['source'] for h in self.index.search('deprem 2026/123')['hits']],['a'])
 def test_arbitrary_numeric_query_never_uses_semantic_fallback(self):
  with self.backend([self.hit('a')]) as mock:self.assertEqual(self.index.search('deprem 741')['hits'],[]);mock.assert_not_called()
 def test_backend_failure_preserves_keyword_results(self):
  with patch.object(jobs.JobIndex,'_semantic',side_effect=TimeoutError):
   result=self.index.search('hasar');self.assertTrue(result['hits']);self.assertEqual(result['semantic']['status'],'unavailable')
 def test_remote_semantic_url_rejected(self):
  p=Path(self.tmp.name)/'config.json';p.write_text(json.dumps({'enabled':True,'url':'https://external.example','token_file':'missing'}))
  self.assertEqual(client.search('private query',config_path=p)['reason'],'ValueError')
 def test_missing_optional_config_disables_service(self):self.assertEqual(client.search('x',config_path=Path(self.tmp.name)/'missing')['status'],'disabled')
 def test_macos_memory_pressure_levels(self):
  with patch.object(service.sys,'platform','darwin'),patch.object(service.subprocess,'check_output',return_value='2\n'):
   self.assertEqual(service.memory_pressure(),'warning')
  with patch.object(service.sys,'platform','darwin'),patch.object(service.subprocess,'check_output',return_value='4\n'):
   self.assertEqual(service.memory_pressure(),'critical')
 def test_critical_pressure_skips_model_inference(self):
  resident=service.Resident(None,'cpu')
  with patch.object(service,'memory_pressure',return_value='critical'):
   with self.assertRaises(MemoryError):resident.encode(['private query'],'SearchQuery')
 def test_complete_chunk_coverage_with_original_offsets(self):
  text=('önemli\n'+('x'*100)+'\n')*200;covered=set()
  for start,end,piece in service.chunks(text):self.assertEqual(text[start:end],piece);self.assertLessEqual(len(piece),service.CHUNK_CHARS);covered.update(range(start,end))
  self.assertEqual(len(covered),len(text))
 def test_semantic_search_preserves_source_database(self):
  raw=self.index.path.read_bytes()
  with self.backend([self.hit('a')]):self.index.search('deprem')
  self.assertEqual(raw,self.index.path.read_bytes())
