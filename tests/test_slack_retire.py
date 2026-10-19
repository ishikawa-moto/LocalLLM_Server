"""Additional public retirement tests only; the prior 109 tests are not rerun."""
import copy
import http.client
import json
from pathlib import Path
import sqlite3
import threading
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer
import test_slack as fixtures
from slack_gateway import RESTRICTION, SlackError, RunCancellation, canonical, digest

SID='session_fixture_01'

def retirement(rid='retire_fixture_0001',sid=SID):
    return dict(request_id=rid,session_id=sid,restriction=copy.deepcopy(RESTRICTION))

class RetirementTests(unittest.TestCase):
    def setUp(self):
        self.fx=fixtures.ServiceTests();self.fx.setUp();self.s=self.fx.s
    def tearDown(self):self.fx.tearDown()
    def retire(self,value=None):return self.s.dispatch('/v1/slack/retire',value or retirement())
    def snapshot(self,table):
        with self.s.store.db() as db:return db.execute('SELECT * FROM '+table+' ORDER BY 1').fetchall()
    def test_removes_only_unadopted_session_payloads_and_zeroes_deleted_db_content(self):
        self.fx.proposed()
        marker='PUBLIC_RETIRE_DELETE_ONLY_CANARY'
        self.s.model=lambda *a:marker
        self.fx.dispatch('chat',fixtures.request(rid='request_retire_chat'))
        other=fixtures.request(rid='request_other_chat');other['session_id']='other_session_fixture'
        self.s.model=lambda *a:'Other public fixture is retained.'
        self.fx.dispatch('chat',other)
        before_other=[r for r in self.snapshot('runs') if r[4]==other['session_id']]
        with patch.object(self.s.controller,'background_job',side_effect=AssertionError('retire must not admit inference')):
            self.s.model=lambda *a:self.fail('retire must not call model')
            self.assertEqual(self.retire(),dict(status='retired',session_id=SID,request_id='retire_fixture_0001',adopted_knowledge_preserved=True))
        self.assertEqual(self.snapshot('proposals'),[])
        self.assertEqual(self.snapshot('runs'),before_other)
        self.assertEqual(len(self.snapshot('retired_sessions')),1)
        self.assertNotIn(marker.encode(),self.s.store.path.read_bytes())
    def test_adopted_knowledge_rule_and_complete_review_audit_survive(self):
        for kind in ('knowledge','rule'):
            fx=fixtures.ServiceTests() if kind=='knowledge' else fixtures.SafeRuleTests();fx.setUp()
            try:
                value=fx.approval() if kind=='knowledge' else fx.approval(fx.proposal())
                s=fx.s;s.policy['human_apply_enabled']=True
                applied=s.dispatch('/v1/slack/apply',value)
                with s.store.db() as db:
                    before={t:db.execute('SELECT * FROM '+t+' ORDER BY 1').fetchall() for t in ('accepted','approvals','rule_state','proposals','runs')}
                s.model=lambda *a:'Discard this unadopted public chat.'
                s.dispatch('/v1/slack/chat',fixtures.request(rid='request_unadopted_extra'))
                s.dispatch('/v1/slack/retire',retirement())
                with s.store.db() as db:
                    for table,rows in before.items():
                        self.assertEqual(db.execute('SELECT * FROM '+table+' ORDER BY 1').fetchall(),rows,(kind,table))
                query=dict(operation='apply',request_id=value['request_id'],session_id=SID,request_hash=digest(value),restriction=RESTRICTION)
                self.assertEqual(s.dispatch('/v1/slack/status',query)['result'],applied)
                with self.assertRaises(SlackError) as caught:s.dispatch('/v1/slack/apply',value)
                self.assertEqual(caught.exception.code,'session_retired')
            finally:fx.tearDown()
    def test_repeat_retirement_is_idempotent_and_id_cannot_change_session(self):
        result=self.retire()
        self.assertEqual(self.retire(),result)
        alias=self.retire(retirement('retire_fixture_0002'))
        self.assertEqual(alias['request_id'],'retire_fixture_0002')
        with self.assertRaises(SlackError) as caught:self.retire(retirement(sid='other_session_fixture'))
        self.assertEqual(caught.exception.code,'retirement_id_conflict')
        self.assertEqual(len(self.snapshot('retired_sessions')),1)
    def test_live_model_is_deferred_without_cancellation_then_retired(self):
        started=threading.Event();release=threading.Event();results=[];errors=[];calls=[]
        def model(messages,tokens,timeout,lease):
            calls.append(lease);started.set();release.wait(3);lease.check();return 'Public completed fixture.'
        self.s.model=model
        def work():
            try:results.append(self.fx.dispatch('chat',fixtures.request()))
            except Exception as exc:errors.append(exc)
        thread=threading.Thread(target=work);thread.start()
        try:
            self.assertTrue(started.wait(2))
            self.assertEqual(self.retire(),dict(status='deferred',retry_safe=True))
            self.assertFalse(calls[0].cancelled.is_set())
        finally:release.set();thread.join(3)
        self.assertFalse(thread.is_alive());self.assertFalse(errors)
        self.assertEqual(results[0]['status'],'complete')
        self.assertEqual(self.retire()['status'],'retired')
        for rid in ('request_fixture_0001','request_after_retired'):
            with self.assertRaises(SlackError) as caught:self.fx.dispatch('chat',fixtures.request(rid=rid))
            self.assertEqual(caught.exception.code,'session_retired')
        self.assertEqual(len(calls),1)
    def test_uncertain_durable_run_is_not_force_deleted_after_restart(self):
        value=fixtures.request()
        self.s.store.reserve(value['request_id'],digest(dict(action='chat',value=value)),SID)
        self.assertEqual(self.retire(),dict(status='deferred',retry_safe=True))
        self.assertEqual(self.snapshot('retired_sessions'),[])
        self.assertEqual(self.snapshot('runs')[0][2],'active')
        self.s.store.finish(value['request_id'],dict(status='interrupted',retry_safe=False))
        self.assertEqual(self.retire()['status'],'retired')
    def test_other_session_activity_and_cancellation_objects_are_untouched(self):
        other=RunCancellation()
        self.s.active['other_request_fixture']=('other_session_fixture',other)
        self.s.applications['other_session_fixture']=1
        self.assertEqual(self.retire()['status'],'retired')
        self.assertFalse(other.cancelled.is_set())
        self.assertIn('other_request_fixture',self.s.active)
        self.assertEqual(self.s.applications,{'other_session_fixture':1})
    def test_apply_in_progress_is_deferred_without_changing_its_ownership(self):
        value=self.fx.approval();self.s.policy['human_apply_enabled']=True
        def applying(v):
            self.assertEqual(self.s.applications[SID],1)
            self.assertEqual(self.retire(),dict(status='deferred',retry_safe=True))
            self.assertEqual(self.s.applications[SID],1)
            return {'status':'public_gate_fixture'}
        with patch.object(self.s,'_apply',side_effect=applying):self.s.apply(value)
        self.assertEqual(self.s.applications,{})
        self.assertEqual(self.retire()['status'],'retired')
    def test_existing_writer_lock_defers_and_is_not_committed_or_released(self):
        with self.s.store.db() as writer:
            writer.execute('BEGIN IMMEDIATE')
            self.assertEqual(self.retire(),dict(status='deferred',retry_safe=True))
            self.assertTrue(writer.in_transaction)
            writer.execute('SELECT 1')
        self.assertEqual(self.retire()['status'],'retired')
    def test_inference_admission_before_durable_reservation_is_protected(self):
        entered=threading.Event();release=threading.Event();errors=[]
        original=self.s.store.reserve
        def reserve(*args):entered.set();release.wait(3);return original(*args)
        def work():
            try:self.fx.dispatch('chat',fixtures.request())
            except Exception as exc:errors.append(exc)
        with patch.object(self.s.store,'reserve',side_effect=reserve):
            thread=threading.Thread(target=work);thread.start()
            try:
                self.assertTrue(entered.wait(2))
                self.assertEqual(self.retire(),dict(status='deferred',retry_safe=True))
                self.assertFalse(self.s.controller.background.cancelled.is_set())
            finally:release.set();thread.join(3)
        self.assertFalse(thread.is_alive());self.assertFalse(errors)
        self.assertEqual(self.retire()['status'],'retired')
    def test_retirement_winning_before_admission_prevents_late_inference(self):
        checked=threading.Event();release=threading.Event();errors=[]
        original=self.s.store.existing
        def existing(*args):result=original(*args);checked.set();release.wait(3);return result
        self.s.model=lambda *a:self.fail('retired Session must never infer')
        def work():
            try:self.fx.dispatch('chat',fixtures.request())
            except Exception as exc:errors.append(exc)
        with patch.object(self.s.store,'existing',side_effect=existing):
            thread=threading.Thread(target=work);thread.start()
            try:
                self.assertTrue(checked.wait(2))
                self.assertEqual(self.retire()['status'],'retired')
            finally:release.set();thread.join(3)
        self.assertFalse(thread.is_alive());self.assertEqual(len(errors),1)
        self.assertIsInstance(errors[0],SlackError);self.assertEqual(errors[0].code,'session_retired')
        self.assertEqual(self.snapshot('runs'),[])
    def test_old_review_proposal_and_apply_cannot_resurrect_retired_session(self):
        value=self.fx.approval();self.s.policy['human_apply_enabled']=True
        self.retire()
        calls=[('review',fixtures.request('review',rid='request_late_review')),
               ('proposal',dict(proposal_id='proposal_late_fixture',review_request_id='request_fixture_0001',session_id=SID,restriction=RESTRICTION)),
               ('apply',value)]
        for action,body in calls:
            with self.subTest(action=action),self.assertRaises(SlackError) as caught:self.fx.dispatch(action,body)
            self.assertEqual(caught.exception.code,'session_retired')
        self.assertEqual(self.snapshot('accepted'),[])
    def test_partial_delete_failure_rolls_back_payload_and_tombstone_together(self):
        self.fx.proposed();before={t:self.snapshot(t) for t in ('runs','proposals')}
        with self.s.store.db() as db:
            db.execute("CREATE TRIGGER fail_retirement_fixture BEFORE DELETE ON runs BEGIN SELECT RAISE(ABORT,'public retirement fixture'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.retire()
        for table,rows in before.items():self.assertEqual(self.snapshot(table),rows)
        self.assertEqual(self.snapshot('retired_sessions'),[]);self.assertEqual(self.snapshot('retirements'),[])
        with self.s.store.db() as db:db.execute('DROP TRIGGER fail_retirement_fixture')
        self.assertEqual(self.retire()['status'],'retired')
    def test_schema_restriction_and_disabled_feature_remain_fail_closed(self):
        for body in (retirement()|{'reason':'not accepted'},retirement(sid='../outside'),retirement()|{'restriction':{}},retirement()|{'request_id':7}):
            with self.subTest(body=body),self.assertRaises(SlackError):self.retire(body)
        self.s.policy['enabled']=False
        with self.assertRaises(SlackError):self.retire()
        self.assertFalse(self.s.capabilities()['session_retirement'])
        self.assertFalse(self.s.store.path.exists())

    def test_purged_ids_cannot_be_rebound_to_a_new_session(self):
        self.fx.proposed();self.retire()
        value=fixtures.request();value['session_id']='new_session_fixture'
        with self.assertRaises(SlackError) as caught:self.fx.dispatch('chat',value)
        self.assertEqual(caught.exception.code,'request_retired')
        outputs=iter([json.dumps(dict(kind='knowledge',body=fixtures.SOURCE,evidence='blue button')),
                      '{"pass":true,"evidence":"blue button","issues":[]}'])
        self.s.model=lambda *a:next(outputs)
        review=fixtures.request('review',rid='request_new_incarnation');review['session_id']=value['session_id']
        self.fx.dispatch('review',review)
        proposal=dict(proposal_id='proposal_fixture_01',review_request_id=review['request_id'],session_id=review['session_id'],restriction=RESTRICTION)
        with self.assertRaises(SlackError) as caught:self.fx.dispatch('proposal',proposal)
        self.assertEqual(caught.exception.code,'proposal_retired')
        proposal['proposal_id']='proposal_new_incarnation'
        self.assertEqual(self.fx.dispatch('proposal',proposal)['status'],'proposed_restricted')
    def test_duplicate_before_reservation_does_not_clear_the_first_ownership(self):
        entered=threading.Event();release=threading.Event();errors=[]
        original=self.s.store.reserve
        def reserve(*args):entered.set();release.wait(3);return original(*args)
        def work():
            try:self.fx.dispatch('chat',fixtures.request())
            except Exception as exc:errors.append(exc)
        with patch.object(self.s.store,'reserve',side_effect=reserve):
            thread=threading.Thread(target=work);thread.start()
            try:
                self.assertTrue(entered.wait(2))
                with self.assertRaises(SlackError) as caught:self.fx.dispatch('chat',fixtures.request())
                self.assertEqual(caught.exception.code,'request_already_reserved')
                self.assertIn('request_fixture_0001',self.s.active)
                self.assertEqual(self.retire(),dict(status='deferred',retry_safe=True))
                self.assertFalse(self.s.controller.background.cancelled.is_set())
            finally:release.set();thread.join(3)
        self.assertFalse(thread.is_alive());self.assertFalse(errors)
        self.assertEqual(len(self.fx.calls),1)

class RetirementHTTPTests(unittest.TestCase):
    def test_existing_host_authentication_guards_the_new_route(self):
        gateway=fixtures.gateway
        class CertSocket:
            def __init__(self,sock):self.sock=sock
            def __getattr__(self,key):return getattr(self.sock,key)
            def getpeercert(self,binary_form=False):return fixtures.CERT if binary_form else {'fixture':True}
        class Handler(gateway.Handler):
            def setup(self):self.request=CertSocket(self.request);super().setup()
            def log_message(self,*args):pass
        fx=fixtures.ServiceTests();fx.setUp()
        server=ThreadingHTTPServer((fixtures.LOOPBACK,0),Handler);server.slack_service=fx.s
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def call(owner):
            conn=http.client.HTTPConnection(fixtures.LOOPBACK,server.server_port,timeout=3)
            try:
                headers={'Content-Type':'application/json','X-LocalBrain-Approval-Owner':owner}
                conn.request('POST','/v1/slack/retire',canonical(retirement()),headers)
                response=conn.getresponse();return response.status,json.loads(response.read())
            finally:conn.close()
        try:
            with patch.object(gateway,'CONFIG',gateway.GatewayConfig(allowed_ips=(fixtures.LOOPBACK,))):
                self.assertEqual(call('Slack-member')[0],403)
                code,result=call('ClientPC-windows-host');self.assertEqual(code,200);self.assertEqual(result['status'],'retired')
        finally:server.shutdown();server.server_close();thread.join(2);fx.tearDown()

if __name__=='__main__':unittest.main()
