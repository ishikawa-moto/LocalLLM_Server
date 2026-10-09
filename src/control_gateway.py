"""mTLS control gateway for LocalBrain model lifecycle and knowledge access."""
from __future__ import annotations
from site_settings import ANY_ADDRESS, LOOPBACK, site_port

import contextlib
import ctypes
import datetime as dt
import http.client
import json
import os
import re
import socket
import sqlite3
import ssl
import subprocess
import threading
import time
import urllib.parse
import uuid
from dataclasses import dataclass
from enum import Enum
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from site_settings import site_value

ROOT = Path(__file__).resolve().parents[1]
APPROVAL_OWNER = site_value("host_owner", "ClientPC-windows-host", ROOT)
MANAGER = ROOT / "Manage-LocalBrain.ps1"
TLS = ROOT / "config" / "tls"
LOG = ROOT / "logs" / "audit.jsonl"
REQUEST_ID = re.compile(r"^[A-Za-z0-9_.:-]{8,128}$")
META_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
JOBS = ROOT / "runtime" / "gateway-jobs.sqlite3"
BASE_LOCK = threading.Lock()
AUDIT_LOCK = threading.Lock()
LIBRARIAN_LOCK = threading.Lock()
_settings_path = ROOT / "config" / "gateway.json"
_settings = json.loads(_settings_path.read_text(encoding="utf-8")) if _settings_path.exists() else {}
_brain_settings_path = ROOT / "config" / "secondbrain.json"
_brain_settings = json.loads(_brain_settings_path.read_text(encoding="utf-8")) if _brain_settings_path.exists() else {}


def setting(name: str, environment: str, default):
    value = os.environ.get(environment, _settings.get(name, default))
    return type(default)(value)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def audit(event: str, **fields) -> None:
    """Append metadata only. Callers must never pass request bodies or credentials."""
    LOG.parent.mkdir(parents=True, exist_ok=True)
    record = {"timestamp": utc_now(), "event": event, **fields}
    with AUDIT_LOCK:
        with LOG.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")


def manager(action: str, wait: bool = True):
    command = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(MANAGER), "-Action", action]
    if not wait:
        subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
        return None
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=90)
    if result.returncode:
        detail = (result.stderr or result.stdout or f"exit code {result.returncode}").strip()
        raise RuntimeError(detail[-1000:])
    return result


def healthy(port: int) -> bool:
    try:
        connection = http.client.HTTPConnection(LOOPBACK, port, timeout=2)
        connection.request("GET", "/health")
        ok = connection.getresponse().status == 200
        connection.close()
        return ok
    except OSError:
        return False


def listening(port: int) -> bool:
    import socket
    try:
        with socket.create_connection((LOOPBACK, port), timeout=1):
            return True
    except OSError:
        return False


class ModelState(str, Enum):
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    READY = "READY"
    BUSY = "BUSY"
    STOPPING = "STOPPING"
    FAILED = "FAILED"


@dataclass(frozen=True)
class GatewayConfig:
    port: int = setting("port", "LOCALBRAIN_GATEWAY_PORT", site_port('gateway'))
    idle_seconds: int = setting("idle_seconds", "LOCALBRAIN_IDLE_SECONDS", 1800)
    start_timeout: int = setting("start_timeout", "LOCALBRAIN_START_TIMEOUT", 180)
    max_queue: int = setting("max_queue", "LOCALBRAIN_MAX_QUEUE", 16)
    queue_timeout_seconds: int = 900
    max_start_attempts: int = setting("max_start_attempts", "LOCALBRAIN_MAX_START_ATTEMPTS", 2)
    max_ram_usage_gb: float = setting("max_ram_usage_gb", "LOCALBRAIN_MAX_RAM_USAGE_GB", 29.0)
    min_gpu_free_gb_for_start: float = setting("min_gpu_free_gb_for_start", "LOCALBRAIN_MIN_GPU_FREE_GB_FOR_START", 8.5)
    allowed_ips: tuple[str, ...] = tuple(x.strip() for x in os.environ.get("LOCALBRAIN_ALLOWED_IPS", "").split(",") if x.strip()) if os.environ.get("LOCALBRAIN_ALLOWED_IPS") else tuple(_settings.get("allowed_ips", [site_value("client_address", "localbrain-client", ROOT),LOOPBACK]))


