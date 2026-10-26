"""Public fixtures only: no Slack accounts, production keys, models or services."""
import copy
import contextlib
import hashlib
import http.client
import json
from pathlib import Path
import tempfile
import sqlite3
import threading
import time
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import control_gateway as gateway
from slack_gateway import (SlackService, SlackError, Deferred, RESTRICTION, CLASSIFICATION,
                           LoopbackModel, Lease, CancellableConnection, canonical, digest, strict_json,
                           contains_restriction)
from slack_web import approval_entry
from site_settings import LOOPBACK

CERT = b'public-test-host-certificate'
PIN = hashlib.sha256(CERT).hexdigest()
SOURCE = 'The public fixture uses a blue button.'

def request(kind='chat', rid='request_fixture_0001'):
    v = dict(request_id=rid, session_id='session_fixture_01', max_tokens=1024, timeout_seconds=5,
             restriction=copy.deepcopy(RESTRICTION))
    if kind == 'chat': v.update(context='', prompt=SOURCE)
    else: v['source'] = SOURCE
    return v

def controller(ready=True):
    return gateway.LifecycleController(gateway.GatewayConfig(),
        run_manager=lambda *a,**kw: (_ for _ in ()).throw(AssertionError('model startup forbidden')),
        health=lambda p: ready, base_ready=lambda: None, audit_fn=lambda *a,**kw:None)

