"""No production configuration, manager, model or Slack account is used."""
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from pathlib import Path
import control_gateway as g
from slack_gateway import SlackService, SlackError, Deferred
from test_gateway import Runtime
from test_slack import request, PIN


class ColdStartTests(unittest.TestCase):
    def make(self, runtime, base=True, timeout=.15):
        return g.LifecycleController(g.GatewayConfig(start_timeout=timeout), runtime.manager, runtime.health,
            sleeper=lambda _: time.sleep(.003), audit_fn=lambda *a, **k: None, base_ready=lambda: None,
            background_ready=lambda: base,start_admission=lambda: True)

    def wait(self, c):
        end=time.monotonic()+2
        while c.background_start is not None and time.monotonic()<end: time.sleep(.005)
        self.assertIsNone(c.background_start)

    def test_cold_once_and_warm_zero(self):
        r=Runtime();c=self.make(r)
        self.assertTrue(c.request_background_start());self.wait(c)
        self.assertEqual(c.state,g.ModelState.READY);self.assertEqual(r.starts,1)
        self.assertFalse(c.request_background_start());self.assertEqual(r.starts,1)

    def test_start_coalesces_and_foreground_joins(self):
        r=Runtime(delayed=True);c=self.make(r,timeout=1)
        self.assertTrue(c.request_background_start())
        for _ in range(8):self.assertFalse(c.request_background_start())
        errors=[];entered=[]
        def foreground():
            try:
                with c.job('public-foreground-001'):entered.append(True)
            except Exception as e:errors.append(type(e).__name__)
        worker=threading.Thread(target=foreground);worker.start();time.sleep(.02)
        with self.assertRaises(Deferred):
            with c.background_job(4):pass
        r.release.set();worker.join(2);self.wait(c)
        self.assertFalse(errors);self.assertTrue(entered);self.assertEqual(r.starts,1)

    def test_foreground_or_busy_prevents_background_start(self):
        for state in ('queue','busy'):
            r=Runtime();c=self.make(r,base=state!='base')
            if state=='queue':c.queued_jobs=1
            if state=='busy':c.active_jobs=1
            self.assertFalse(c.request_background_start());self.assertEqual(r.starts,0)

    def test_failure_no_retry_no_force_stop(self):
        r=Runtime(fail=True);c=self.make(r,timeout=.025)
        self.assertTrue(c.request_background_start());self.wait(c)
        self.assertEqual(c.state,g.ModelState.FAILED)
        for _ in range(4):self.assertFalse(c.request_background_start())
        self.assertEqual((r.starts,r.stops),(1,0))

    def test_stop_does_not_race_owned_start(self):
        r=Runtime(delayed=True);c=self.make(r,timeout=1);c.request_background_start()
        self.assertFalse(c.stop('public-test'));r.release.set();self.wait(c)
        self.assertEqual(r.stops,0)

    def service(self,c,enabled):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        return SlackService(Path(self.tmp.name),c,lambda *a:'PUBLIC response','public-client','public-owner',
            dict(enabled=True,host_certificate_sha256=PIN,human_apply_enabled=False,model_cold_start=enabled))

    def test_capability_read_review_and_disabled_do_not_start(self):
        for enabled in (False,True):
            r=Runtime();c=self.make(r);s=self.service(c,enabled)
            self.assertEqual(s.capabilities()['model_cold_start'],enabled)
            self.assertEqual(s.execute('review',request('review'))['status'],'deferred')
            self.assertEqual(r.starts,0)
            if not enabled:
                self.assertEqual(s.execute('chat',request())['status'],'deferred')
                self.assertEqual(r.starts,0)

    def test_valid_chat_starts_then_completes_same_request(self):
        r=Runtime(delayed=True);c=self.make(r,timeout=1);s=self.service(c,True)
        self.assertEqual(s.execute('chat',request())['status'],'deferred')
        r.release.set();self.wait(c)
        value=s.execute('chat',request());self.assertEqual(value['status'],'complete')
        self.assertEqual(s.execute('chat',request()),value);self.assertEqual(r.starts,1)

    def test_resource_limit_prevents_cold_start(self):
        r=Runtime();c=self.make(r);s=self.service(c,True)
        s.resource_status=lambda: {'accepting_jobs':False}
        self.assertEqual(s.execute('chat',request()),dict(status='deferred',retry_safe=True,reason='resource_limit_exceeded'))
        self.assertEqual(r.starts,0)

    def test_failed_start_returns_explicit_error_instead_of_endless_queue(self):
        r=Runtime(fail=True);c=self.make(r,timeout=.025);s=self.service(c,True)
        self.assertEqual(s.execute('chat',request())['status'],'deferred')
        self.wait(c)
        with self.assertRaises(SlackError) as error:s.execute('chat',request())
        self.assertEqual(error.exception.code,'local_model_start_failed');self.assertEqual(r.starts,1)

    def test_invalid_request_does_not_start(self):
        r=Runtime();c=self.make(r);s=self.service(c,True)
        value=request();value['context']='x'*17000
        with self.assertRaises(SlackError):s.execute('chat',value)
        self.assertEqual(r.starts,0)

    def test_stale_ready_rechecks_actual_gpu_before_start(self):
        r=Runtime();c=self.make(r);c.state=g.ModelState.READY
        config=g.GatewayConfig(max_ram_usage_gb=10000,min_gpu_free_gb_for_start=10)
        c.start_admission=lambda:g.resource_status(for_start=True)['accepting_jobs']
        with patch.object(g,'CONTROLLER',c),patch.object(g,'CONFIG',config),patch.object(g.subprocess,'run',return_value=SimpleNamespace(stdout='900, 1000\n')):
            self.assertTrue(g.resource_status()['accepting_jobs'])  # stale status alone is insufficient
            self.assertFalse(c.request_background_start())
        self.assertEqual(r.starts,0);self.assertEqual(c.state,g.ModelState.STOPPED)

    def test_sampling_does_not_lock_out_foreground_and_rechecks_ownership(self):
        r=Runtime();c=self.make(r);sampled=threading.Event();release=threading.Event()
        def sample():
            sampled.set();self.assertTrue(release.wait(1));return True
        c.start_admission=sample;result=[]
        thread=threading.Thread(target=lambda:result.append(c.request_background_start()))
        thread.start();self.assertTrue(sampled.wait(1))
        acquired=c.condition.acquire(timeout=.1)
        try:
            self.assertTrue(acquired,'resource sampling held the foreground condition lock')
            c.queued_jobs=1
        finally:
            if acquired:c.condition.release()
            release.set();thread.join(1)
        self.assertEqual(result,[False]);self.assertEqual(r.starts,0)

    def test_failed_admission_never_starts_model(self):
        r=Runtime();c=self.make(r)
        c.start_admission=lambda:False
        self.assertFalse(c.request_background_start());self.assertEqual(r.starts,0)