class LifecycleController:
    """Serializes model starts and inference while exposing observable state."""
    def __init__(self, config: GatewayConfig, run_manager: Callable = manager,
                 health: Callable[[int], bool] = healthy, clock: Callable[[], float] = time.monotonic,
                 sleeper: Callable[[float], None] = time.sleep, base_ready: Callable[[], None] | None = None,
                 audit_fn: Callable = audit, journal_path: Path | None = None):
        self.config = config
        self.run_manager = run_manager
        self.health = health
        self.clock = clock
        self.sleeper = sleeper
        self.base_ready = base_ready
        self.audit = audit_fn
        self.condition = threading.Condition(threading.RLock())
        self.inference = threading.Semaphore(1)
        self.state = ModelState.READY if health(site_port('llm')) else ModelState.STOPPED
        self.active_jobs = 0
        self.queued_jobs = 0
        self.inflight_ids: set[str] = set()
        self.cancelled_ids: set[str] = set()
        self.active_task: dict | None = None
        self.active_connections: dict[str, http.client.HTTPConnection] = {}
        self.journal_path = journal_path
        if journal_path is not None:
            journal_path.parent.mkdir(parents=True, exist_ok=True)
            with self._journal() as db:
                db.execute("CREATE TABLE IF NOT EXISTS jobs (request_id TEXT PRIMARY KEY, role TEXT, state TEXT NOT NULL, updated_at TEXT NOT NULL)")
        self.last_model_request = clock()
        self.last_heartbeat: float | None = None
        self.last_error: str | None = None

    def snapshot(self) -> dict:
        with self.condition:
            return {"state": self.state.value, "active_jobs": self.active_jobs, "queued_jobs": self.queued_jobs,
                    "active_task": self.active_task,
                    "last_model_request_age_seconds": max(0, round(self.clock() - self.last_model_request, 3)),
                    "last_heartbeat_age_seconds": None if self.last_heartbeat is None else max(0, round(self.clock() - self.last_heartbeat, 3)),
                    "last_error": self.last_error}

    @contextlib.contextmanager
    def _journal(self):
        connection = sqlite3.connect(self.journal_path, timeout=5)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _record(self, request_id: str, role: str | None, state: str, new: bool = False) -> None:
        if self.journal_path is None:
            return
        with self._journal() as db:
            if new:
                db.execute("INSERT INTO jobs VALUES (?,?,?,?)", (request_id, role, state, utc_now()))
            else:
                db.execute("UPDATE jobs SET state=?, updated_at=? WHERE request_id=?", (state, utc_now(), request_id))

    def job_status(self, request_id: str) -> dict | None:
        if self.journal_path is None:
            return None
        with self._journal() as db:
            row = db.execute("SELECT role,state,updated_at FROM jobs WHERE request_id=?", (request_id,)).fetchone()
        if row is None:
            return None
        with self.condition:
            state = "interrupted" if row[1] in ("queued", "active") and request_id not in self.inflight_ids else row[1]
        return {"request_id": request_id, "role": row[0], "state": state, "updated_at": row[2]}

    def cancel(self, request_id: str) -> bool:
        with self.condition:
            if request_id not in self.inflight_ids:
                return False
            self.cancelled_ids.add(request_id)
            connection = self.active_connections.get(request_id)
            self.condition.notify_all()
        if connection is not None:
            try:
                if connection.sock is not None:
                    connection.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()
        self.audit("request_cancelled", request_id=request_id)
        return True

    def register_connection(self, request_id: str, connection: http.client.HTTPConnection) -> None:
        with self.condition:
            self.active_connections[request_id] = connection
            if request_id in self.cancelled_ids:
                connection.close()

    def heartbeat(self) -> None:
        with self.condition:
            self.last_heartbeat = self.clock()

    def ensure_base(self) -> None:
        if self.base_ready is not None:
            self.base_ready(); return
        if self.health(site_port('brain')) and listening(site_port('proxy')): return
        with BASE_LOCK:
            if not self.health(site_port('brain')): self.run_manager("StartBrain", wait=False)
            if not self.health(site_port('embedding')): self.run_manager("StartEmbedding", wait=False)
            if not listening(site_port('proxy')): self.run_manager("StartProxy", wait=False)
            end=self.clock()+15
            while self.clock()<end:
                if self.health(site_port('brain')) and listening(site_port('proxy')): return
                self.sleeper(.25)
            raise ConnectionError("LocalBrain internal services failed readiness")

    def ensure_ready(self) -> bool:
        self.ensure_base()
        with self.condition:
            if self.health(site_port('llm')):
                self.state = ModelState.READY if self.active_jobs == 0 else ModelState.BUSY
                return True
            if self.state == ModelState.STARTING:
                end = self.clock() + self.config.start_timeout
                while self.state == ModelState.STARTING and self.clock() < end:
                    self.condition.wait(timeout=min(1, max(0.01, end - self.clock())))
                return self.state in (ModelState.READY, ModelState.BUSY)
            self.state = ModelState.STARTING
            self.last_error = None

        success = False; error = None
        for attempt in range(1, self.config.max_start_attempts + 1):
            self.audit("model_start_attempt", attempt=attempt)
            try:
                self.run_manager("StartLLM", wait=False)
                end = self.clock() + self.config.start_timeout
                while self.clock() < end:
                    if self.health(site_port('llm')): success = True; break
                    self.sleeper(min(1, max(0.01, end - self.clock())))
            except Exception as exc:
                error = (str(exc).strip() or type(exc).__name__)[:1000]
            if success: break
            try: self.run_manager("StopLLM", wait=True)
            except Exception: pass

        with self.condition:
            self.state = ModelState.READY if success else ModelState.FAILED
            self.last_error = None if success else (error or "model readiness timeout")
            self.condition.notify_all()
        self.audit("model_started" if success else "model_start_failed", error=self.last_error)
        return success

    def stop(self, reason: str) -> bool:
        with self.condition:
            if self.active_jobs or self.queued_jobs: return False
            if not self.health(site_port('llm')): self.state = ModelState.STOPPED; return True
            self.state = ModelState.STOPPING
        try:
            self.run_manager("StopLLM", wait=True); stopped = not self.health(site_port('llm'))
        except Exception as exc:
            stopped = False; self.last_error = (str(exc).strip() or type(exc).__name__)[:1000]
        with self.condition:
            self.state = ModelState.STOPPED if stopped else ModelState.FAILED
            self.condition.notify_all()
        self.audit("model_stopped" if stopped else "model_stop_failed", reason=reason)
        return stopped

    @contextlib.contextmanager
    def job(self, request_id: str, role: str | None = None, metadata: dict | None = None):
        if not REQUEST_ID.fullmatch(request_id): raise ValueError("invalid request ID")
        with self.condition:
            if request_id in self.inflight_ids: raise RuntimeError("duplicate request ID")
            if self.queued_jobs >= self.config.max_queue: raise OverflowError("request queue is full")
            try: self._record(request_id, role, "queued", new=True)
            except sqlite3.IntegrityError: raise RuntimeError("duplicate request ID") from None
            self.inflight_ids.add(request_id); self.queued_jobs += 1; self.last_model_request = self.clock()
        self.audit("request_queued", request_id=request_id, **(metadata or {}))
        acquired = False; active_started = False
        terminal = "failed"
        try:
            if not self.ensure_ready(): raise ConnectionError(self.last_error or "model failed to start")
            deadline = self.clock() + self.config.queue_timeout_seconds
            while not acquired:
                if request_id in self.cancelled_ids:
                    terminal = "cancelled"; raise InterruptedError("request cancelled")
                remaining = deadline - self.clock()
                if remaining <= 0:
                    terminal = "timed_out"; raise TimeoutError("queue wait timed out")
                acquired = self.inference.acquire(timeout=min(.2, remaining))
            if not self.health(site_port('llm')) and not self.ensure_ready(): raise ConnectionError(self.last_error or "model failed to restart")
            with self.condition:
                if request_id in self.cancelled_ids:
                    terminal = "cancelled"; raise InterruptedError("request cancelled")
                self._record(request_id, role, "active")
                self.queued_jobs -= 1; self.active_jobs += 1; self.state = ModelState.BUSY; self.last_model_request = self.clock()
                self.active_task = {"request_id": request_id, "role": role, "task_id": (metadata or {}).get("task_id")}
                active_started = True
            self.audit("request_started", request_id=request_id)
            yield
            terminal = "cancelled" if request_id in self.cancelled_ids else "completed"
        finally:
            try:
                with self.condition:
                    if request_id in self.cancelled_ids: terminal = "cancelled"
                    if active_started:
                        self.active_jobs -= 1; self.last_model_request = self.clock()
                        self.active_task = None
                        if self.state != ModelState.FAILED: self.state = ModelState.READY if self.health(site_port('llm')) else ModelState.STOPPED
                    else: self.queued_jobs = max(0, self.queued_jobs - 1)
                    self.inflight_ids.discard(request_id); self.cancelled_ids.discard(request_id)
                    self.active_connections.pop(request_id, None)
                    self._record(request_id, role, terminal)
                    self.condition.notify_all()
            finally:
                if acquired: self.inference.release()
            if active_started: self.audit("request_finished", request_id=request_id)

    def idle_once(self) -> bool:
        with self.condition:
            eligible = (self.state == ModelState.READY and self.active_jobs == 0 and self.queued_jobs == 0
                        and self.clock() - self.last_model_request >= self.config.idle_seconds)
        return self.stop("idle_timeout") if eligible else False


