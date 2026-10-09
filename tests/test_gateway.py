from site_settings import LOOPBACK, site_port
import importlib.util
import pathlib
import sys
import tempfile
import threading
import time
import unittest

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "control_gateway.py"
spec = importlib.util.spec_from_file_location("control_gateway", SRC)
gateway = importlib.util.module_from_spec(spec); sys.modules[spec.name] = gateway; spec.loader.exec_module(gateway)


class Runtime:
    def __init__(self, fail=False, delayed=False):
        self.running = False; self.fail = fail; self.delayed = delayed; self.starts = 0; self.stops = 0
        self.release = threading.Event()
    def health(self, port): return self.running if port == site_port('llm') else True
    def manager(self, action, wait=True):
        if action == "StartLLM":
            self.starts += 1
            if self.delayed: self.release.wait(1)
            if not self.fail: self.running = True
        elif action == "StopLLM": self.stops += 1; self.running = False


def controller(runtime, **values):
    config = gateway.GatewayConfig(start_timeout=values.get("start_timeout", .1), idle_seconds=values.get("idle_seconds", 10),
                                   max_queue=values.get("max_queue", 16), max_start_attempts=values.get("attempts", 2),
                                   queue_timeout_seconds=values.get("queue_timeout", 900),
                                   max_ram_usage_gb=29, allowed_ips=(LOOPBACK,))
    return gateway.LifecycleController(config, runtime.manager, runtime.health,
                                       sleeper=lambda _: time.sleep(.005), base_ready=lambda: None, audit_fn=lambda *args,**kwargs: None,
                                       journal_path=values.get("journal_path"))