class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.calls = []
        def model(messages, tokens, timeout, lease):
            self.calls.append((messages, tokens, timeout))
            return 'Public fixture answer.'
        self.c = controller()
        self.s = SlackService(self.root, self.c, model, LOOPBACK, 'ClientPC-windows-host',
                              dict(enabled=True, host_certificate_sha256=PIN, human_apply_enabled=False), clock=lambda:1000)
    def tearDown(self): self.tmp.cleanup()
    def dispatch(self, action, value): return self.s.dispatch('/v1/slack/'+action, value)
    def review(self):
        # JSON keyword pass is explicit, no claim that a fake model is qualification.
        def model(messages, *args):
            self.calls.append(messages)
            if len(self.calls) % 2: return json.dumps(dict(kind='knowledge', body=SOURCE, evidence='blue button'))
            return '{"pass":true,"evidence":"blue button","issues":[]}'
        self.s.model = model
        return self.dispatch('review', request('review'))
    def proposed(self):
        review = self.review()
        self.assertEqual(review['status'], 'complete')
        value = dict(proposal_id='proposal_fixture_01', review_request_id='request_fixture_0001',
                     session_id='session_fixture_01', restriction=copy.deepcopy(RESTRICTION))
        proposal = self.dispatch('proposal', value)
        return proposal
    def approval(self):
        proposal = self.proposed()
        a = dict(schema='localbrain.slack-host-approval.v1', approval_id='approval_fixture_01', nonce='nonce_fixture_0001',
                 approver_id='admin_fixture_001', action='accept_restricted', proposal_id='proposal_fixture_01',
                 proposal_hash=proposal['proposal_hash'], review_hash=proposal['payload']['review_hash'],
                 session_id='session_fixture_01', issued_at=990, expires_at=1100,
                 host_owner='ClientPC-windows-host', host_certificate_sha256=PIN,
                 target=proposal['payload']['target'], base_hash=proposal['payload']['base_hash'], new_hash=proposal['payload']['new_hash'])
        a['receipt_hash'] = digest(a)
        return dict(request_id='apply_fixture_0001', proposal_id='proposal_fixture_01', proposal_hash=proposal['proposal_hash'],
                    session_id='session_fixture_01', approval=a, restriction=copy.deepcopy(RESTRICTION))
    def test_host_ip_owner_and_pinned_certificate_required(self):
        self.s.authenticate(LOOPBACK, CERT, 'ClientPC-windows-host')
        for ip, cert, owner in [('other-host', CERT, 'ClientPC-windows-host'), (LOOPBACK,b'other','ClientPC-windows-host'),
                                (LOOPBACK,CERT,'Slack-member'), (LOOPBACK,b'','ClientPC-windows-host')]:
            with self.subTest(ip=ip,owner=owner), self.assertRaises(SlackError): self.s.authenticate(ip,cert,owner)
    def test_disabled_capabilities_and_no_store_created(self):
        self.s.policy['enabled'] = False
        self.assertFalse(self.s.capabilities()['chat'])
        with self.assertRaises(SlackError): self.dispatch('chat', request())
        self.assertFalse((self.root/'runtime').exists())
    def test_missing_or_invalid_policy_is_disabled(self):
        s = SlackService.from_root(self.root, self.c, self.s.model, LOOPBACK, 'ClientPC-windows-host')
        self.assertFalse(s.capabilities()['enabled'])
        (self.root/'config').mkdir()
        (self.root/'config/slack-server.json').write_text('{"enabled":true}')
        self.assertFalse(SlackService.from_root(self.root,self.c,self.s.model,LOOPBACK,'x').capabilities()['enabled'])
    def test_complete_chat_local_only_and_hard_budgets(self):
        v=request(); v.update(max_tokens=2048, timeout_seconds=120)
        result=self.dispatch('chat',v)
        self.assertEqual(result['classification'], CLASSIFICATION)
        self.assertEqual(self.calls[0][1],1024)
        self.assertLessEqual(self.calls[0][2],120)
        self.assertGreater(self.calls[0][2],115)
        self.assertEqual(len(self.calls[0][0]),2)
    def test_request_is_idempotent_and_changed_body_conflicts(self):
        v=request(); self.assertEqual(self.dispatch('chat',v),self.dispatch('chat',v)); self.assertEqual(len(self.calls),1)
        v['prompt']='changed'
        with self.assertRaises(SlackError): self.dispatch('chat',v)
    def test_utf8_source_budget_includes_context_separator(self):
        value=request();value['context']='[]';value['prompt']='x'*15997
        self.assertEqual(self.dispatch('chat',value)['status'],'complete')
        value=request(rid='request_byte_boundary_02');value['context']='[]';value['prompt']='x'*15998
        with self.assertRaises(SlackError) as error:self.dispatch('chat',value)
        self.assertEqual(error.exception.code,'input_budget_exceeded')
        self.assertEqual(len(self.calls),1)
    def test_resource_limit_blocks_fresh_inference_but_allows_cached_and_non_inference(self):
        value=request()
        result=self.dispatch('chat',value)
        checks=[]
        self.s.resource_status=lambda: checks.append(True) or {'accepting_jobs':False}
        deferred=self.dispatch('chat',request(rid='request_resource_limit_01'))
        self.assertEqual(deferred,dict(status='deferred',retry_safe=True,reason='resource_limit_exceeded'))
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.dispatch('chat',value),result)
        status=dict(operation='chat',request_id=value['request_id'],session_id=value['session_id'],
                    request_hash=digest(dict(action='chat',value=value)),restriction=copy.deepcopy(RESTRICTION))
        self.assertEqual(self.dispatch('status',status)['state'],'complete')
        cancel=dict(request_id=value['request_id'],session_id=value['session_id'],restriction=copy.deepcopy(RESTRICTION))
        self.assertFalse(self.dispatch('cancel',cancel)['cancelled'])
        self.assertEqual(len(checks),1)
        self.s.resource_status=lambda: {'accepting_jobs':True}
        fresh=request(rid='request_resource_limit_01')
        resumed=self.dispatch('chat',fresh)
        self.assertEqual(resumed['status'],'complete')
        self.assertEqual(self.dispatch('chat',fresh),resumed)
        self.assertEqual(len(self.calls),2)  # only the resumed call inferred, then exact replay
    def test_malformed_or_weakened_restriction_rejected(self):
        for key,replacement in [('cloudFallback',True),('cloudFallback',0),('origin','web'),('externalAi','allow')]:
            v=request(); v['restriction'][key]=replacement
            with self.subTest(key=key,value=replacement),self.assertRaises(SlackError): self.dispatch('chat',v)
        for key,value in [('tools',[]),('url','https://example.invalid'),('max_tokens',True),('timeout_seconds',0),('prompt','x'*16001)]:
            v=request();v[key]=value
            with self.subTest(key=key),self.assertRaises(SlackError):self.dispatch('chat',v)
        self.assertFalse(self.calls)
    def test_two_fresh_review_passes_and_source_binding(self):
        result=self.review()
        self.assertIsNotNone(result['candidate']);self.assertEqual(len(self.calls),2)
        self.assertTrue(all(len(m)==1 and m[0]['role']=='user' for m in self.calls))
        self.assertNotIn('Propose one durable knowledge item or safe Slack response rule', self.calls[1][0]['content'])
        self.assertEqual(result['review_hash'],digest(result['review']))
        self.assertEqual(result['review']['source_sha256'],hashlib.sha256(SOURCE.encode()).hexdigest())
    def test_missing_evidence_or_bad_verdict_produces_no_candidate(self):
        self.s.model=lambda *args:'{"kind":"knowledge","body":"unsupported","evidence":"not in source"}'
        self.assertIsNone(self.dispatch('review',request('review'))['candidate'])
    def test_busy_is_deferred_without_reserved_request(self):
        with self.c.job('normal_fixture_0001'):
            self.assertEqual(self.dispatch('chat',request()),dict(status='deferred',retry_safe=True,reason='foreground_busy'))
        self.assertEqual(self.dispatch('chat',request())['status'],'complete')
    def test_interrupted_generation_is_never_silently_retried(self):
        self.s.model=lambda *a: (_ for _ in ()).throw(ConnectionError('secret source must not appear'))
        result=self.dispatch('chat',request())
        self.assertEqual((result['status'],result['retry_safe'],result['reason']),('interrupted',False,'upstream_failure'))
        self.s.model=lambda *a: self.fail('must not retry')
        self.assertEqual(self.dispatch('chat',request()),result)
    def test_restart_with_active_reservation_returns_unresolved(self):
        v=request();self.s.store.reserve(v['request_id'],digest(dict(action='chat',value=v)),v['session_id'])
        self.assertEqual(self.dispatch('chat',v),dict(status='unresolved',retry_safe=False))
    def test_proposal_requires_same_session_and_completed_review(self):
        proposal=self.proposed()
        self.assertEqual(proposal['payload']['classification'],CLASSIFICATION)
        self.assertFalse((self.root/'SecondBrain').exists())
        self.assertFalse((self.root/'exports').exists())
        value=dict(proposal_id='proposal_fixture_02',review_request_id='request_fixture_0001',session_id='other_session_0001',restriction=RESTRICTION)
        with self.assertRaises(SlackError):self.dispatch('proposal',value)
    def test_human_apply_defaults_to_disabled(self):
        v=self.approval()
        with self.assertRaises(SlackError):self.dispatch('apply',v)
    def test_exact_host_approval_is_atomic_and_idempotent(self):
        v=self.approval();self.s.policy['human_apply_enabled']=True
        a=self.dispatch('apply',v)
        self.assertEqual(a['status'],'applied_restricted')
        self.assertEqual(self.dispatch('apply',v),a)
        with self.s.store.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM accepted').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM approvals').fetchone()[0],1)
    def test_changed_approval_receipt_expiry_and_nonce_rejected(self):
        v=self.approval();self.s.policy['human_apply_enabled']=True
        for key,replacement in [('proposal_hash','0'*64),('session_id','other_session_0001'),('host_owner','Slack-member'),('expires_at',999),('issued_at',True)]:
            changed=copy.deepcopy(v); changed['approval'][key]=replacement
            a=changed['approval'];a['receipt_hash']=digest({k:x for k,x in a.items() if k!='receipt_hash'})
            with self.subTest(key=key),self.assertRaises(SlackError):self.dispatch('apply',changed)
        self.dispatch('apply',v)
        changed=copy.deepcopy(v);changed['request_id']='apply_fixture_0002'
        with self.assertRaises(SlackError):self.dispatch('apply',changed)
    def test_member_assertion_is_not_an_approval(self):
        v=self.approval();self.s.policy['human_apply_enabled']=True
        v['approval']={'approved':True,'member_id':'member_fixture_0001'}
        with self.assertRaises(SlackError):self.dispatch('apply',v)
    def test_duplicate_json_and_nonfinite_rejected(self):
        for raw in ('{"a":1,"a":2}','{"a":NaN}'):
            with self.assertRaises(ValueError):strict_json(raw)
    def test_classification_recognized_in_nested_ordinary_envelopes(self):
        self.assertTrue(contains_restriction({'payload':[{'data_class':'slack_restricted'}]}))
        self.assertFalse(contains_restriction({'prompt':'public fixture'}))
    def test_non_loopback_model_target_rejected(self):
        with self.assertRaises(SlackError):LoopbackModel('example.invalid',1,lambda:'fixture')

