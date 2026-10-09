"""Loopback-only compatibility proxy for Codex Responses requests.

Qwen's bundled chat template requires every system/developer instruction to be
at the start. Codex may place developer messages in ``input`` in addition to
the top-level ``instructions`` field. This proxy merges those leading-policy
messages into ``instructions`` and otherwise forwards the request unchanged.
"""
from __future__ import annotations
from site_settings import LOOPBACK, site_port

import argparse
import http.client
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _text(content):
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for item in content:
        if isinstance(item, dict):
            value = item.get("text")
            if isinstance(value, str):
                parts.append(value)
    return "\n".join(parts)


def normalize(body: dict) -> dict:
    items = body.get("input")
    if not isinstance(items, list):
        return body
    policy = []
    kept = []
    for item in items:
        if isinstance(item, dict) and item.get("type") == "message" and item.get("role") in {"system", "developer"}:
            text = _text(item.get("content"))
            if text:
                policy.append(text)
        else:
            kept.append(item)
    if policy:
        current = body.get("instructions")
        body["instructions"] = "\n\n".join(([current] if isinstance(current, str) and current else []) + policy)
        body["input"] = kept
    return body


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        print(fmt % args, flush=True)

    def _forward(self):
        length = int(self.headers.get("Content-Length", "0"))
        payload = self.rfile.read(length) if length else b""
        if self.command == "POST" and payload and self.path.rstrip("/").endswith("responses"):
            try:
                payload = json.dumps(normalize(json.loads(payload)), ensure_ascii=False).encode("utf-8")
            except (ValueError, TypeError):
                pass
        headers = {k: v for k, v in self.headers.items() if k.lower() not in {"host", "content-length", "connection"}}
        headers["Content-Length"] = str(len(payload))
        conn = http.client.HTTPConnection(LOOPBACK, site_port('llm'), timeout=600)
        try:
            conn.request(self.command, self.path, body=payload, headers=headers)
            upstream = conn.getresponse()
            self.send_response(upstream.status)
            for key, value in upstream.getheaders():
                if key.lower() not in {"connection", "content-length", "transfer-encoding"}:
                    self.send_header(key, value)
            self.send_header("Connection", "close")
            self.end_headers()
            while True:
                chunk = upstream.read1(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        finally:
            conn.close()
            self.close_connection = True

    do_GET = do_POST = do_DELETE = _forward


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=LOOPBACK)
    parser.add_argument("--port", type=int, default=site_port('proxy'))
    args = parser.parse_args()
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
