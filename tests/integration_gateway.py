"""Live mTLS and SecondBrain integration checks for an installed ServerPC gateway."""

from __future__ import annotations
import pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'src'))
from site_settings import LOOPBACK, site_port

import http.client
import json
import pathlib
import ssl
import sys
import uuid


ROOT = pathlib.Path(__file__).resolve().parents[1]
TLS = ROOT / "config" / "tls"


def request(context: ssl.SSLContext, method: str, path: str, payload: dict | None = None):
    connection = http.client.HTTPSConnection(LOOPBACK, site_port('gateway'), context=context, timeout=60)
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"X-LocalBrain-Request-Id": str(uuid.uuid4())}
    if body is not None:
        headers["Content-Type"] = "application/json"
    try:
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        data = response.read()
        return response.status, json.loads(data or b"{}")
    finally:
        connection.close()


def main() -> int:
    unauthenticated = ssl.create_default_context(cafile=str(TLS / "ca.pem"))
    unauthenticated.check_hostname = False
    try:
        request(unauthenticated, "GET", "/v1/control/status")
    except (ssl.SSLError, ConnectionResetError, http.client.RemoteDisconnected):
        pass
    else:
        raise AssertionError("Gateway accepted a connection without a client certificate")

    authenticated = ssl.create_default_context(cafile=str(TLS / "ca.pem"))
    authenticated.check_hostname = False
    authenticated.load_cert_chain(TLS / "client.pem", TLS / "client-key.pem")

    status_code, status = request(authenticated, "GET", "/v1/control/status")
    assert status_code == 200, (status_code, status)
    assert status.get("gateway") == "ok", status

    status_code, result = request(
        authenticated,
        "POST",
        "/v1/brain/search",
        {"query": "LocalBrain", "limit": 3},
    )
    assert status_code == 200, (status_code, result)
    assert "results" in result, result
    chunk_id=result["results"][0]["id"] if result["results"] else None
    read_checks={}
    for name,path,payload in (
        ("entity","/v1/brain/find-entity",{"query":"LocalBrain","limit":3}),
        ("concept","/v1/brain/find-concept",{"query":"RAG","limit":3}),
        ("synthesis","/v1/brain/find-synthesis",{"query":"LocalBrain","limit":3}),
        ("superseded","/v1/brain/superseded",{"query":"","limit":3}),
    ):
        code,value=request(authenticated,"POST",path,payload); assert code==200,(path,code,value); assert "results" in value,value
        read_checks[name]=value
    if chunk_id:
        code,value=request(authenticated,"POST","/v1/brain/sources",{"chunk_id":chunk_id}); assert code==200,(code,value); assert "sources" in value
        read_checks["sources"]=value
        code,value=request(authenticated,"POST","/v1/brain/history",{"path":result["results"][0]["path"],"limit":3}); assert code==200,(code,value); assert "history" in value
        read_checks["history"]=value
    print(json.dumps({"mtls": "PASS", "gateway": status, "brain_search": result,"extended_reads":read_checks}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