class PriorityTests(unittest.TestCase):
    def test_cold_background_deferred_never_starts_model(self):
        c=controller(False)
        with self.assertRaises(Deferred):
            with c.background_job(4):self.fail('admitted cold')
    def test_chat_preempts_review_then_defers_until_release(self):
        c=controller()
        with c.background_job(5) as lease:
            with self.assertRaises(Deferred):
                with c.background_job(4):pass
            self.assertTrue(lease.cancelled.is_set())
            # Expected cancellation is propagated on scope exit.
            lease.cancelled.clear()
        with c.background_job(4):pass
    def test_high_request_blocks_new_background_and_preempts_existing(self):
        c=controller();started=threading.Event();errors=[]
        def low():
            try:
                with c.background_job(5) as lease:
                    started.set();lease.cancelled.wait(2);lease.check()
            except InterruptedError:pass
            except Exception as e:errors.append(e)
        thread=threading.Thread(target=low);thread.start();self.assertTrue(started.wait(1))
        with c.job('normal_fixture_0001'):
            with self.assertRaises(Deferred):
                with c.background_job(4):pass
            self.assertEqual(c.snapshot()['active_jobs'],1)
        thread.join(2);self.assertFalse(thread.is_alive());self.assertFalse(errors)
        self.assertEqual(c.snapshot()['queued_jobs'],0)
        self.assertIsNone(c.snapshot()['active_task'])
    def test_no_slack_ids_enter_normal_journal_or_audit(self):
        with tempfile.TemporaryDirectory() as td:
            events=[];c=controller();c.audit=lambda *a,**kw:events.append((a,kw))
            c.journal_path=Path(td)/'normal.sqlite3'
            # A background scope never opens the ordinary request journal.
            with c.background_job(4):self.assertEqual(c.snapshot()['active_task'],{'role':'restricted-background'})
            self.assertFalse(c.journal_path.exists());self.assertFalse(events)
    def test_cancel_before_connect_cannot_reconnect(self):
        lease=Lease(4);lease.abort();conn=CancellableConnection(LOOPBACK,1,1,lease)
        with self.assertRaises(InterruptedError):conn.connect()

