from site_settings import LOOPBACK
import importlib.util, json, pathlib, subprocess, sys, tempfile, time, unittest, urllib.request, urllib.error
SRC=pathlib.Path(__file__).resolve().parents[1]/'src'/'localbrain.py'
spec=importlib.util.spec_from_file_location('localbrain',SRC); module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)

class BrainTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='localbrain-test-'); self.brain=module.Brain(self.tmp.name)
        self.path=self.brain.docs/'decisions'/'design.md'
        self.path.write_text('# 設計判断\n\n指定外のモデル接続先は使用しない。\n\n# 設定\n\nplayerId must be unique.\n',encoding='utf-8')
        module.atomic_json(self.path.with_suffix('.md.meta.json'),{'project':'demo','status':'active','evidence_level':'confirmed'})
        self.brain.index()
    def tearDown(self): self.tmp.cleanup()
    def test_exact_and_project(self):
        self.assertTrue(self.brain.search('playerId','demo')['results'])
        self.assertFalse(self.brain.search('playerId','other')['results'])
        self.assertTrue(self.brain.search('使用しない','demo')['results'])
    def test_stale_source_never_returned(self):
        item=self.brain.search('playerId')['results'][0]
        self.path.write_text('# Replaced\nnew setting',encoding='utf-8')
        self.assertFalse(self.brain.search('playerId')['results'])
        with self.assertRaises(ValueError): self.brain.get(item['id'])
        self.brain.index(); self.assertTrue(self.brain.search('new setting')['results'])
    def test_metadata_status_change_hidden_before_reindex(self):
        module.atomic_json(self.path.with_suffix('.md.meta.json'),{'project':'demo','status':'superseded','evidence_level':'confirmed'})
        self.assertFalse(self.brain.search('playerId')['results'])
        self.brain.index(); self.assertFalse(self.brain.search('playerId')['results'])
    def test_deleted_and_renamed(self):
        self.path.rename(self.path.with_name('renamed.md')); self.brain.index()
        rows=self.brain.search('playerId')['results']; self.assertEqual(len(rows),1)
        self.assertTrue(rows[0]['path'].endswith('renamed.md'))
        self.path.with_name('renamed.md').unlink(); self.brain.index()
        self.assertFalse(self.brain.search('playerId')['results'])
    def test_context_budget(self):
        for budget in (64,256,500,1200):
            result=self.brain.context('playerId',budget=budget)
            self.assertLessEqual(len(result['markdown'].encode('utf-8')),budget)
    def test_draft_idempotency_and_exclusion(self):
        first=self.brain.propose('request-0001','Suggestion','UNCONFIRMED-ZYX','demo')
        second=self.brain.propose('request-0001','Suggestion','UNCONFIRMED-ZYX','demo')
        self.assertFalse(first['duplicate']); self.assertTrue(second['duplicate'])
        with self.assertRaises(ValueError): self.brain.propose('request-0001','Other','body')
        self.brain.index(); self.assertFalse(self.brain.search('UNCONFIRMED-ZYX')['results'])
        self.assertTrue(self.brain.search('UNCONFIRMED-ZYX',include_drafts=True)['results'])
    def test_proposal_rejects_secret(self):
        with self.assertRaises(ValueError):
            self.brain.propose('request-secret','Credential','api_key = sk-abcdefghijklmnopqrstuvwxyz123456')
    def test_path_escape(self):
        for path in ('../../secret.txt','C:/Windows/win.ini'):
            with self.assertRaises(ValueError): self.brain.safe_path(path)
        with self.assertRaises(ValueError): self.brain.propose('../outside','x','body')
    def test_import_repeat(self):
        source=pathlib.Path(self.tmp.name)/'original.md'; source.write_text('source evidence',encoding='utf-8')
        one=self.brain.import_file(source,'demo'); two=self.brain.import_file(source,'demo')
        self.assertFalse(one['duplicate']); self.assertTrue(two['duplicate']); self.assertTrue(one['queued']); self.assertFalse(two['queued']); self.brain.index()
        self.assertEqual(len(self.brain.search('source evidence')['results']),1)
    def test_long_line_is_bounded_and_complete(self):
        text='日本語'*10000; chunks=list(self.brain.chunks(text,'x','revision'))
        self.assertEqual(''.join(x[5] for x in chunks),text)
        self.assertTrue(all(len(x[5].encode())<=6000 for x in chunks))
    def test_http_auth(self):
        import socket
        with socket.socket() as s: s.bind((LOOPBACK,0)); port=s.getsockname()[1]
        process=subprocess.Popen([sys.executable,str(SRC),'--root',self.tmp.name,'serve','--port',str(port)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            base=f'http://{LOOPBACK}:{port}'
            for _ in range(50):
                try: urllib.request.urlopen(base+'/health',timeout=1).close(); break
                except OSError: time.sleep(.1)
            body=json.dumps({'query':'playerId'}).encode()
            with self.assertRaises(urllib.error.HTTPError) as caught: urllib.request.urlopen(urllib.request.Request(base+'/search',data=body),timeout=3)
            self.assertEqual(caught.exception.code,401)
            req=urllib.request.Request(base+'/search',data=body,headers={'Authorization':'Bearer '+self.brain.keys['read']})
            self.assertTrue(json.load(urllib.request.urlopen(req,timeout=3))['results'])
            req=urllib.request.Request(base+'/propose',data=b'{}',headers={'Authorization':'Bearer '+self.brain.keys['read']})
            with self.assertRaises(urllib.error.HTTPError) as caught: urllib.request.urlopen(req,timeout=3)
            self.assertEqual(caught.exception.code,401)
        finally:
            process.terminate(); process.wait(timeout=10)

if __name__=='__main__': unittest.main()