class GatewayLifecycleTests(unittest.TestCase):
    def test_stopped_request_starts_and_stop_then_restarts(self):
        runtime = Runtime(); c = controller(runtime)
        with c.job("request-0001"): self.assertEqual(c.state, gateway.ModelState.BUSY)
        self.assertEqual(runtime.starts, 1); self.assertTrue(c.stop("test"))
        with c.job("request-0002"): pass
        self.assertEqual(runtime.starts, 2)

    def test_two_requests_during_start_only_start_once(self):
        runtime = Runtime(delayed=True); c = controller(runtime, start_timeout=2)
        entered = []; errors = []
        def work(request_id):
            try:
                with c.job(request_id): entered.append(request_id)
            except Exception as exc: errors.append(exc)
        first = threading.Thread(target=work, args=("request-0001",)); second = threading.Thread(target=work, args=("request-0002",))
        first.start(); time.sleep(.03); second.start(); time.sleep(.03); runtime.release.set()
        first.join(2); second.join(2)
        self.assertFalse(errors); self.assertEqual(set(entered), {"request-0001", "request-0002"}); self.assertEqual(runtime.starts, 1)

    def test_start_failure_stops_after_two_attempts(self):
        runtime = Runtime(fail=True); c = controller(runtime, start_timeout=.03, attempts=2)
        with self.assertRaises(ConnectionError):
            with c.job("request-0001"): pass
        self.assertEqual(runtime.starts, 2); self.assertEqual(c.state, gateway.ModelState.FAILED)

    def test_duplicate_request_is_rejected(self):
        runtime = Runtime(); c = controller(runtime)
        with c.job("request-0001"):
            with self.assertRaises(RuntimeError):
                with c.job("request-0001"): pass

    def test_idle_stop_and_heartbeat_does_not_extend_idle(self):
        runtime = Runtime(); runtime.running = True; c = controller(runtime, idle_seconds=.02)
        time.sleep(.08); c.heartbeat(); self.assertTrue(c.idle_once()); self.assertEqual(runtime.stops, 1)

    def test_idle_does_not_stop_active_job(self):
        runtime = Runtime(); c = controller(runtime, idle_seconds=.01)
        with c.job("request-0001"):
            time.sleep(.02); self.assertFalse(c.idle_once()); self.assertTrue(runtime.running)

    def test_queue_limit(self):
        runtime = Runtime(delayed=True); c = controller(runtime, max_queue=1, start_timeout=1)
        def run():
            with c.job("request-0001"): pass
        worker = threading.Thread(target=run); worker.start(); time.sleep(.03)
        with self.assertRaises(OverflowError):
            with c.job("request-0002"): pass
        runtime.release.set(); worker.join(2)

    def test_duplicate_is_durable_across_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = pathlib.Path(directory) / "jobs.sqlite3"
            runtime = Runtime(); first = controller(runtime, journal_path=journal)
            with first.job("critic-job-0001", role="critic"): pass
            restarted = controller(runtime, journal_path=journal)
            self.assertEqual(restarted.job_status("critic-job-0001")["state"], "completed")
            with self.assertRaisesRegex(RuntimeError, "duplicate"):
                with restarted.job("critic-job-0001", role="critic"): pass

    def test_unfinished_job_is_reported_interrupted_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = pathlib.Path(directory) / "jobs.sqlite3"
            runtime = Runtime(); runtime.running = True
            first = controller(runtime, journal_path=journal)
            with first.job("critic-job-0003", role="critic"):
                restarted = controller(runtime, journal_path=journal)
                self.assertEqual(restarted.job_status("critic-job-0003")["state"], "interrupted")

    def test_cancel_queued_request(self):
        runtime = Runtime(); runtime.running = True
        c = controller(runtime)
        entered = threading.Event(); release = threading.Event(); errors = []
        def first():
            with c.job("actor-job-0001"):
                entered.set(); release.wait(2)
        def second():
            try:
                with c.job("critic-job-0002", role="critic"): pass
            except Exception as exc: errors.append(exc)
        one = threading.Thread(target=first); two = threading.Thread(target=second)
        one.start(); self.assertTrue(entered.wait(1)); two.start()
        for _ in range(100):
            if c.snapshot()["queued_jobs"] == 1: break
            time.sleep(.01)
        self.assertTrue(c.cancel("critic-job-0002"))
        two.join(2); release.set(); one.join(2)
        self.assertEqual(len(errors), 1); self.assertIsInstance(errors[0], InterruptedError)
        self.assertEqual(c.snapshot()["queued_jobs"], 0)

    def test_queue_wait_times_out(self):
        runtime = Runtime(); runtime.running = True
        c = controller(runtime, queue_timeout=.03)
        entered = threading.Event(); release = threading.Event()
        def first():
            with c.job("actor-job-0001"):
                entered.set(); release.wait(2)
        one = threading.Thread(target=first); one.start(); self.assertTrue(entered.wait(1))
        with self.assertRaises(TimeoutError):
            with c.job("critic-job-0002", role="critic"): pass
        release.set(); one.join(2)

    def test_cancel_active_request_closes_upstream_connection(self):
        runtime = Runtime(); runtime.running = True
        c = controller(runtime)
        class Connection:
            sock = None
            closed = False
            def close(self): self.closed = True
        connection = Connection()
        with c.job("actor-job-0004"):
            c.register_connection("actor-job-0004", connection)
            self.assertTrue(c.cancel("actor-job-0004"))
            self.assertTrue(connection.closed)
        self.assertIsNone(c.snapshot()["active_task"])

    def test_critic_prompt_rejects_conversation_history(self):
        valid = b'{"model":"local-qwen38","messages":[{"role":"user","content":"review this diff"}]}'
        gateway.validate_critic_prompt("/v1/chat/completions", valid)
        with self.assertRaises(ValueError):
            gateway.validate_critic_prompt("/v1/chat/completions", b'{"messages":[{"role":"user"},{"role":"assistant"}]}')
        with self.assertRaises(ValueError):
            gateway.validate_critic_prompt("/v1/responses", b'{"input":"review","previous_response_id":"actor-response"}')
        with self.assertRaises(ValueError):
            gateway.validate_critic_prompt("/v1/responses", b'{"input":"review","instructions":"actor history"}')

    def test_telemetry_headers_are_bounded_and_validated(self):
        metadata = gateway.request_metadata({"X-LocalBrain-Task-Id": "task-001",
                                             "X-LocalBrain-Request-Role": "critic",
                                             "X-LocalBrain-Risk-Level": "high",
                                             "X-LocalBrain-Session-Id": "session-001",
                                             "X-LocalBrain-Trace-Id": "trace-001"})
        self.assertEqual(set(metadata), {"task_id", "request_role", "risk_level", "session_id", "trace_id"})
        with self.assertRaises(ValueError):
            gateway.request_metadata({"X-LocalBrain-Trace-Id": "a\nsecret"})


if __name__ == "__main__": unittest.main()
