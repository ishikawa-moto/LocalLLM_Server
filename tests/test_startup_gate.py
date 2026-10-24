"""Public existing-history fixtures. No production DB/config or external calls."""
import datetime as dt
import hashlib
import http.client
import json
import sqlite3
import tempfile
import threading
import unittest
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import startup_gate as gate
import chat_service as chat
import control_gateway as gateway
from slack_gateway import RestrictedStore
from site_settings import LOOPBACK
from test_chat import Fake

class GateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.old=(gate._binding,gate._spec_sha);gate._binding=None;gate._spec_sha=None
        self.store=chat.Store(self.root/'runtime/chat.sqlite3');self.cid=self.store.create('shared')
        self.store.complete('shared',self.cid,'PUBLIC QUESTION','PUBLIC ANSWER',{})
        self.slack=RestrictedStore(self.root)
        with self.slack.db():pass
        self.jobs=self.root/'runtime/gateway-jobs.sqlite3'
        gateway.LifecycleController(gateway.CONFIG,health=lambda port:False,audit_fn=lambda *a,**kw:None,journal_path=self.jobs)
        self.spec=dict(data_root=str(self.root),source_commit='a'*40,manifest_sha256='b'*64,
                       activation_go=str(self.root/'activation-go.json'),
                       databases={n:gate.identity(self.root/n) for n in gate.SCHEMAS})
        self.sha='c'*64;gate.configure(self.spec,self.sha)
    def tearDown(self):
        gate._binding,gate._spec_sha=self.old;self.tmp.cleanup()
    def go(self,**kw):
        v=dict(schema='localbrain.chat-ocr-activation.v1',spec_sha256=self.sha,manifest_sha256='b'*64,
               source='a'*40,data_root=str(self.root),databases=self.spec['databases'],
               existing_history_preserved=True,loopback_authenticated_endpoints_verified=True,
               verified_utc=dt.datetime.now(dt.timezone.utc).isoformat())
        v.update(kw);Path(self.spec['activation_go']).write_text(json.dumps(v))
    def hashes(self):
        # SQLite read-only WAL access can allocate SHM and an empty WAL; neither
        # is history. Main bytes and any nonempty WAL must remain identical.
        return {str(p.relative_to(self.root)):hashlib.sha256(p.read_bytes()).hexdigest()
                for p in (self.root/'runtime').rglob('*') if p.is_file() and
                not p.name.endswith('-shm') and (not p.name.endswith('-wal') or p.stat().st_size)}
    def test_existing_constructors_do_not_reinitialize_schema_or_history(self):
        before=self.hashes()
        chat.Store(self.store.path)
        gateway.LifecycleController(gateway.CONFIG,health=lambda port:(_ for _ in ()).throw(AssertionError('health called while held')),
                                   audit_fn=lambda *a,**kw:None,journal_path=self.jobs)
        with self.slack.db() as db:self.assertEqual(db.execute('SELECT count(*) FROM runs').fetchone()[0],0)
        self.assertEqual(self.store.messages('other-cookie',self.cid)[1]['content'],'PUBLIC ANSWER')
        self.assertEqual(before,self.hashes())
    def test_missing_database_never_created(self):
        self.jobs.unlink()
        with self.assertRaises((OSError,ValueError)):gate.connect(self.jobs)
        self.assertFalse(self.jobs.exists())
    def test_wrong_schema_held_without_repair(self):
        db=sqlite3.connect(self.jobs)
        try:db.execute('ALTER TABLE jobs ADD COLUMN unexpected TEXT');db.commit()
        finally:db.close()
        before=self.hashes()
        with self.assertRaisesRegex(ValueError,'schema'):gate.validate_schema(self.jobs)
        self.assertEqual(before,self.hashes())
    def test_same_columns_without_primary_key_are_rejected(self):
        db=sqlite3.connect(self.jobs)
        try:
            db.executescript('DROP TABLE jobs;CREATE TABLE jobs(request_id TEXT,role TEXT,state TEXT NOT NULL,updated_at TEXT NOT NULL);')
        finally:db.close()
        with self.assertRaisesRegex(ValueError,'schema'):gate.validate_schema(self.jobs)
    def test_partial_unique_index_cannot_replace_required_unique_constraint(self):
        db=sqlite3.connect(self.slack.path)
        try:
            db.executescript('''DROP TABLE approvals;
            CREATE TABLE approvals(id TEXT PRIMARY KEY,nonce TEXT NOT NULL,request_id TEXT UNIQUE NOT NULL,hash TEXT NOT NULL,result TEXT NOT NULL);
            CREATE UNIQUE INDEX nonce_partial ON approvals(nonce) WHERE 0;''')
        finally:db.close()
        with self.assertRaisesRegex(ValueError,'schema'):gate.validate_schema(self.slack.path)
    def test_malformed_go_remains_closed_and_is_recoverable(self):
        for value in ([],None,3,'wrong'):
            Path(self.spec['activation_go']).write_text(json.dumps(value));self.assertFalse(gate.opened())
        self.go();self.assertTrue(gate.opened())
    def test_identity_rechecked_on_every_connection(self):
        self.go();self.assertTrue(gate.opened())
        self.jobs.rename(self.jobs.with_suffix('.saved'))
        sqlite3.connect(self.jobs).close()
        with self.assertRaisesRegex(ValueError,'identity'):gate.connect(self.jobs)
        self.assertFalse(gate.opened());self.assertEqual(gate.readiness()['status'],'CHAT_OCR_SERVER_NOT_READY')
    def test_held_connection_rejects_writes_but_reads_existing_wal(self):
        # Keep a public writer open so the committed new row remains in WAL.
        writer=sqlite3.connect(self.store.path)
        try:
            writer.execute('INSERT INTO conversations VALUES (?,?,?,?)',('wal-id','lan-shared','WAL ONLY',1));writer.commit()
            db=gate.connect(self.store.path)
            try:
                self.assertEqual(db.execute('SELECT title FROM conversations WHERE id=?',('wal-id',)).fetchone()[0],'WAL ONLY')
                with self.assertRaises(sqlite3.OperationalError):db.execute('DELETE FROM messages')
            finally:db.close()
        finally:writer.close()
    def test_go_binds_source_spec_manifest_root_and_history_identity(self):
        for key,value in [('source','d'*40),('spec_sha256','d'*64),('manifest_sha256','d'*64),
                          ('data_root','other'),('databases',{}),('existing_history_preserved',1),
                          ('loopback_authenticated_endpoints_verified',False),('verified_utc','invalid')]:
            self.go(**{key:value});self.assertFalse(gate.opened(),key)
        self.go();self.assertTrue(gate.opened());self.assertEqual(gate.readiness()['status'],'CHAT_OCR_SERVER_READY')
    def test_success_preserves_identity_and_shared_history_and_survives_new_pid(self):
        self.go()
        before=dict(self.spec['databases']);store=chat.Store(self.store.path)
        store.complete('other-cookie',self.cid,'PUBLIC SECOND QUESTION','PUBLIC SECOND ANSWER',{})
        self.assertEqual(store.messages('third-cookie',self.cid)[-1]['content'],'PUBLIC SECOND ANSWER')
        self.assertEqual(before,{n:gate.identity(self.root/n) for n in gate.SCHEMAS})
        with patch('os.getpid',return_value=987654):self.assertTrue(gate.opened())
    def test_durable_go_rebinds_in_fresh_interpreter_without_mutating_history(self):
        self.go();path=self.root/'public-startup-spec.json';path.write_text(json.dumps(self.spec));before=self.hashes()
        code="import sys,json;from pathlib import Path;sys.path.insert(0,sys.argv[1]);import startup_gate as g;s=json.loads(Path(sys.argv[2]).read_bytes());g.configure(s,sys.argv[3]);[g.validate_schema(Path(s['data_root'])/n) for n in g.SCHEMAS];print(json.dumps(g.readiness()))"
        result=subprocess.run([sys.executable,'-I','-B','-c',code,str(Path(gate.__file__).parent),str(path),self.sha],
                              capture_output=True,text=True,check=True)
        self.assertEqual(json.loads(result.stdout)['status'],'CHAT_OCR_SERVER_READY');self.assertEqual(before,self.hashes())
    def test_required_gate_cannot_fall_back_when_binding_missing(self):
        gate._binding=None
        with patch.dict('os.environ',{'LOCALBRAIN_GATE_REQUIRED':'1'}):
            with self.assertRaisesRegex(ValueError,'not_bound'):gate.opened()
    def test_background_function_waits_until_go(self):
        done=threading.Event();worker=threading.Thread(target=gate.run_after_open,args=(done.set,),daemon=True)
        worker.start();self.assertFalse(done.wait(.3));self.go();self.assertTrue(done.wait(2));worker.join(2)
    def test_existing_running_model_is_passively_ready_after_activation(self):
        calls=[]
        controller=gateway.LifecycleController(gateway.CONFIG,health=lambda port:(calls.append(port) or True),
            run_manager=lambda *a,**kw:(_ for _ in ()).throw(AssertionError('process change')),journal_path=self.jobs)
        controller.refresh_after_activation();self.assertEqual(calls,[])
        self.go();controller.refresh_after_activation();controller.refresh_after_activation()
        self.assertEqual(len(calls),1);self.assertEqual(controller.state,gateway.ModelState.READY)
    def test_chat_all_admission_held_and_metadata_health_has_no_upstream(self):
        app=SimpleNamespace(config=dict(chat.DEFAULTS,allowed_hosts=['public.test']),store=self.store,
                            upstream=SimpleNamespace(request=lambda *a,**kw:(_ for _ in ()).throw(AssertionError('upstream'))))
        server=chat.Server((LOOPBACK,0),chat.Handler);server.app=app
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            for method,path in [('GET','/'),('GET','/api/conversations'),('GET','/api/ocr/config'),('POST','/api/chat'),
                                ('POST','/api/conversations'),('POST','/api/ocr/submit')]:
                conn=http.client.HTTPConnection(*server.server_address,timeout=2)
                conn.request(method,path,body='{}' if method=='POST' else None,headers={'Host':'public.test','X-Chat-Request':'1'})
                response=conn.getresponse();self.assertEqual(response.status,503,(method,path));response.read();conn.close()
            conn=http.client.HTTPConnection(*server.server_address,timeout=2);conn.request('GET','/health',headers={'Host':'public.test'})
            response=conn.getresponse();self.assertEqual(json.loads(response.read())['status'],'ACTIVATION_HELD');conn.close()
        finally:server.shutdown();server.server_close();thread.join(2)
    def test_gateway_routes_are_gated_after_authentication(self):
        for method,path in [('GET','/v1/models'),('GET','/v1/control/status'),('GET','/v1/slack/capabilities'),
                            ('POST','/v1/chat/completions'),('POST','/v1/control/start'),('POST','/v1/slack/apply'),
                            ('POST','/v1/chat-ocr/next'),('POST','/v1/brain/search')]:
            sent=[]
            fake=SimpleNamespace(path=path,allowed=lambda:True,send_json=lambda status,value:sent.append((status,value)))
            getattr(gateway.Handler,'do_'+method)(fake)
            self.assertEqual(sent[0][0],503,(method,path))
        sent=[];fake=SimpleNamespace(path='/health',allowed=lambda:False,send_json=lambda status,value:sent.append(status))
        gateway.Handler.do_GET(fake);self.assertEqual(sent,[403])

if __name__=='__main__':unittest.main()
