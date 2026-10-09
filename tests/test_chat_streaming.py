"""A delayed HTTP origin proves proxy flushes before upstream EOF."""
from site_settings import LOOPBACK, site_port
import http.client
import importlib.util
import pathlib
import threading
import unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('stream_proxy',pathlib.Path(__file__).resolve().parents[1]/'src/codex_proxy.py')
proxy=importlib.util.module_from_spec(spec);spec.loader.exec_module(proxy)

class StreamingTests(unittest.TestCase):
    def test_first_sse_event_before_upstream_finishes(self):
        release=threading.Event()
        class Origin(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
                self.wfile.write(b'data: first\n\n');self.wfile.flush()
                release.wait(4)
                self.wfile.write(b'data: [DONE]\n\n');self.wfile.flush()
            def log_message(self,*args):pass
        upstream=ThreadingHTTPServer((LOOPBACK,0),Origin)
        frontend=ThreadingHTTPServer((LOOPBACK,0),proxy.Handler)
        threads=[threading.Thread(target=s.serve_forever,daemon=True) for s in (upstream,frontend)]
        for t in threads:t.start()
        original=http.client.HTTPConnection
        def connect(host,port=None,**kw):return original(host,upstream.server_address[1] if port==site_port('llm') else port,**kw)
        conn=original(LOOPBACK,frontend.server_address[1],timeout=2)
        try:
            with patch.object(proxy.http.client,'HTTPConnection',side_effect=connect):
                conn.request('GET','/stream');r=conn.getresponse()
                self.assertEqual(r.readline(),b'data: first\n')
                self.assertFalse(release.is_set())
                release.set();r.read()
        finally:
            release.set();conn.close()
            for s in (frontend,upstream):s.shutdown();s.server_close()
            for t in threads:t.join()

if __name__=='__main__':unittest.main()