class TransportTests(unittest.TestCase):
    def test_preemption_interrupts_blocked_stream_and_releases_shared_slot(self):
        started=threading.Event();disconnected=threading.Event()
        class Origin(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
                self.wfile.write(b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n');self.wfile.flush();started.set()
                self.connection.settimeout(3)
                try:
                    if self.connection.recv(1)==b'':disconnected.set()
                except OSError:disconnected.set()
        server=ThreadingHTTPServer((LOOPBACK,0),Origin);server.daemon_threads=True
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        with tempfile.TemporaryDirectory() as td:
            c=controller();s=SlackService(Path(td),c,LoopbackModel(LOOPBACK,server.server_port,lambda:'public-fixture-key'),LOOPBACK,'host',dict(enabled=True))
            results=[];low=threading.Thread(target=lambda:results.append(s.dispatch('/v1/slack/chat',request())))
            try:
                low.start();self.assertTrue(started.wait(2));begin=time.monotonic()
                with c.job('foreground_fixture_01'):pass
                low.join(2);self.assertFalse(low.is_alive());self.assertLess(time.monotonic()-begin,2)
                self.assertEqual(len(results),1)
                self.assertEqual((results[0]['status'],results[0]['retry_safe'],results[0]['reason']),('interrupted',False,'foreground_preempted'))
                self.assertTrue(disconnected.wait(2))
            finally:low.join(4);server.shutdown();server.server_close();thread.join(2)

class MenuTests(unittest.TestCase):
    def test_only_exact_configured_client_https_entry_enabled(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'config').mkdir()
            (root/'config/private-site.json').write_text('{"client_address":"localbrain-client"}')
            p=root/'config/slack-web.json'
            self.assertEqual(approval_entry(root),{'enabled':False})
            for url in ('http://localbrain-client/slack/approvals','https://other-host/slack/approvals',
                        'https://user@localbrain-client/slack/approvals','https://localbrain-client/slack/approvals?token=fixture',
                        'https://localbrain-client/slack/approvals#secret','https://localbrain-client/redirect'):
                p.write_text(json.dumps(dict(enabled=True,approval_url=url)))
                self.assertFalse(approval_entry(root)['enabled'])
            p.write_text(json.dumps(dict(enabled=True,approval_url='https://localbrain-client/slack/approvals')))
            self.assertTrue(approval_entry(root)['enabled'])


class HTTPBoundaryTests(unittest.TestCase):
    def test_host_routes_framing_and_ordinary_route_egress(self):
        class CertSocket:
            def __init__(self,sock):self.sock=sock
            def __getattr__(self,key):return getattr(self.sock,key)
            def getpeercert(self,binary_form=False):return CERT if binary_form else {'fixture':True}
        class FixtureHandler(gateway.Handler):
            def setup(self):
                self.request=CertSocket(self.request)
                super().setup()
            def log_message(self,*args):pass
        with tempfile.TemporaryDirectory() as td:
            c=controller();s=SlackService(Path(td),c,lambda *a:'fixture answer',LOOPBACK,'ClientPC-windows-host',
                    dict(enabled=True,host_certificate_sha256=PIN,human_apply_enabled=False))
            server=ThreadingHTTPServer((LOOPBACK,0),FixtureHandler);server.slack_service=s
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            def call(method,path,body=None,owner=True,extra=None):
                conn=http.client.HTTPConnection(LOOPBACK,server.server_port,timeout=3)
                headers={'Content-Type':'application/json'}
                if owner:headers['X-LocalBrain-Approval-Owner']='ClientPC-windows-host'
                headers.update(extra or {})
                try:
                    conn.request(method,path,canonical(body) if body is not None else None,headers)
                    response=conn.getresponse();return response.status,json.loads(response.read())
                finally:conn.close()
            try:
                config=gateway.GatewayConfig(allowed_ips=(LOOPBACK,))
                with patch.object(gateway,'CONFIG',config),patch.object(gateway,'resource_status',return_value={'accepting_jobs':True}):
                    self.assertEqual(call('GET','/v1/slack/capabilities')[0],200)
                    self.assertEqual(call('POST','/v1/slack/chat',request())[1]['status'],'complete')
                    with patch.object(gateway,'CONTROLLER',c,create=True), \
                         patch.object(gateway,'healthy',return_value=False), \
                         patch.object(gateway,'listening',return_value=True):
                        status_code,observed=call('GET','/v1/control/status')
                    self.assertEqual(status_code,200)
                    self.assertTrue(observed['restricted_activity']['quiescent'])
                    self.assertFalse(observed['restricted_activity']['admission_fenced'])
                    self.assertEqual(call('POST','/v1/slack/chat',request(),owner=False)[0],403)
                    self.assertEqual(call('POST','/v1/slack/chat?unexpected=1',request())[0],404)
                    self.assertEqual(call('POST','/v1/chat/completions',{'messages':[], 'restriction':RESTRICTION})[0],403)
                    self.assertEqual(call('POST','/v1/brain/propose',{'body':'fixture'},extra={'X-LocalBrain-Data-Class':'slack_restricted'})[0],403)
                    conn=http.client.HTTPConnection(LOOPBACK,server.server_port,timeout=3)
                    conn.putrequest('POST','/v1/slack/chat');conn.putheader('X-LocalBrain-Approval-Owner','ClientPC-windows-host')
                    conn.putheader('Content-Length','2');conn.putheader('Content-Length','2');conn.endheaders(b'{}')
                    response=conn.getresponse();self.assertEqual(response.status,400);response.read();conn.close()
            finally:server.shutdown();server.server_close();thread.join(2)

class CancellationTests(unittest.TestCase):
    def test_explicit_host_cancel_discards_output_and_binds_session(self):
        started=threading.Event();results=[]
        def model(messages,tokens,timeout,lease):
            started.set();lease.cancelled.wait(2);lease.check();return 'must be discarded'
        with tempfile.TemporaryDirectory() as td:
            s=SlackService(Path(td),controller(),model,LOOPBACK,'host',dict(enabled=True))
            thread=threading.Thread(target=lambda:results.append(s.dispatch('/v1/slack/chat',request())))
            thread.start();self.assertTrue(started.wait(1))
            cancel=dict(request_id='request_fixture_0001',session_id='other_session_01',restriction=RESTRICTION)
            self.assertFalse(s.dispatch('/v1/slack/cancel',cancel)['cancelled'])
            cancel['session_id']='session_fixture_01'
            self.assertTrue(s.dispatch('/v1/slack/cancel',cancel)['cancelled'])
            thread.join(2);self.assertFalse(thread.is_alive())
            self.assertEqual(len(results),1)
            self.assertEqual((results[0]['status'],results[0]['retry_safe'],results[0]['reason']),('interrupted',False,'explicit_cancel'))
    def test_many_background_arrivals_never_overlap(self):
        c=controller();barrier=threading.Barrier(9);release=threading.Event();entered=[];failures=[]
        def work(index):
            barrier.wait()
            try:
                with c.background_job(4):entered.append(index);release.wait(1)
            except Deferred:pass
            except Exception as e:failures.append(e)
        threads=[threading.Thread(target=work,args=(i,)) for i in range(8)]
        for t in threads:t.start()
        barrier.wait()
        time.sleep(.1);self.assertEqual(len(entered),1);release.set()
        for t in threads:t.join(2)
        self.assertFalse(failures)
        self.assertTrue(all(not t.is_alive() for t in threads))


class SafeRuleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.s=SlackService(self.root,controller(),None,LOOPBACK,'ClientPC-windows-host',
            dict(enabled=True,host_certificate_sha256=PIN,human_apply_enabled=False),clock=lambda:1000)
    def tearDown(self):self.tmp.cleanup()
    def make_review(self,delta,rid):
        outputs=iter([json.dumps(dict(kind='rule',body='Use the stated public response preference.',evidence='brief',delta=delta)),
                      '{"pass":true,"evidence":"brief","issues":[]}'])
        self.s.model=lambda *a:next(outputs)
        v=request('review',rid);v['source']='Use brief replies in en locale.'
        return self.s.dispatch('/v1/slack/review',v)
    def proposal(self,rid='request_rule_0001'):
        self.assertIsNotNone(self.make_review({'style':'brief'},rid)['candidate'])
        return self.s.dispatch('/v1/slack/proposal',dict(proposal_id=rid.replace('request','proposal'),review_request_id=rid,
            session_id='session_fixture_01',restriction=RESTRICTION))
    def approval(self,p,suffix='0001'):
        payload=p['payload']
        a=dict(schema='localbrain.slack-host-approval.v1',approval_id='approval_rule_'+suffix,nonce='nonce_rule_'+suffix,
               approver_id='admin_fixture_001',action='accept_restricted',proposal_id=payload['proposal_id'],proposal_hash=p['proposal_hash'],
               review_hash=payload['review_hash'],session_id=payload['session_id'],issued_at=990,expires_at=1100,
               host_owner='ClientPC-windows-host',host_certificate_sha256=PIN,
               target=payload['target'],base_hash=payload['base_hash'],new_hash=payload['new_hash'])
        a['receipt_hash']=digest(a)
        return dict(request_id='apply_rule_'+suffix,proposal_id=payload['proposal_id'],proposal_hash=p['proposal_hash'],
                    session_id=payload['session_id'],approval=a,restriction=RESTRICTION)
    def test_only_enumerated_slack_rule_delta_is_accepted(self):
        for index,delta in enumerate(({'shell':'cmd'},{'auth':'off'},{'path':'outside'},{'external_ai':'allow'},
                                      {'style':'run tools'},{'locale':'../../outside'},{})):
            with self.subTest(delta=delta):
                self.assertIsNone(self.make_review(delta,'request_bad_rule_'+str(index))['candidate'])
    def test_rule_proposal_displays_exact_diff_and_hashes_without_applying(self):
        p=self.proposal();payload=p['payload'];diff=payload['rule_diff']
        self.assertEqual(payload['target'],'slack-response-policy')
        self.assertEqual(diff,dict(before={'locale':'ja','style':'normal'},delta={'style':'brief'},after={'locale':'ja','style':'brief'}))
        self.assertEqual(payload['new_hash'],digest(self.s.rule_payload(diff['after'])))
        with self.s.store.db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM rule_state').fetchone()[0],0)
        self.assertFalse(self.s.capabilities()['rule_activation'])
    def test_human_rule_apply_changes_only_restricted_chat_policy(self):
        p=self.proposal();v=self.approval(p)
        with self.assertRaises(SlackError):self.s.dispatch('/v1/slack/apply',v)
        self.s.policy['human_apply_enabled']=True
        result=self.s.dispatch('/v1/slack/apply',v)
        self.assertEqual(result['target'],'slack-response-policy')
        self.assertEqual(self.s.dispatch('/v1/slack/apply',v),result)
        messages=[]
        def model(m,*a):messages.extend(m);return 'Public fixture reply.'
        self.s.model=model
        self.s.dispatch('/v1/slack/chat',request(rid='request_after_rule_01'))
        self.assertIn('Response style: brief.',messages[0]['content'])
        self.assertFalse((self.root/'config').exists());self.assertFalse((self.root/'SecondBrain').exists())
        self.assertTrue(all(p.is_relative_to(self.root/'runtime/slack-restricted') for p in self.root.rglob('*') if p.is_file()))
    def test_target_base_and_new_hash_must_match_human_receipt(self):
        v=self.approval(self.proposal());self.s.policy['human_apply_enabled']=True
        for key,bad in [('target','ordinary-actor-rules'),('base_hash','0'*64),('new_hash','0'*64)]:
            changed=copy.deepcopy(v);a=changed['approval'];a[key]=bad
            a['receipt_hash']=digest({k:x for k,x in a.items() if k!='receipt_hash'})
            with self.subTest(key=key),self.assertRaises(SlackError):self.s.dispatch('/v1/slack/apply',changed)
        with self.s.store.db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM rule_state').fetchone()[0],0)
    def test_concurrent_proposals_cannot_overwrite_changed_base(self):
        first=self.proposal();second=self.proposal('request_rule_0002');self.s.policy['human_apply_enabled']=True
        self.s.dispatch('/v1/slack/apply',self.approval(first))
        with self.assertRaises(SlackError) as caught:self.s.dispatch('/v1/slack/apply',self.approval(second,'0002'))
        self.assertEqual(caught.exception.code,'rule_base_changed')
        with self.s.store.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM accepted').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM approvals').fetchone()[0],1)
        # Re-read an immutable proposal preserves the originally displayed base.
        again=self.s.dispatch('/v1/slack/proposal',dict(proposal_id=first['payload']['proposal_id'],review_request_id='request_rule_0001',
                  session_id='session_fixture_01',restriction=RESTRICTION))
        self.assertEqual(again,first)

