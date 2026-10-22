"""Public stubs: no model, production database, credential, or Slack calls."""
import tempfile,unittest,copy,threading,json
from pathlib import Path
from test_slack import controller,request,PIN
from slack_gateway import SlackService,SlackError,RESTRICTION,digest
from site_settings import LOOPBACK
class Model:
 def __init__(self):self.calls=0;self.idle=True;self.fail=True
 def quiescent(self):return self.idle
 def __call__(self,messages,tokens,timeout,lease):
  self.calls+=1
  if self.fail:lease.abort('explicit_cancel');lease.check()
  return 'PUBLIC completed'
class RecoveryTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.model=Model();self.service=SlackService(Path(self.tmp.name),controller(),self.model,LOOPBACK,'fixture-host',dict(enabled=True,host_certificate_sha256=PIN));self.root=request(rid='PUBLIC_ROOT_001');self.root['timeout_seconds']=120;self.root['max_tokens']=64
  self.assertEqual(self.service.execute('chat',self.root)['status'],'interrupted')
 def retry(self,previous=None,next_id='PUBLIC_NEXT_001',logical=None):
  previous=previous or self.root;new=dict(previous,request_id=next_id)
  return dict(logical_id=logical or self.root['request_id'],previous_request_id=previous['request_id'],previous_request_hash=digest(dict(action='chat',value=previous)),request=new,cause='priority_yield_lease',restriction=copy.deepcopy(RESTRICTION))
 def dispatch(self,value):return self.service.dispatch('/v1/slack/retry',value)
 def test_shared_client_contract_hash(self):
  value=json.loads((Path(__file__).parent/'fixtures/slack-recovery-contract.json').read_text(encoding='utf8'))
  self.assertEqual(digest(dict(action='chat',value=value['request'])),value['request_hash'])
 def test_quiescence_required_before_new_reservation_or_model(self):
  self.model.idle=False;r=self.retry();self.assertEqual(self.dispatch(r),dict(status='deferred',retry_safe=True));self.assertEqual(self.model.calls,1)
  with self.service.store.db() as db:self.assertEqual(db.execute('SELECT count(*) FROM runs').fetchone()[0],1);self.assertEqual(db.execute('SELECT count(*) FROM retry_links').fetchone()[0],0)
 def test_bounded_chain_and_durable_idempotency(self):
  first=self.retry();self.assertEqual(self.dispatch(first)['status'],'interrupted');second=self.retry(first['request'],'PUBLIC_NEXT_002');self.assertEqual(self.dispatch(second)['status'],'interrupted')
  with self.assertRaises(SlackError):self.dispatch(self.retry(second['request'],'PUBLIC_NEXT_003'))
  self.assertEqual(self.model.calls,3)
  self.assertEqual(self.dispatch(first)['status'],'interrupted');self.assertEqual(self.model.calls,3)
 def test_successful_retry_and_duplicate_returns_same_result(self):
  self.model.fail=False;r=self.retry();result=self.dispatch(r);self.assertEqual(result['status'],'complete');self.assertEqual(self.dispatch(r),result);self.assertEqual(self.model.calls,2)
 def test_changed_body_and_unbound_root_denied(self):
  r=self.retry();r['request']['prompt']='CHANGED'
  with self.assertRaises(SlackError):self.dispatch(r)
  self.assertEqual(self.model.calls,1)
  self.dispatch(self.retry());r=self.retry(self.retry()['request'],'PUBLIC_NEXT_002',logical='FOREIGN_ROOT_001')
  with self.assertRaises(SlackError):self.dispatch(r)
  self.assertEqual(self.model.calls,2)
 def test_timeout_is_terminal_not_priority_retry(self):
  with self.service.store.db() as db:db.execute('UPDATE runs SET result=? WHERE id=?',('{"status":"interrupted","reason":"deadline_exceeded"}',self.root['request_id']))
  with self.assertRaises(SlackError):self.dispatch(self.retry())
  self.assertEqual(self.model.calls,1)
 def test_missing_probe_is_not_idle(self):
  self.service.model=lambda *args:'MUST NOT RUN';self.assertEqual(self.dispatch(self.retry())['status'],'deferred')
 def test_competing_attempt_waits_without_second_generation(self):
  entered=threading.Event();release=threading.Event();errors=[]
  class Blocking(Model):
   def __call__(inner,*args):inner.calls+=1;entered.set();release.wait(3);return 'PUBLIC completed'
  model=Blocking();self.service.model=model;result=[]
  thread=threading.Thread(target=lambda:result.append(self.dispatch(self.retry())),daemon=True);thread.start();self.assertTrue(entered.wait(2))
  try:self.assertEqual(self.dispatch(self.retry(next_id='PUBLIC_COMPETITOR_001'))['status'],'deferred');self.assertEqual(model.calls,1)
  finally:release.set();thread.join(3)
  self.assertFalse(thread.is_alive());self.assertEqual(result[0]['status'],'complete')
if __name__=='__main__':unittest.main()
