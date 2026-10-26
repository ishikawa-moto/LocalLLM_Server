"""Only public temporary stores and injected services; no installed runtime/model."""
import tempfile
import json
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import control_gateway as g
from slack_gateway import SlackService, Deferred, SlackError, DEFER_REASONS, RESTRICTION
from test_gateway import Runtime
from test_slack import request, PIN


class AdmissionTests(unittest.TestCase):
    def test_shared_wire_contract(self):
        contract = json.loads((Path(__file__).parent/'fixtures/slack-admission-v1.json').read_text())
        self.assertEqual(contract['schema'], 'localbrain.slack-admission.v1')
        self.assertEqual(set(contract['reasons']), DEFER_REASONS)
        self.assertEqual(contract['restriction'], RESTRICTION)

    def test_status_review_and_disabled_chat_do_not_start_base(self):
        base_calls = []
        c, r, _ = self.setup_controller(base_ready=lambda: base_calls.append(True))
        s, calls = self.service(c)
        s.capabilities()
        s.status(dict(operation='chat', request_id='public_unknown_001', session_id='session_fixture_01', request_hash='0'*64, restriction=RESTRICTION))
        self.assertEqual(s.execute('review', request('review'))['status'], 'deferred')
        s.policy['model_cold_start'] = False
        self.assertEqual(s.execute('chat', request())['reason'], 'model_start_disabled')
        self.assertEqual((base_calls, r.starts, calls), ([], 0, []))

    def test_default_base_manager_recovers_services_in_order(self):
        from site_settings import site_port
        running = set(); actions = []
        names = {name: site_port(name) for name in ('brain', 'embedding', 'proxy', 'llm')}
        def health(port): return port in running
        def manager(action, **kwargs):
            actions.append(action)
            service = {'StartBrain': 'brain', 'StartEmbedding': 'embedding', 'StartProxy': 'proxy', 'StartLLM': 'llm'}[action]
            running.add(names[service])
        with patch.object(g, 'listening', side_effect=lambda port: port in running):
            c = g.LifecycleController(g.GatewayConfig(start_timeout=.1), manager, health,
                start_admission=lambda: True, audit_fn=lambda *a, **k: None)
            self.assertEqual(actions, [])
            self.assertTrue(c.request_background_start()); self.wait(c)
            self.assertEqual(actions, ['StartBrain', 'StartEmbedding', 'StartProxy', 'StartLLM'])
            self.assertEqual(c.state, g.ModelState.READY)

    def test_gate_closing_during_base_prevents_model_start(self):
        entered = threading.Event(); release = threading.Event(); gate = {'open': True}
        def base(): entered.set(); self.assertTrue(release.wait(1))
        with patch.object(g.startup_gate, 'opened', side_effect=lambda: gate['open']):
            c, r, events = self.setup_controller(base_ready=base)
            self.assertTrue(c.request_background_start()); self.assertTrue(entered.wait(1))
            gate['open'] = False; release.set(); self.wait(c)
        self.assertEqual(r.starts, 0)
        self.assertEqual(c.background_failure_reason, 'activation_pending')
        self.assertIn(('restricted_model_start_finished', {'reason': 'activation_pending'}), events)

    def setup_controller(self, runtime=None, base_ready=None, background_ready=None, admission=None):
        runtime = runtime or Runtime()
        events = []
        controller = g.LifecycleController(g.GatewayConfig(start_timeout=.15), runtime.manager, runtime.health,
            sleeper=lambda _: time.sleep(.002), base_ready=base_ready or (lambda: None),
            background_ready=background_ready or (lambda: True), start_admission=admission or (lambda: True),
            audit_fn=lambda event, **fields: events.append((event, fields)))
        return controller, runtime, events

    def service(self, controller):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        calls = []
        def model(*args): calls.append(True); return 'PUBLIC response'
        service = SlackService(Path(temporary.name), controller, model, 'public-client', 'public-owner',
            dict(enabled=True, host_certificate_sha256=PIN, human_apply_enabled=False, model_cold_start=True))
        return service, calls

    def wait(self, controller):
        end = time.monotonic()+2
        while controller.background_start is not None and time.monotonic()<end: time.sleep(.003)
        self.assertIsNone(controller.background_start)

    def test_authenticated_chat_recovers_base_before_model_once(self):
        runtime = Runtime(delayed=True); base = {'ready': False, 'calls': 0}
        def ensure(): base['calls'] += 1; base['ready'] = True
        c, r, events = self.setup_controller(runtime, ensure, lambda: base['ready'])
        s, calls = self.service(c)
        self.assertFalse(base['ready']); self.assertEqual(r.starts, 0)
        first = s.execute('chat', request())
        self.assertEqual(first['status'], 'deferred'); self.assertIn(first['reason'], ('base_starting', 'model_starting'))
        r.release.set(); self.wait(c)
        result = s.execute('chat', request())
        self.assertEqual(result['status'], 'complete'); self.assertEqual(s.execute('chat', request()), result)
        self.assertEqual((base['calls'], r.starts, len(calls)), (1, 1, 1))
        names = [event for event, _ in events]
        self.assertLess(names.index('restricted_base_start_ready'), names.index('restricted_model_start_requested'))

    def test_base_recovery_is_coalesced_without_foreground_lock(self):
        entered = threading.Event(); release = threading.Event(); starts = []
        def base(): starts.append(True); entered.set(); self.assertTrue(release.wait(1))
        c, r, _ = self.setup_controller(base_ready=base)
        self.assertTrue(c.request_background_start()); self.assertTrue(entered.wait(1))
        try:
            for _ in range(8): self.assertEqual(c.request_background_start_reason(), 'base_starting')
            self.assertTrue(c.condition.acquire(timeout=.1)); c.condition.release()
        finally: release.set(); self.wait(c)
        self.assertEqual((len(starts), r.starts), (1, 1))

    def test_closed_activation_gate_never_starts_base_or_model(self):
        calls = []
        with patch.object(g.startup_gate, 'opened', return_value=False):
            c, r, _ = self.setup_controller(base_ready=lambda: calls.append(True))
            self.assertEqual(c.request_background_start_reason(), 'activation_pending')
            self.assertFalse(c.request_background_start())
        self.assertEqual((calls, r.starts), ([], 0))

    def test_base_failure_is_fixed_and_not_retried_or_force_stopped(self):
        calls = []
        def base(): calls.append(True); raise OSError('PUBLIC_SECRET_NOT_FOR_LOG')
        c, r, events = self.setup_controller(base_ready=base)
        self.assertTrue(c.request_background_start()); self.wait(c)
        self.assertEqual(c.state, g.ModelState.FAILED)
        self.assertEqual(c.request_background_start_reason(), 'base_start_failed')
        for _ in range(4): self.assertFalse(c.request_background_start())
        self.assertEqual((len(calls), r.starts, r.stops), (1, 0, 0))
        self.assertNotIn('PUBLIC_SECRET', str(events)+str(c.snapshot()))

    def test_resources_are_rechecked_after_base(self):
        samples = iter((True, False))
        c, r, events = self.setup_controller(admission=lambda: next(samples))
        self.assertTrue(c.request_background_start()); self.wait(c)
        self.assertEqual(c.state, g.ModelState.STOPPED); self.assertEqual(r.starts, 0)
        self.assertEqual(c.background_failure_reason, 'resource_limit_exceeded')
        self.assertIn(('restricted_model_start_finished', {'reason': 'resource_limit_exceeded'}), events)

    def test_temporary_resource_veto_does_not_latch_model_failure(self):
        samples = iter((True, False, True, True))
        c, r, _ = self.setup_controller(admission=lambda: next(samples))
        self.assertTrue(c.request_background_start()); self.wait(c)
        self.assertEqual(c.state, g.ModelState.STOPPED); self.assertEqual(r.starts, 0)
        self.assertTrue(c.request_background_start()); self.wait(c)
        self.assertEqual(c.state, g.ModelState.READY); self.assertEqual(r.starts, 1)

    def test_deferrals_return_and_audit_fixed_reason_without_identifiers(self):
        for reason in ('foreground_busy', 'resource_limit_exceeded', 'resource_probe_failed'):
            c, r, events = self.setup_controller()
            if reason == 'foreground_busy': c.queued_jobs = 1
            elif reason == 'resource_limit_exceeded': c.start_admission = lambda: False
            else:
                def failed(): raise OSError('PUBLIC_SECRET_NOT_FOR_LOG')
                c.start_admission = failed
            s, calls = self.service(c); value = s.execute('chat', request())
            self.assertEqual(value, dict(status='deferred', retry_safe=True, reason=reason))
            self.assertEqual(events[-1], ('restricted_admission_deferred', {'operation': 'chat', 'reason': reason}))
            self.assertEqual((r.starts, calls), (0, [])); self.assertNotIn('PUBLIC_SECRET', str(events))
            with s.store.db() as db: self.assertEqual(db.execute('SELECT count(*) FROM runs').fetchone()[0], 0)

    def test_initial_resource_probe_exception_is_reasoned_without_start(self):
        c, r, events = self.setup_controller(); s, calls = self.service(c)
        s.resource_status = lambda: (_ for _ in ()).throw(OSError('PUBLIC_SECRET'))
        self.assertEqual(s.execute('chat', request()), dict(status='deferred', retry_safe=True, reason='resource_probe_failed'))
        self.assertEqual((r.starts, calls), (0, [])); self.assertNotIn('PUBLIC_SECRET', str(events))

    def test_priority_and_semaphore_admission_remain_enforced(self):
        for reason in ('foreground_busy', 'inference_busy'):
            c, r, _ = self.setup_controller(); r.running = True; c.state = g.ModelState.READY
            if reason == 'foreground_busy': c.queued_jobs = 1
            else: c.inference.acquire()
            try:
                with self.assertRaises(Deferred) as error:
                    with c.background_job(4): self.fail('Admission was bypassed')
                self.assertEqual(error.exception.reason, reason)
            finally:
                if reason == 'inference_busy': c.inference.release()

    def test_reason_text_cannot_enter_audit(self):
        c, r, events = self.setup_controller(); s, _ = self.service(c)
        self.assertEqual(s.defer('chat', 'PUBLIC_SECRET'), dict(status='deferred', retry_safe=True, reason='admission_unavailable'))
        self.assertEqual(events, [('restricted_admission_deferred', {'operation': 'chat', 'reason': 'admission_unavailable'})])