class RollbackAndCleanupTests(unittest.TestCase):
    def test_rule_transaction_failure_rolls_back_rule_receipt_and_acceptance(self):
        fixture=SafeRuleTests();fixture.setUp()
        try:
            value=fixture.approval(fixture.proposal())
            fixture.s.policy['human_apply_enabled']=True
            with fixture.s.store.db() as db:
                db.execute("CREATE TRIGGER fail_public_fixture BEFORE INSERT ON approvals BEGIN SELECT RAISE(ABORT,'public fixture failure'); END")
            with self.assertRaises(sqlite3.IntegrityError):fixture.s.dispatch('/v1/slack/apply',value)
            with fixture.s.store.db() as db:
                for table in ('accepted','approvals','rule_state'):
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0],0)
                db.execute('DROP TRIGGER fail_public_fixture')
            self.assertEqual(fixture.s.dispatch('/v1/slack/apply',value)['status'],'applied_restricted')
        finally:fixture.tearDown()
    def test_cleanup_releases_inference_slot_even_when_health_fails(self):
        c=controller()
        with c.background_job(4):
            c.health=lambda p: (_ for _ in ()).throw(OSError('public fixture health error'))
        self.assertEqual(c.state,gateway.ModelState.FAILED)
        self.assertTrue(c.inference.acquire(blocking=False))
        c.inference.release()
        self.assertEqual(c.snapshot()['active_jobs'],0)
        c.health=lambda p:True
        with c.job('normal_after_health_failure'):pass

