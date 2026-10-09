"""Small ServerPC diagnostics client for the mTLS Gateway; never prints private keys."""
from __future__ import annotations
from site_settings import LOOPBACK, site_port

import argparse
import http.client
import json
import pathlib
import ssl
import sys
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[1]
TLS = ROOT / "config" / "tls"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("method", choices=("GET", "POST"))
    parser.add_argument("path")
    parser.add_argument("--body-file", type=pathlib.Path)
    args = parser.parse_args()
    body = args.body_file.read_bytes() if args.body_file else None
    context = ssl.create_default_context(cafile=str(TLS / "ca.pem")); context.check_hostname = False
    context.load_cert_chain(TLS / "client.pem", TLS / "client-key.pem")
    connection = http.client.HTTPSConnection(LOOPBACK, site_port('gateway'), context=context, timeout=900)
    headers = {"X-LocalBrain-Request-Id": str(uuid.uuid4())}
    if body is not None: headers["Content-Type"] = "application/json"
    try:
        connection.request(args.method, args.path, body=body, headers=headers)
        response = connection.getresponse(); raw = response.read()
    finally: connection.close()
    if response.status >= 400:
        print(raw.decode("utf-8", errors="replace"), file=sys.stderr); return 1
    value = json.loads(raw or b"{}")
    print(json.dumps(value, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
