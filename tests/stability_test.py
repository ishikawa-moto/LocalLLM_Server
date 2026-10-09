"""Two-hour mTLS Gateway stability test for the final LocalBrain configuration."""
from __future__ import annotations
import pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'src'))
from site_settings import LOOPBACK, site_port

import argparse
import ctypes
import http.client
import json
import pathlib
import ssl
import subprocess
import time
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[1]
TLS = ROOT / "config" / "tls"
parser = argparse.ArgumentParser()
parser.add_argument("--minutes", type=float, default=120)
parser.add_argument("--interval", type=float, default=60)
args = parser.parse_args()
report_path = ROOT / "logs" / "stability-current.json"


class MemoryStatus(ctypes.Structure):
    _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total_phys", ctypes.c_ulonglong),
                ("avail_phys", ctypes.c_ulonglong), ("total_page", ctypes.c_ulonglong),
                ("avail_page", ctypes.c_ulonglong), ("total_virtual", ctypes.c_ulonglong),
                ("avail_virtual", ctypes.c_ulonglong), ("avail_extended", ctypes.c_ulonglong)]


def ram() -> dict:
    status = MemoryStatus(); status.length = ctypes.sizeof(status)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
    return {"load_percent": status.load, "used_gb": round((status.total_phys-status.avail_phys)/1024**3, 2)}


def tls_context() -> ssl.SSLContext:
    context = ssl.create_default_context(cafile=str(TLS / "ca.pem")); context.check_hostname = False
    context.load_cert_chain(TLS / "client.pem", TLS / "client-key.pem")
    return context


def request(method: str, path: str, body: dict | None = None, timeout: int = 300):
    connection = http.client.HTTPSConnection(LOOPBACK, site_port('gateway'), context=tls_context(), timeout=timeout)
    raw = None if body is None else json.dumps(body).encode()
    headers = {"X-LocalBrain-Request-Id": str(uuid.uuid4())}
    if raw is not None: headers["Content-Type"] = "application/json"
    try:
        connection.request(method, path, raw, headers); response = connection.getresponse(); payload = response.read()
        if response.status >= 400: raise RuntimeError(f"HTTP {response.status}: {payload[:500]!r}")
        return json.loads(payload or b"{}")
    finally: connection.close()


def listeners() -> dict[str, list[int]]:
    result = subprocess.run(["netstat.exe", "-ano", "-p", "tcp"], capture_output=True, text=True, timeout=10)
    ports = {str(site_port('llm')): [], str(site_port('proxy')): [], str(site_port('brain')): [], str(site_port('embedding')): [], str(site_port('gateway')): []}
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0] == "TCP" and parts[3] == "LISTENING":
            port = parts[1].rsplit(":", 1)[-1]
            if port in ports: ports[port].append(int(parts[4]))
    return {port: sorted(set(pids)) for port, pids in ports.items()}


started = time.time(); deadline = started + args.minutes * 60
llama_settings = json.loads((ROOT / "config" / "llama-settings.json").read_text(encoding="utf-8"))
report = {"started_at": started, "requested_minutes": args.minutes, "interval_seconds": args.interval,
          "configuration": {"gateway": "mTLS", "parallel": 1, "mtp": "off", **llama_settings}, "samples": [],
          "restart_exercised": False, "completed": False}
iteration = 0
while True:
    sample_start = time.perf_counter(); sample = {"iteration": iteration, "timestamp": time.time(), "errors": []}
    try:
        if not report["restart_exercised"] and time.time() >= started + (args.minutes * 30):
            before = request("GET", "/v1/control/status")
            stopped = request("POST", "/v1/control/stop", {})
            report["restart_exercised"] = stopped.get("state") == "STOPPED"
            sample["restart_before"] = before.get("state"); sample["restart_stop"] = stopped.get("state")
        result = request("POST", "/v1/chat/completions", {"model": "local-qwen38",
                         "messages": [{"role": "user", "content": "Reply with exactly STABLE."}],
                         "max_tokens": 256, "temperature": 0})
        answer = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        sample["response_pass"] = "STABLE" in answer
        sample["gateway"] = request("GET", "/v1/control/status")
        brain = request("POST", "/v1/brain/search", {"query": "LocalBrain", "limit": 1})
        sample["brain_pass"] = brain.get("mode") == "hybrid" and not brain.get("degraded", True)
    except Exception as error:
        sample["errors"].append(f"request: {type(error).__name__}: {error}")
    sample["listeners"] = listeners(); sample["ram"] = ram(); sample["seconds"] = time.perf_counter()-sample_start
    required_ports = (str(site_port('llm')), str(site_port('proxy')), str(site_port('brain')), str(site_port('embedding')), str(site_port('gateway')))
    sample["single_listener_pass"] = all(len(sample["listeners"][port]) == 1 for port in required_ports)
    report["samples"].append(sample); report["last_updated_at"] = time.time()
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    if time.time() >= deadline: break
    iteration += 1; time.sleep(max(0, min(args.interval, deadline-time.time())))

report["finished_at"] = time.time(); report["elapsed_minutes"] = (report["finished_at"]-started)/60
report["completed"] = True
report["all_pass"] = report["restart_exercised"] and all(not sample["errors"] and sample.get("response_pass")
    and sample.get("brain_pass") and sample.get("single_listener_pass") for sample in report["samples"])
report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
print(json.dumps({"completed": True, "all_pass": report["all_pass"], "samples": len(report["samples"])}, ensure_ascii=False))