class CanonicalContractTests(unittest.TestCase):
    def test_public_host_canonicalization_vector(self):
        vector=json.loads((Path(__file__).parent/'fixtures/slack-canonical.json').read_text(encoding='utf-8'))
        self.assertEqual(canonical(vector['value']).decode('utf-8'),vector['canonical_utf8'])
        self.assertEqual(digest(vector['value']),vector['sha256'])



class DurableStatusTests(unittest.TestCase):
    def test_exact_apply_status_is_bound_to_hash_and_session(self):
        fixture=ServiceTests();fixture.setUp()
        try:
            value=fixture.approval();fixture.s.policy['human_apply_enabled']=True
            query=dict(operation='apply',request_id=value['request_id'],session_id=value['session_id'],request_hash=digest(value),restriction=RESTRICTION)
            self.assertEqual(fixture.dispatch('status',query),dict(state='unknown',retry_safe=False))
            applied=fixture.dispatch('apply',value)
            self.assertEqual(fixture.dispatch('status',query)['result'],applied)
            self.assertEqual(fixture.dispatch('status',query)['state'],'applied')
            for key,bad in [('session_id','other_session_001'),('request_hash','0'*64)]:
                altered=query|{key:bad}
                with self.subTest(key=key),self.assertRaises(SlackError):fixture.dispatch('status',altered)
        finally:fixture.tearDown()
    def test_completed_and_uncertain_run_status_never_reexecutes(self):
        fixture=ServiceTests();fixture.setUp()
        try:
            value=request();query=dict(operation='chat',request_id=value['request_id'],session_id=value['session_id'],request_hash=digest(dict(action='chat',value=value)),restriction=RESTRICTION)
            self.assertEqual(fixture.dispatch('status',query)['state'],'unknown')
            fixture.s.store.reserve(value['request_id'],query['request_hash'],value['session_id'])
            self.assertEqual(fixture.dispatch('status',query),dict(state='unresolved',request_hash=query['request_hash'],retry_safe=False,recovery_contract='quiescent-attempt-v1'))
            self.assertFalse(fixture.calls)
            other=request(rid='request_completed_status')
            result=fixture.dispatch('chat',other)
            query.update(request_id=other['request_id'],request_hash=digest(dict(action='chat',value=other)))
            self.assertEqual(fixture.dispatch('status',query)['result'],result)
            self.assertEqual(len(fixture.calls),1)
            query['operation']='review'
            with self.assertRaises(SlackError):fixture.dispatch('status',query)
        finally:fixture.tearDown()


