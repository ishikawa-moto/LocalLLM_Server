from site_settings import LOOPBACK
import importlib.util
import pathlib
import tempfile
import threading
import time
import unittest
import http.client
import json

SRC=pathlib.Path(__file__).resolve().parents[1]/'src/chat_service.py'
spec=importlib.util.spec_from_file_location('chat_service',SRC)
chat=importlib.util.module_from_spec(spec);spec.loader.exec_module(chat)

class Fake:
    def __init__(self):
        self.calls=[];self.active=0;self.peak=0;self.delay=.02;self.fail_brain=False
    def request(self,target,path,value=None,timeout=10):
        self.calls.append((target,path,value))
        if path=='/health':return {'model_loaded':True,'brain':True,'state':'READY'}
        if path=='/search':
            if self.fail_brain:raise OSError()
            return dict(mode='hybrid',results=[dict(text='reference fact',path='facts/a.md',revision='123',hybrid_score=.03)])
        return {}
    def tokens(self,text):return len(text)
    def count(self,messages):return sum(len(m['content'])+10 for m in messages)
    def generate(self,job,messages):
        self.active+=1;self.peak=max(self.peak,self.active)
        try:
            for _ in range(3):
                time.sleep(self.delay);job.check();yield 'hello'
        finally:self.active-=1
    def cancel(self,rid):pass

class ChatTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.tmp.name)
        self.fake=Fake();self.app=chat.App(self.root,chat.DEFAULTS.copy(),self.fake)
    def tearDown(self):
        self.app.stopping.set();self.app.worker.join(2);self.tmp.cleanup()
    def wait(self,job):
        end=time.monotonic()+3
        while not job.done and time.monotonic()<end:time.sleep(.01)
        self.assertTrue(job.done)
    def submit(self,owner='a',brain=False):
        cid=self.app.store.create(owner)
        return self.app.submit(owner,dict(conversation_id=cid,message='hello',secondbrain=brain))
    def test_history_and_jobs_shared_across_browsers(self):
        j=self.submit();self.wait(j)
        self.assertEqual(self.app.store.listing('b'),self.app.store.listing('a'))
        self.assertEqual(len(self.app.store.messages('b',j.cid)),2)
        self.assertIs(self.app.get_job('b',j.id),j)
    def test_legacy_cookie_histories_visible_and_deletable(self):
        with self.app.store.db() as db:
            db.executemany('INSERT INTO conversations VALUES (?,?,?,?)',
                [('old-a','cookie-hash-a','legacy A',1),('old-b','cookie-hash-b','legacy B',2)])
            db.execute('INSERT INTO messages(conversation,role,content,metadata) VALUES (?,?,?,?)',('old-a','user','legacy text','{}'))
        self.assertEqual(len(self.app.store.listing('new-browser')),2)
        self.assertEqual(self.app.store.messages('new-browser','old-a')[0]['content'],'legacy text')
        self.app.delete_conversation('new-browser','old-a')
        self.assertEqual([c['id'] for c in self.app.store.listing('another-browser')],['old-b'])
        with self.app.store.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM messages').fetchone()[0],0)
    def test_delete_only_selected_owned_conversation_and_completed_jobs(self):
        a=self.submit();b=self.submit();self.wait(a);self.wait(b)
        self.app.delete_conversation('other-browser',a.cid)
        with self.assertRaises(chat.ChatError):self.app.store.messages('a',a.cid)
        with self.assertRaises(chat.ChatError):self.app.get_job('a',a.id)
        with self.app.store.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM messages WHERE conversation=?',(a.cid,)).fetchone()[0],0)
        self.assertEqual(len(self.app.store.messages('a',b.cid)),2)
        with self.assertRaises(chat.ChatError):self.app.submit('a',dict(conversation_id=a.cid,message='cannot recreate'))
    def test_delete_rejects_active_conversation(self):
        self.fake.delay=.1;a=self.submit()
        with self.assertRaises(chat.ChatError) as ctx:self.app.delete_conversation('a',a.cid)
        self.assertEqual(ctx.exception.status,409)
        self.wait(a)
    def test_queue_serializes_and_history_separate(self):
        a=self.submit('a');b=self.submit('b');self.wait(a);self.wait(b)
        self.assertEqual(self.fake.peak,1)
        self.assertEqual(len(self.app.store.messages('a',a.cid)),2)
        self.assertFalse(any(c[1]=='/search' for c in self.fake.calls))
    def test_retrieval_read_only_and_fallback(self):
        a=self.submit(brain=True);self.wait(a)
        self.assertTrue(a.metadata['secondbrain_used'])
        self.assertEqual([x[1] for x in self.fake.calls if x[0]=='brain'],['/search'])
        self.fake.fail_brain=True;b=self.submit(brain=True);self.wait(b)
        self.assertEqual(b.events[-1]['status'],'completed')
        self.assertFalse(b.metadata['secondbrain_used'])
        self.assertTrue(any(x['type']=='warning' for x in b.events))
    def test_cancel_no_partial_history(self):
        self.fake.delay=.15;a=self.submit();time.sleep(.05);self.app.cancel(a);self.wait(a)
        self.assertEqual(self.app.store.messages('a',a.cid),[])
        self.assertEqual(a.events[-1]['status'],'Cancelled')
    def test_same_conversation_busy(self):
        self.fake.delay=.1;a=self.submit()
        with self.assertRaises(chat.ChatError):self.app.submit('a',dict(conversation_id=a.cid,message='next'))
        self.wait(a)
    def test_context_overflow_and_history_trim(self):
        cfg=chat.DEFAULTS|dict(context_tokens=600,output_tokens=100)
        with self.assertRaises(chat.ChatError):chat.build_context(self.fake,cfg,[],'x'*1000,[])
        history=[{'role':'user','content':'u'*300},{'role':'assistant','content':'a'*300}]
        messages,meta=chat.build_context(self.fake,cfg,history,'ok',[])
        self.assertEqual(meta['history_messages'],0);self.assertLessEqual(meta['context_tokens'],500)
        self.assertEqual(messages[-1]['role'],'user')
    def test_retrieval_budget_and_score(self):
        cfg=chat.DEFAULTS|dict(max_retrieval_tokens=100,score_threshold=.02)
        _,meta=chat.build_context(self.fake,cfg,[],'ok',[dict(text='x'*1000,hybrid_score=.1),dict(text='x',hybrid_score=.01)])
        self.assertEqual(meta['retrieval_count'],0)
    def test_http_shared_history_csrf_and_host(self):
        server=chat.Server((LOOPBACK,0),chat.Handler);server.app=self.app
        port=server.server_address[1];self.app.config['allowed_hosts']=[f'{LOOPBACK}:{port}']
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def req(path,method='GET',data=None,headers=None):
            c=http.client.HTTPConnection(LOOPBACK,port,timeout=3)
            c.request(method,path,None if data is None else json.dumps(data),headers or {})
            r=c.getresponse();status=r.status;cookie=r.getheader('Set-Cookie');body=r.read();c.close();return status,cookie,json.loads(body)
        try:
            _,cookie,_=req('/api/conversations');cookie=cookie.split(';')[0]
            self.assertEqual(req('/api/conversations','POST',{}, {'Cookie':cookie})[0],403)
            self.assertEqual(req('/api/conversations','POST',{}, {'Cookie':cookie,'X-Chat-Request':'1','Origin':'https://evil.test'})[0],403)
            code,_,data=req('/api/conversations','POST',{}, {'Cookie':cookie,'X-Chat-Request':'1'})
            self.assertEqual(code,201)
            self.assertEqual(req('/api/conversations/'+data['id'])[0],200)
            self.assertEqual(req('/api/conversations/delete','POST',{'conversation_id':data['id']},{'Cookie':cookie,'X-Chat-Request':'1'})[0],400)
            self.assertEqual(req('/api/conversations/delete','POST',{'conversation_id':data['id'],'confirm':True},{'Cookie':'lb_chat=different-browser','X-Chat-Request':'1'})[0],200)
            self.assertEqual(req('/api/conversations/delete','POST',{'conversation_id':data['id'],'confirm':True},{'Cookie':cookie,'X-Chat-Request':'1'})[0],404)
            self.assertEqual(req('/api/conversations/'+data['id'],headers={'Cookie':cookie})[0],404)
            self.assertEqual(req('/api/health',headers={'Host':'evil.test'})[0],403)
        finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
