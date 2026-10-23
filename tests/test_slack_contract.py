"""Public execution contract regressions. No production data, endpoints or models."""
import contextlib
import copy
import json
import unittest
import test_slack as public
import slack_gateway as slack


class ExecutionContractTests(unittest.TestCase):
    def setUp(self):
        self.fixture = public.ServiceTests()
        self.fixture.setUp()
        self.s = self.fixture.s
        self.value = public.request()
    def tearDown(self):
        self.fixture.tearDown()
    def query(self):
        return dict(operation='chat',request_id=self.value['request_id'],
                    session_id=self.value['session_id'],
                    request_hash=slack.digest(dict(action='chat',value=self.value)),
                    restriction=copy.deepcopy(slack.RESTRICTION))
    def cancel(self):
        return self.s.dispatch('/v1/slack/cancel',dict(request_id=self.value['request_id'],
            session_id=self.value['session_id'],restriction=copy.deepcopy(slack.RESTRICTION)))
    def test_same_request_during_generation_is_running_not_terminal(self):
        seen=[]
        def model(*args):
            seen.append(self.s.execute('chat',self.value))
            return 'PUBLIC answer'
        self.s.model=model
        result=self.s.execute('chat',self.value)
        self.assertEqual(seen,[dict(status='running',retry_safe=False)])
        self.assertEqual(result['status'],'complete')
        self.assertEqual(self.s.execute('chat',self.value),result)
        self.assertEqual(len(seen),1)
    def test_orphan_reservation_is_unresolved_not_invented_interruption(self):
        self.s.store.reserve(self.value['request_id'],self.query()['request_hash'],self.value['session_id'])
        self.assertEqual(self.s.execute('chat',self.value),dict(status='unresolved',retry_safe=False))
        self.assertEqual(self.s.status(self.query())['state'],'unresolved')
        self.assertFalse(self.fixture.calls)
    def test_acknowledged_cancel_after_lease_release_prevents_complete(self):
        original=self.s.controller.background_job
        acknowledged=[]
        @contextlib.contextmanager
        def release_then_cancel(priority):
            with original(priority) as lease: yield lease
            acknowledged.append(self.cancel())
        self.s.controller.background_job=release_then_cancel
        result=self.s.execute('chat',self.value)
        self.assertEqual(acknowledged,[dict(cancelled=True)])
        self.assertEqual((result['status'],result['reason']),('interrupted','explicit_cancel'))
        self.assertNotIn('text',result)
        self.assertEqual(self.s.status(self.query())['state'],'interrupted')
    def test_registration_lasts_until_terminal_persistence_and_late_cancel_is_refused(self):
        original=self.s.store.finish
        seen=[]
        def finish(rid,result):
            seen.append((rid in self.s.active,self.s.status(self.query())['state'],self.cancel()['cancelled']))
            original(rid,result)
        self.s.store.finish=finish
        result=self.s.execute('chat',self.value)
        self.assertEqual(seen,[(True,'running',False)])
        self.assertEqual(result['status'],'complete')
        self.assertFalse(self.s.active)
    def test_cancel_transport_io_does_not_hold_service_registration_lock(self):
        lock_free=[]
        service=self.s
        class Transport:
            def abort(self):
                acquired=service.lock.acquire(blocking=False)
                lock_free.append(acquired)
                if acquired: service.lock.release()
        def model(messages,tokens,timeout,lease):
            lease.register(Transport())
            self.assertTrue(self.cancel()['cancelled'])
            lease.check()
        self.s.model=model
        self.assertEqual(self.s.execute('chat',self.value)['reason'],'explicit_cancel')
        self.assertTrue(lock_free)
        self.assertTrue(all(lock_free))
    def test_snapshot_does_not_initialize_missing_database_or_claim_quiescence(self):
        result=self.s.activity_snapshot()
        self.assertFalse(result['available'])
        self.assertFalse(result['quiescent'])
        self.assertFalse((self.fixture.root/'runtime').exists())
    def test_registered_finalization_is_busy_even_after_controller_releases_lease(self):
        original=self.s.controller.background_job
        seen=[]
        @contextlib.contextmanager
        def observe(priority):
            with original(priority) as lease: yield lease
            seen.append((self.s.controller.snapshot()['active_jobs'],self.s.activity_snapshot()))
        self.s.controller.background_job=observe
        self.s.execute('chat',self.value)
        self.assertEqual(seen[0][0],0)
        self.assertEqual(seen[0][1]['active_requests'],1)
        self.assertFalse(seen[0][1]['quiescent'])
        final=self.s.activity_snapshot()
        self.assertTrue(final['quiescent'])
        self.assertTrue(final['snapshot_only'])
        self.assertFalse(final['admission_fenced'])
        self.assertEqual(final['scope'],'chat_review_apply')
    def test_stale_durable_run_prevents_quiescence_without_modifying_it(self):
        self.s.store.reserve(self.value['request_id'],self.query()['request_hash'],self.value['session_id'])
        result=self.s.activity_snapshot()
        self.assertTrue(result['available'])
        self.assertEqual(result['nonterminal_runs'],1)
        self.assertFalse(result['quiescent'])
        self.assertEqual(self.s.status(self.query())['state'],'unresolved')
        self.assertNotIn('PUBLIC',json.dumps(result))
        self.assertNotIn(self.value['request_id'],json.dumps(result))
    def test_application_in_progress_prevents_snapshot_quiescence(self):
        self.s.store.reserve(self.value['request_id'],self.query()['request_hash'],self.value['session_id'])
        self.s.store.finish(self.value['request_id'],dict(status='interrupted',retry_safe=False))
        with self.s.lock: self.s.applications['public_session']=1
        self.assertFalse(self.s.activity_snapshot()['quiescent'])
    def test_contract_is_advertised_without_changing_model_safety_bounds(self):
        caps=self.s.capabilities()
        self.assertEqual(caps['execution_contract'],'localbrain.slack-execution.v2')
        self.assertEqual(caps['chat_timeout_seconds'],120)
        self.assertEqual(caps['chat_max_tokens'],1024)
        self.assertEqual(caps['review_timeout_seconds'],60)
        self.assertFalse(caps['cancel_ack_is_terminal'])
        for key in ('external_ai','cloud_fallback','ordinary_secondbrain_adoption','automatic_adoption'):
            self.assertFalse(caps[key])

    def test_terminal_receipt_cannot_be_overwritten_by_late_finalizer(self):
        complete=self.s.execute('chat',self.value)
        with self.assertRaises(slack.SlackError) as caught:
            self.s.store.finish(self.value['request_id'],dict(status='interrupted',retry_safe=False))
        self.assertEqual(caught.exception.code,'run_terminal_conflict')
        self.assertEqual(self.s.execute('chat',self.value),complete)
        self.assertEqual(len(self.fixture.calls),1)
    def test_persistence_failure_remains_unresolved_without_reexecution(self):
        import sqlite3
        original=self.s.store.finish
        self.s.store.finish=lambda *args: (_ for _ in ()).throw(sqlite3.OperationalError('PUBLIC fixture'))
        with self.assertRaises(sqlite3.OperationalError):
            self.s.execute('chat',self.value)
        self.assertFalse(self.s.active)
        self.s.store.finish=original
        self.assertEqual(self.s.status(self.query())['state'],'unresolved')
        self.assertEqual(self.s.execute('chat',self.value),dict(status='unresolved',retry_safe=False))
        self.assertEqual(len(self.fixture.calls),1)
    def test_snapshot_does_not_claim_stable_after_registration_cycle(self):
        self.s.execute('chat',self.value)
        original=self.s.store.nonterminal_count
        def count():
            with self.s.lock: self.s.activity_generation+=2
            return original()
        self.s.store.nonterminal_count=count
        result=self.s.activity_snapshot()
        self.assertFalse(result['stable'])
        self.assertFalse(result['quiescent'])
    def test_snapshot_is_read_only_and_exports_counts_not_results(self):
        import hashlib
        self.s.execute('chat',self.value)
        before=hashlib.sha256(self.s.store.path.read_bytes()).hexdigest()
        result=self.s.activity_snapshot()
        self.assertTrue(result['quiescent'])
        self.assertEqual(before,hashlib.sha256(self.s.store.path.read_bytes()).hexdigest())
        self.assertNotIn(self.value['prompt'],json.dumps(result))


if __name__=='__main__': unittest.main()