class SchemaBoundaryTests(unittest.TestCase):
    def test_unknown_legacy_restricted_schema_is_not_silently_migrated(self):
        fixture=ServiceTests();fixture.setUp()
        try:
            path=fixture.s.store.path;path.parent.mkdir(parents=True)
            with contextlib.closing(sqlite3.connect(path)) as db, db:
                db.execute('CREATE TABLE runs(id TEXT PRIMARY KEY, state TEXT)')
                db.execute("INSERT INTO runs VALUES ('public_legacy_fixture','unknown')")
            with self.assertRaises(SlackError) as caught:fixture.dispatch('chat',request())
            self.assertEqual(caught.exception.code,'restricted_schema_mismatch')
            self.assertFalse(fixture.calls)
            with contextlib.closing(sqlite3.connect(path)) as db, db:self.assertEqual(db.execute('SELECT state FROM runs').fetchone()[0],'unknown')
        finally:fixture.tearDown()


class PersistenceIntegrityTests(unittest.TestCase):
    def test_changed_stored_proposal_is_not_applied_under_old_hash(self):
        fixture=ServiceTests();fixture.setUp()
        try:
            value=fixture.approval();fixture.s.policy['human_apply_enabled']=True
            with fixture.s.store.db() as db:
                raw=db.execute('SELECT payload FROM proposals').fetchone()[0]
                payload=json.loads(raw);payload['candidate']['body']='changed public fixture'
                db.execute('UPDATE proposals SET payload=?',(canonical(payload).decode(),))
            with self.assertRaises(SlackError) as caught:fixture.dispatch('apply',value)
            self.assertEqual(caught.exception.code,'proposal_integrity')
            with fixture.s.store.db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM accepted').fetchone()[0],0)
        finally:fixture.tearDown()
    def test_changed_review_candidate_cannot_borrow_existing_review_hash(self):
        fixture=ServiceTests();fixture.setUp()
        try:
            fixture.review()
            with fixture.s.store.db() as db:
                result=json.loads(db.execute('SELECT result FROM runs').fetchone()[0])
                result['candidate']['body']='changed public fixture'
                db.execute('UPDATE runs SET result=?',(canonical(result).decode(),))
            with self.assertRaises(SlackError) as caught:fixture.dispatch('proposal',dict(proposal_id='proposal_changed_01',review_request_id='request_fixture_0001',session_id='session_fixture_01',restriction=RESTRICTION))
            self.assertEqual(caught.exception.code,'review_integrity')
        finally:fixture.tearDown()

if __name__=='__main__':unittest.main()

