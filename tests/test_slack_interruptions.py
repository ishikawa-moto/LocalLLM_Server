"""Public stub regressions only. No original request, production DB or live model."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import slack_gateway as slack
from test_slack import controller, request, PIN
from site_settings import LOOPBACK


class InterruptionMetadataTests(unittest.TestCase):
    def service(self, model=None):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        calls = []
        def answer(messages, tokens, timeout, lease):
            calls.append((tokens, timeout))
            return 'Public fixture answer.'
        service = slack.SlackService(Path(td.name), controller(), model or answer,
            LOOPBACK, 'public-host', dict(enabled=True,host_certificate_sha256=PIN))
        return service, calls

    def test_deadline_input_bytes_and_caller_boundaries(self):
        for size, expected in ((1,30),(2048,30),(2049,45),(4096,45),(4097,60),(10902,105),(16000,120)):
            with self.subTest(size=size):
                self.assertEqual(slack.chat_deadline_seconds(size,120),expected)
                self.assertEqual(slack.chat_deadline_seconds(size,30),30)
                self.assertEqual(slack.chat_deadline_seconds(size,5),5)

    def test_long_public_chat_gets_bounded_allowance_and_unchanged_output_limit(self):
        service,calls=self.service()
        value=request();value.update(context='c'*10840,prompt='q'*61,timeout_seconds=120,max_tokens=2048)
        self.assertEqual(service.execute('chat',value)['status'],'complete')
        self.assertEqual(calls[0][0],1024)
        self.assertGreater(calls[0][1],100)
        self.assertLessEqual(calls[0][1],105)
        self.assertEqual(service.capabilities()['chat_timeout_seconds'],120)

    def test_review_total_deadline_stays_at_sixty_seconds(self):
        calls=[]
        def model(messages,tokens,timeout,lease):
            calls.append(timeout);return '{}'
        service,_=self.service(model)
        value=request('review');value['timeout_seconds']=120
        self.assertEqual(service.execute('review',value)['status'],'complete')
        self.assertEqual(len(calls),2)
        self.assertTrue(all(0<t<=60 for t in calls))

    def test_first_lease_cause_wins_and_marking_does_no_transport_io(self):
        lease=slack.Lease(4)
        class Transport:
            def abort(self): raise AssertionError('mark must not perform transport I/O')
        lease.register(Transport())
        lease.cancel('foreground_preempted')
        lease.cancel('deadline_exceeded')
        self.assertEqual(lease.reason,'foreground_preempted')
        with self.assertRaises(InterruptedError):lease.check()

    def test_deadline_reason_is_persisted_replayed_and_reported_without_retry(self):
        class ImmediateTimer:
            daemon=False
            def __init__(self,delay,callback,args=()):self.callback,self.args=callback,args
            def start(self):self.callback(*self.args)
            def cancel(self):pass
        calls=[]
        def model(messages,tokens,timeout,lease):
            calls.append(True);lease.check()
        service,_=self.service(model)
        value=request()
        with patch.object(slack.threading,'Timer',ImmediateTimer):
            result=service.execute('chat',value)
        self.assertEqual((result['status'],result['retry_safe'],result['reason']),('interrupted',False,'deadline_exceeded'))
        self.assertEqual(result['diagnostics']['deadline_seconds'],5)
        self.assertIsInstance(result['diagnostics']['started_at_unix_ms'],int)
        self.assertIsInstance(result['diagnostics']['finished_at_unix_ms'],int)
        service.model=lambda *a:self.fail('interrupted request must not infer twice')
        self.assertEqual(service.execute('chat',value),result)
        status=service.status(dict(operation='chat',request_id=value['request_id'],session_id=value['session_id'],
            request_hash=slack.digest(dict(action='chat',value=value)),restriction=slack.RESTRICTION))
        self.assertEqual(status['reason'],'deadline_exceeded')
        self.assertEqual(status['diagnostics'],result['diagnostics'])
        self.assertEqual(len(calls),1)

    def test_deadline_is_checked_even_if_timer_callback_is_delayed(self):
        now=[0.]
        class InertTimer:
            daemon=False
            def __init__(self,*a,**k):pass
            def start(self):pass
            def cancel(self):pass
        def model(*args):now[0]=6.;return 'Late answer must be discarded.'
        service,_=self.service(model)
        with patch.object(slack.time,'monotonic',side_effect=lambda:now[0]),patch.object(slack.threading,'Timer',InertTimer):
            result=service.execute('chat',request())
        self.assertEqual(result['reason'],'deadline_exceeded')
        self.assertNotIn('text',result)

    def test_transport_timeout_and_failure_are_distinct_and_exception_text_is_private(self):
        for exc,reason in ((TimeoutError('PRIVATE-fixture-detail'),'transport_timeout'),
                           (ConnectionError('PRIVATE-fixture-detail'),'upstream_failure'),
                           (ValueError('PRIVATE-fixture-detail'),'invalid_output')):
            with self.subTest(reason=reason):
                def model(*args):raise exc
                service,_=self.service(model)
                result=service.execute('chat',request())
                self.assertEqual(result['reason'],reason)
                self.assertFalse(result['retry_safe'])
                self.assertNotIn('PRIVATE-fixture-detail',json.dumps(result))

    def test_legacy_receipt_is_not_reclassified_or_reexecuted(self):
        service,_=self.service(lambda *a:self.fail('legacy active request must not retry'))
        value=request()
        service.store.reserve(value['request_id'],slack.digest(dict(action='chat',value=value)),value['session_id'])
        self.assertEqual(service.execute('chat',value),dict(status='interrupted',retry_safe=False))

    def test_safe_metadata_whitelist_rejects_unknown_fields_and_arbitrary_reasons(self):
        value=dict(reason='upstream_failure',diagnostics=dict(input_bytes=10,phase='generation',
            elapsed_ms=True,source='PRIVATE-fixture-detail',exception='PRIVATE-fixture-detail'))
        result=slack.safe_interruption(value)
        self.assertEqual(result['diagnostics'],dict(input_bytes=10,phase='generation'))
        self.assertEqual(slack.safe_interruption(dict(reason={'secret':'fixture'})),dict(status='interrupted',retry_safe=False))


    def test_io_error_after_absolute_deadline_is_not_misreported_as_upstream_failure(self):
        now=[0.]
        class InertTimer:
            daemon=False
            def __init__(self,*a,**k):pass
            def start(self):pass
            def cancel(self):pass
        def model(*args):
            now[0]=6.
            raise OSError('PRIVATE-fixture-detail')
        service,_=self.service(model)
        with patch.object(slack.time,'monotonic',side_effect=lambda:now[0]),patch.object(slack.threading,'Timer',InertTimer):
            result=service.execute('chat',request())
        self.assertEqual(result['reason'],'deadline_exceeded')
        self.assertNotIn('PRIVATE-fixture-detail',json.dumps(result))
