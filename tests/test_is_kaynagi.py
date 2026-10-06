import importlib.util, json, pathlib, sqlite3, tempfile, unittest
spec=importlib.util.spec_from_file_location('jobs',pathlib.Path(__file__).resolve().parents[1]/'is_kaynagi.py')
jobs=importlib.util.module_from_spec(spec);spec.loader.exec_module(jobs)

class RetrievalTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.tmp.name);self.home=self.root/'home';self.home.mkdir();self.system=self.root/'system';self.plans=self.system/'ceo-planlar';self.plans.mkdir(parents=True);self.index=self.root/'index.db'
  self.plan=self.plans/'2026-123.md';self.plan.write_text('Örnek 2. Asliye Ticaret Mahkemesi 2026/123\nYangın hasar raporu\n`t_1234abcd`\n`G:\\Dosyalar\\rapor.docx`\n2021-10-22',encoding='utf-8')
  with sqlite3.connect(self.home/'kanban.db') as d:
   d.executescript('CREATE TABLE tasks(id TEXT PRIMARY KEY,title TEXT,body TEXT,result TEXT,status TEXT,assignee TEXT,updated_at REAL); CREATE TABLE task_runs(id INTEGER,task_id TEXT,summary TEXT,metadata TEXT);')
   d.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?)',('t_1234abcd','Teslim','çalışma','rapor hazır','done','test-worker',12))
  d.close()
  jobs.build(self.home,self.system,self.index)
 def tearDown(self):self.tmp.cleanup()
 def search(self,q,**kw):return jobs.JobIndex(self.index).search(q,**kw)
 def test_turkish_bidirectional(self):
  for q in ['yangın','yangin','YANGIN','yandin','yangın hasar raporu']:
   self.assertEqual(self.search(q)['status'],'found')
 def test_single_query_contains_plan_and_task(self):
  r=self.search('yangın');self.assertEqual({h['kind'] for h in r['hits']},{'plan','kanban'})
  task=next(h for h in r['hits'] if h['kind']=='kanban');self.assertEqual(task['metadata']['status'],'done');self.assertEqual(task['metadata']['esaslar'],['2026/123']);self.assertTrue(task['metadata']['plan_sources']);self.assertIn('G:\\Dosyalar\\rapor.docx',task['metadata']['dosya_kokleri'])
 def test_date_not_case(self):self.assertEqual(jobs.metadata('2026/123 2021-10-22')['esaslar'],['2026/123'])
 def test_numeric_query_never_loosely_falls_back(self):self.assertEqual(self.search('yangın 2018/999')['hits'],[])
 def test_case_filter(self):self.assertTrue(self.search('yangin',case='2026/123')['hits']);self.assertFalse(self.search('yangin',case='2018/55')['hits'])
 def test_incremental_and_missing_provider_retains_snapshot(self):
  r=jobs.build(self.home,self.system,self.index);self.assertTrue(all(x['changed']==0 for x in r['sources']))
  self.plans.rename(self.system/'offline');jobs.build(self.home,self.system,self.index);r=self.search('yangin');self.assertTrue(r['coverage']['plan']['stale']);self.assertTrue(any(h['kind']=='plan' for h in r['hits']))
 def test_evidence_and_scope(self):
  r=self.search('yangin');self.assertFalse(r['semantic_embedding']);self.assertTrue(all(h['source_sha256'] for h in r['hits']));self.assertIn('separate verification',r['hits'][0]['evidence_claim'])
 def test_read_does_not_write_source_database(self):
  before=(self.home/'kanban.db').read_bytes();self.search('yangin');self.assertEqual(before,(self.home/'kanban.db').read_bytes())
 def test_no_hit_and_empty_query_are_not_proof_of_absence(self):
  for q in ['', 'xxunknownzz', '" OR *']:
   r=self.search(q);self.assertEqual(r['hits'],[]);self.assertIn('not proof',r['next'])
 def test_secrets_redacted(self):
  self.assertNotIn('abcdefgh',jobs.safe('token=abcdefgh'));self.assertNotIn('abcdefgh',jobs.safe('"api_key": "abcdefgh"'))
 def test_deleted_plan_removed_after_successful_snapshot(self):
  self.plan.unlink();jobs.build(self.home,self.system,self.index);self.assertFalse(any(h['kind']=='plan' for h in self.search('yangin')['hits']))
 def test_archive_map_instructions_excluded(self):
  folder=self.home/'runtime/oturum-arsivcisi/archives/x';folder.mkdir(parents=True)
  (folder/'map.json').write_text(json.dumps({'baslik':'Eski iş','istekler':[[1,'imar raporu']], 'talimat':'UNIQUEFORBIDDENZZ'}),encoding='utf-8')
  (folder/'receipt.json').write_text(json.dumps({'verified':True,'sid':'old','profile':'default','package':'missing-on-purpose.hkp'}),encoding='utf-8')
  jobs.build(self.home,self.system,self.index);self.assertTrue(self.search('imar',kind='archive')['hits']);self.assertFalse(self.search('UNIQUEFORBIDDENZZ')['hits'])

if __name__=='__main__':unittest.main()