CONFIG = GatewayConfig()
CONTROLLER = LifecycleController(CONFIG)


def resource_status() -> dict:
    class MemoryStatus(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total_phys", ctypes.c_ulonglong),
                    ("avail_phys", ctypes.c_ulonglong), ("total_page", ctypes.c_ulonglong), ("avail_page", ctypes.c_ulonglong),
                    ("total_virtual", ctypes.c_ulonglong), ("avail_virtual", ctypes.c_ulonglong), ("avail_extended", ctypes.c_ulonglong)]
    status = MemoryStatus(); status.length = ctypes.sizeof(MemoryStatus)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
    used = (status.total_phys - status.avail_phys) / 1024 ** 3
    gpu = {"available": False, "used_gb": None, "total_gb": None, "free_gb": None}
    try:
        result = subprocess.run(
            ["nvidia-smi.exe", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3, check=True, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        used_mb, total_mb = (int(value.strip()) for value in result.stdout.splitlines()[0].split(","))
        gpu = {"available": True, "used_gb": round(used_mb / 1024, 2), "total_gb": round(total_mb / 1024, 2),
               "free_gb": round((total_mb - used_mb) / 1024, 2)}
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        pass
    state = CONTROLLER.snapshot()["state"]
    gpu_ok = state in (ModelState.READY.value, ModelState.BUSY.value) or not gpu["available"] or gpu["free_gb"] >= CONFIG.min_gpu_free_gb_for_start
    return {"ram_used_gb": round(used, 2), "ram_total_gb": round(status.total_phys / 1024 ** 3, 2),
            "ram_limit_gb": CONFIG.max_ram_usage_gb, "gpu": gpu,
            "min_gpu_free_gb_for_start": CONFIG.min_gpu_free_gb_for_start,
            "accepting_jobs": used <= CONFIG.max_ram_usage_gb and gpu_ok}


def load_internal_keys() -> dict:
    return json.loads((ROOT / "config" / "api-keys.json").read_text(encoding="utf-8"))


def request_metadata(headers) -> dict:
    fields = {"task_id": "X-LocalBrain-Task-Id", "request_role": "X-LocalBrain-Request-Role",
              "risk_level": "X-LocalBrain-Risk-Level", "session_id": "X-LocalBrain-Session-Id",
              "trace_id": "X-LocalBrain-Trace-Id"}
    result = {}
    for key, header in fields.items():
        item = headers.get(header)
        if item is not None:
            if not META_ID.fullmatch(item): raise ValueError(f"invalid {key}")
            result[key] = item
    if result.get("request_role") not in (None, "actor", "critic"):
        raise ValueError("invalid request_role")
    return result


def validate_critic_prompt(path: str, body: bytes) -> None:
    """Critic requests must have one explicit user prompt and no conversation handle."""
    try: value = json.loads(body)
    except (ValueError, UnicodeDecodeError): raise ValueError("invalid Critic JSON") from None
    if not isinstance(value, dict) or any(k in value for k in ("previous_response_id", "conversation", "tools", "instructions")):
        raise ValueError("Critic request contains conversation state or tools")
    if path.endswith("chat/completions"):
        messages = value.get("messages")
        if not isinstance(messages, list) or len(messages) != 1 or not isinstance(messages[0], dict) or messages[0].get("role") != "user":
            raise ValueError("Critic requires exactly one user message")
    else:
        items = value.get("input")
        if isinstance(items, str) and items:
            return
        if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict) or items[0].get("role") != "user":
            raise ValueError("Critic requires exactly one user input")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"; server_version = "LocalBrainGateway/2"
    def log_message(self, fmt, *args): print(f"{self.client_address[0]} {fmt % args}", flush=True)
    def allowed(self) -> bool:
        cert = self.connection.getpeercert(); allowed = self.client_address[0] in CONFIG.allowed_ips and bool(cert)
        if not allowed: audit("authentication_failure", source_ip=self.client_address[0], certificate_present=bool(cert))
        return allowed
    def send_json(self, status: int, value: dict) -> None:
        raw = json.dumps(value, ensure_ascii=False).encode("utf-8"); self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store"); self.send_header("Connection", "close"); self.end_headers()
        self.wfile.write(raw); self.close_connection = True
    def body(self) -> bytes:
        if self.headers.get("Transfer-Encoding"): raise ValueError("transfer encoding unsupported")
        size = int(self.headers.get("Content-Length", "0"))
        if size < 0 or size > 10 * 1024 * 1024: raise ValueError("invalid request size")
        return self.rfile.read(size) if size else b""
    def do_GET(self):
        if not self.allowed(): return self.send_json(403, {"error": "client denied"})
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path == "/v1/control/job-status":
            request_ids = urllib.parse.parse_qs(parsed.query).get("request_id", [])
            if len(request_ids) != 1 or not REQUEST_ID.fullmatch(request_ids[0]):
                return self.send_json(400, {"error": "invalid request ID"})
            result = CONTROLLER.job_status(request_ids[0])
            return self.send_json(200 if result else 404, result or {"error": "job not found"})
        if self.path in ("/health", "/v1/control/status", "/control/status"):
            model_ready = healthy(site_port('llm'))
            result = CONTROLLER.snapshot(); result.update({"gateway": "ok", "model_server": model_ready,
                                                           "model_loaded": model_ready, "brain": healthy(site_port('brain')),
                                                           "proxy": listening(site_port('proxy')), "queue_depth": result["queued_jobs"],
                                                           "resources": resource_status()})
            return self.send_json(200, result)
        if self.path == "/v1/brain/status": return self.proxy_internal(site_port('brain'), "/health", model=False)
        if self.path in ("/v1/models", "/models"): return self.proxy_internal(site_port('proxy'), "/v1/models", model=True)
        return self.send_json(404, {"error": "endpoint not found"})
    def do_POST(self):
        if not self.allowed(): return self.send_json(403, {"error": "client denied"})
        if self.path == "/v1/control/cancel":
            try:
                value = json.loads(self.body())
                request_id = value.get("request_id") if isinstance(value, dict) else None
                if not isinstance(request_id, str) or not REQUEST_ID.fullmatch(request_id):
                    raise ValueError("invalid request ID")
            except (ValueError, UnicodeDecodeError) as exc:
                return self.send_json(400, {"error": str(exc)})
            found = CONTROLLER.cancel(request_id)
            return self.send_json(202 if found else 404, {"cancelled": found, "request_id": request_id})
        controls = {"/v1/control/start":"start", "/control/start":"start", "/v1/control/stop":"stop", "/control/stop":"stop",
                    "/v1/control/heartbeat":"heartbeat", "/control/heartbeat":"heartbeat", "/v1/control/client-shutdown":"shutdown"}
        if self.path in controls:
            action = controls[self.path]
            if action == "heartbeat": CONTROLLER.heartbeat(); return self.send_json(200, {"status":"ok"})
            if action == "shutdown": CONTROLLER.heartbeat(); audit("client_shutdown", source_ip=self.client_address[0]); return self.send_json(200, {"status":"acknowledged"})
            if action == "start":
                ok = CONTROLLER.ensure_ready(); return self.send_json(200 if ok else 503, CONTROLLER.snapshot())
            ok = CONTROLLER.stop("explicit_request"); return self.send_json(200 if ok else 409, CONTROLLER.snapshot())
        model_routes = {"/v1/model/responses":"/v1/responses", "/v1/responses":"/v1/responses",
                        "/v1/model/chat/completions":"/v1/chat/completions", "/v1/chat/completions":"/v1/chat/completions"}
        critic_routes = {"/v1/model/critic/responses":"/v1/responses",
                         "/v1/model/critic/chat/completions":"/v1/chat/completions"}
        if self.path in model_routes or self.path in critic_routes:
            resources = resource_status()
            if not resources["accepting_jobs"]: return self.send_json(503, {"error":"RAM usage exceeds configured limit", "resources":resources})
            critic = self.path in critic_routes
            return self.proxy_internal(site_port('proxy'), (critic_routes if critic else model_routes)[self.path], model=True, critic=critic)
        apply_routes = {"/v1/brain/apply-decision":"/apply/decision", "/v1/brain/apply-human-decision":"/apply/human-decision", "/v1/brain/apply-supersede":"/apply/supersede",
                        "/v1/brain/apply-merge":"/apply/merge", "/v1/brain/apply-snapshot":"/apply-snapshot"}
        if self.path in apply_routes:
            # Existing mTLS/IP guard above has already authenticated this connection.
            try:
                body = self.body()
            except (ValueError, UnicodeError):
                return self.send_json(400, {"status":"rejected", "reason_code":"malformed_request", "reason":"Invalid request framing."})
            return self.proxy_internal(site_port('brain'), apply_routes[self.path], model=False, body=body)
        brain_routes = {"/v1/brain/search":"/search", "/brain/search":"/search", "/v1/brain/context":"/context", "/brain/context":"/context",
                        "/v1/brain/get":"/get", "/brain/get":"/get", "/v1/brain/find-decisions":"/search", "/v1/brain/find-incidents":"/search",
                        "/v1/brain/find-entity":"/find-entity", "/v1/brain/find-concept":"/find-concept", "/v1/brain/find-synthesis":"/find-synthesis",
                        "/v1/brain/sources":"/sources", "/v1/brain/history":"/history", "/v1/brain/superseded":"/superseded",
                        "/v1/brain/proposal":"/proposal", "/v1/brain/propose-bound-decision":"/propose-bound-decision",
                         "/v1/brain/proposals":"/propose", "/brain/propose":"/propose", "/v1/brain/propose-decision":"/propose",
                        "/v1/brain/propose-merge":"/propose", "/v1/brain/propose-supersede":"/propose"}
        if self.path in brain_routes:
            body = self.body()
            if self.path.endswith("find-decisions") or self.path.endswith("find-incidents"):
                value = json.loads(body or b"{}"); value["folder"] = "decisions" if self.path.endswith("find-decisions") else "incidents"; body = json.dumps(value).encode()
            proposal_kinds={"/v1/brain/propose-decision":"decision","/v1/brain/propose-merge":"merge","/v1/brain/propose-supersede":"supersede"}
            if self.path in proposal_kinds:
                value=json.loads(body or b"{}"); value["kind"]=proposal_kinds[self.path]; body=json.dumps(value).encode()
            return self.proxy_internal(site_port('brain'), brain_routes[self.path], model=False, body=body)
        return self.send_json(404, {"error":"endpoint not found"})
    def proxy_internal(self, port: int, path: str, model: bool, body: bytes | None = None, critic: bool = False):
        explicit_id = self.headers.get("X-LocalBrain-Request-Id")
        request_id = explicit_id or str(uuid.uuid4())
        response_started = False
        role = None
        try:
            if not REQUEST_ID.fullmatch(request_id): return self.send_json(400, {"error":"invalid request ID"})
            if body is None: body = self.body()
            metadata = request_metadata(self.headers) if model else {}
            role = "critic" if critic else ("actor" if model else None)
            if critic:
                if not explicit_id: raise ValueError("Critic requires X-LocalBrain-Request-Id")
                validate_critic_prompt(path, body)
            keys = load_internal_keys()
            token = keys["llm"] if model else (keys["propose"] if path in ("/propose","/propose-bound-decision") or path.startswith("/apply/") else keys["read"])
            headers = {"Authorization":"Bearer "+token, "X-LocalBrain-Request-Id":request_id, "Content-Length":str(len(body))}
            if path.startswith("/apply/"):
                import hashlib
                headers["X-LocalBrain-Apply-Client"] = self.client_address[0]
                if self.headers.get("X-LocalBrain-Approval-Owner") == APPROVAL_OWNER:
                    headers["X-LocalBrain-Approval-Owner"] = APPROVAL_OWNER
                headers["X-LocalBrain-Apply-Certificate"] = hashlib.sha256(self.connection.getpeercert(binary_form=True)).hexdigest()
            if self.headers.get("Content-Type"): headers["Content-Type"] = self.headers["Content-Type"]
            context = CONTROLLER.job(request_id, role=role, metadata=metadata) if model else contextlib.nullcontext()
            with context:
                CONTROLLER.ensure_base(); connection = http.client.HTTPConnection(LOOPBACK, port, timeout=900)
                try:
                    if model:
                        CONTROLLER.register_connection(request_id, connection)
                        if request_id in CONTROLLER.cancelled_ids: raise InterruptedError("request cancelled")
                    connection.request(self.command, path, body=body, headers=headers); response = connection.getresponse(); self.send_response(response.status)
                    for key, value in response.getheaders():
                        if key.lower() not in {"connection", "transfer-encoding", "content-length"}: self.send_header(key, value)
                    self.send_header("Connection", "close"); self.end_headers(); response_started = True
                    while chunk := response.read1(65536): self.wfile.write(chunk); self.wfile.flush()
                finally: connection.close(); self.close_connection = True
        except ValueError as exc:
            if not response_started: self.send_json(400, {"error":str(exc), "request_id":request_id})
        except RuntimeError as exc:
            if not response_started: self.send_json(409, {"error":str(exc), "request_id":request_id})
        except OverflowError as exc:
            if not response_started: self.send_json(429, {"error":str(exc), "request_id":request_id})
        except TimeoutError as exc:
            if not response_started: self.send_json(408, {"error":str(exc), "request_id":request_id})
        except InterruptedError as exc:
            if not response_started: self.send_json(409, {"error":str(exc), "request_id":request_id})
        except Exception as exc:
            audit("request_failed", request_id=request_id, error=type(exc).__name__)
            if not response_started:
                self.send_json(503, {"error":"LocalBrain request failed", "cause":type(exc).__name__, "request_id":request_id,
                                     "timestamp":utc_now(), "retry":not critic, "log":str(LOG)})
        finally: self.close_connection = True


def pending_librarian_items() -> int:
    try:
        connection=http.client.HTTPConnection(LOOPBACK,site_port('brain'),timeout=3); connection.request("GET","/health")
        response=connection.getresponse(); value=json.loads(response.read()); connection.close()
        return int(value.get("librarian",{}).get("pending",0)) if response.status==200 else 0
    except Exception: return 0


def idle_watch() -> None:
    last_librarian=0.0
    while True:
        time.sleep(min(30,max(1,CONFIG.idle_seconds/2)))
        snapshot=CONTROLLER.snapshot(); features=_brain_settings.get("features",{})
        librarian_idle=max(60,int(_brain_settings.get("librarian_idle_seconds",300)))
        eligible=(features.get("idle_librarian",False) and snapshot["state"] in (ModelState.READY.value,ModelState.STOPPED.value)
                  and snapshot["active_jobs"]==0 and snapshot["queued_jobs"]==0
                  and snapshot["last_model_request_age_seconds"]>=librarian_idle and time.monotonic()-last_librarian>=600)
        if eligible and LIBRARIAN_LOCK.acquire(blocking=False):
            try:
                manager("RunLibrarian",wait=False); last_librarian=time.monotonic(); audit("idle_librarian_started")
            finally: LIBRARIAN_LOCK.release()
        CONTROLLER.idle_once()


def main() -> None:
    global CONTROLLER
    CONTROLLER = LifecycleController(CONFIG, journal_path=JOBS)
    server = ThreadingHTTPServer((ANY_ADDRESS, CONFIG.port), Handler); server.daemon_threads = True
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH); context.load_cert_chain(TLS / "server.pem", TLS / "server-key.pem")
    context.load_verify_locations(TLS / "ca.pem"); context.verify_mode = ssl.CERT_REQUIRED; server.socket = context.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=idle_watch, daemon=True).start(); threading.Thread(target=CONTROLLER.ensure_base, daemon=True).start()
    audit("gateway_started", port=CONFIG.port, allowed_ips=CONFIG.allowed_ips)
    print(f"LocalBrain Control Gateway listening on https://{ANY_ADDRESS}:{CONFIG.port}", flush=True); server.serve_forever()


if __name__ == "__main__": main()
