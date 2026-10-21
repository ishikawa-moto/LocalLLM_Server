"""Public fixtures only: no real provider, credential, model, or production DB."""
import json
import pathlib
import tempfile
import threading
import time
import http.client
import unittest
from unittest.mock import Mock, patch
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer

from chat_web_search import WebSearch, SearchError, validate_query, plan_query, public_url
import test_chat as base
import chat_web_search as search_module
chat = base.chat


class SearchProviderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        (self.root/'config').mkdir()
        self.path = self.root/'config/private-chat-web-search.json'

    def tearDown(self):
        self.tmp.cleanup()

    def provider(self):
        self.path.write_text(json.dumps(dict(enabled=True, provider='tavily_keyless')))
        return WebSearch(self.root)

    def test_configuration_is_opt_in_and_errors_do_not_break_chat(self):
        self.assertEqual(WebSearch(self.root).status, 'not_configured')
        for cfg in ('{', '{"enabled":"true"}', '{"enabled":true,"provider":"arbitrary-url"}'):
            self.path.write_text(cfg)
            self.assertEqual(WebSearch(self.root).status, 'configuration_error')
        self.path.write_text('{"enabled":false}')
        self.assertEqual(WebSearch(self.root).status, 'disabled')
        self.assertTrue(self.provider().ready)
        self.path.write_text('{"enabled":true,"provider":"tavily_keyless","api_key":"PUBLIC_FIXTURE"}')
        self.assertEqual(WebSearch(self.root).status,'configuration_error')

    def test_planner_requires_exact_json_and_validates_before_network(self):
        self.assertEqual(plan_query('{"query":""}'), '')
        self.assertEqual(plan_query('{"query":"Python release notes"}'), 'Python release notes')
        for text in ('[]', '{"query":null}', '{"query":"ok","tool":"fetch"}', '```json\n{}\n```'):
            with self.subTest(text=text), self.assertRaises(SearchError): plan_query(text)
        search = self.provider()
        for query in ('x'*201, 'api_key=PUBLIC_SECRET', 'C:\\Users\\Someone\\file', 'person@example.org',
                      'http://localhost/admin', 'xoxb-public-fixture', 'query\nheader', '/home/example/private'):
            with self.subTest(query=query), patch('chat_web_search.http.client.HTTPSConnection') as connect:
                with self.assertRaises(SearchError): search.search(query, lambda: None)
                connect.assert_not_called()

    def response(self, body, status=200):
        conn = Mock()
        conn.getresponse.return_value.status = status
        conn.getresponse.return_value.read1.side_effect = [body, b'']
        return conn

    def test_fixed_tls_endpoint_single_call_bounded_safe_results(self):
        body = json.dumps(dict(results=[
            dict(url='javascript:alert(1)', title='bad'),
            dict(url='http://localhost/admin', title='private'),
            dict(url='https://example.org/docs', title='<b>Official</b>', content='A &amp; B'),
            dict(url='https://example.org/docs', title='duplicate')])).encode()
        conn = self.response(body)
        with patch('chat_web_search.http.client.HTTPSConnection', return_value=conn) as connect:
            results = self.provider().search('Python release notes', lambda: None)
        connect.assert_called_once_with('api.tavily.com', timeout=12)
        self.assertEqual(conn.request.call_count, 1)
        method, path = conn.request.call_args.args
        self.assertEqual(method, 'POST')
        self.assertEqual(path,'/search')
        payload=json.loads(conn.request.call_args.kwargs['body'])
        headers=conn.request.call_args.kwargs['headers']
        self.assertEqual(payload['query'],'Python release notes')
        self.assertFalse(payload['include_answer'])
        self.assertEqual(headers['X-Tavily-Access-Mode'],'keyless')
        self.assertNotIn('Authorization',headers)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['title'], 'Official')
        self.assertEqual(results[0]['text'], 'A & B')
        self.assertIn('retrieved_at', results[0])
        conn.close.assert_called_once()

    def test_redirect_http_error_invalid_json_and_oversize_are_not_success(self):
        for body, status in ((b'{}', 302), (b'PRIVATE_RESPONSE', 429), (b'not-json', 200), (b'{"error":{"code":"keyless_limit_exceeded","message":"sign up"}}',200), (b'x'*(256*1024+1), 200)):
            with self.subTest(status=status, size=len(body)):
                conn = self.response(body, status)
                with patch('chat_web_search.http.client.HTTPSConnection', return_value=conn):
                    with self.assertRaises(SearchError) as error: self.provider().search('public query', lambda: None)
                self.assertNotIn('PRIVATE_RESPONSE', str(error.exception))
                self.assertEqual(conn.request.call_count, 1)
                conn.close.assert_called_once()

    def test_cancel_before_connect_and_provider_timeout(self):
        with patch('chat_web_search.http.client.HTTPSConnection') as connect:
            with self.assertRaises(chat.ChatError):
                self.provider().search('public query', lambda: (_ for _ in ()).throw(chat.ChatError('Cancelled',409)))
            connect.assert_not_called()
        with patch('chat_web_search.http.client.HTTPSConnection') as connect:
            connect.return_value.getresponse.side_effect = TimeoutError()
            with self.assertRaises(SearchError): self.provider().search('public query', lambda: None)
            connect.return_value.close.assert_called_once()

    def test_absolute_deadline_breaks_stalled_response_and_closes_own_socket(self):
        release = threading.Event()
        conn = Mock()
        conn.sock.shutdown.side_effect = lambda *_: release.set()
        def stalled():
            if not release.wait(2): raise AssertionError('deadline did not interrupt socket')
            raise OSError('public stalled response')
        conn.getresponse.side_effect = stalled
        with patch('chat_web_search.TIMEOUT', .05), patch('chat_web_search.http.client.HTTPSConnection',return_value=conn):
            with self.assertRaisesRegex(SearchError,'search_timeout'):
                self.provider().search('public query', lambda: None)
        conn.close.assert_called_once()

    def test_late_dns_never_sends_query_after_deadline_or_cancel(self):
        for cancel in (False,True):
            entered,release = threading.Event(),threading.Event()
            conn = Mock()
            def stalled_dns():
                entered.set(); release.wait(2)
            conn.connect.side_effect = stalled_dns
            def check():
                if cancel and entered.is_set(): raise chat.ChatError('Cancelled',409)
            try:
                with patch('chat_web_search.TIMEOUT',.07),patch('chat_web_search.http.client.HTTPSConnection',return_value=conn):
                    with self.assertRaises(chat.ChatError if cancel else SearchError): self.provider().search('public query',check)
                self.assertFalse(release.is_set())
                conn.request.assert_not_called()
            finally:
                release.set()
                until=time.monotonic()+1
                while time.monotonic()<until:
                    if search_module._CONNECT_SLOT.acquire(blocking=False):
                        search_module._CONNECT_SLOT.release(); break
                    time.sleep(.005)
                else: self.fail('DNS worker did not release its owned connection slot')
            conn.request.assert_not_called()

    def cascade(self):
        self.path.write_text(json.dumps(dict(enabled=True,provider='free_cascade',
            searxng_url=f'http://{base.LOOPBACK}:12345/search')))
        return WebSearch(self.root)

    def test_free_cascade_uses_fallback_only_for_missing_or_insufficient_results(self):
        primary = [dict(url='https://docs.python.org/3/',title='Python',text='relevant')]
        fallback = [dict(url='https://www.python.org/',title='Python',text='official')]
        for sufficient in (True,False):
            search=self.cascade(); search._search=Mock(side_effect=[primary,fallback])
            report=Mock(); evaluate=Mock(return_value=sufficient)
            result=search.search('Python docs',lambda:None,evaluate=evaluate,report=report)
            self.assertEqual(result,primary if sufficient else fallback)
            self.assertEqual([c.args[0] for c in search._search.call_args_list],
                             ['searxng'] if sufficient else ['searxng','tavily_keyless'])
            self.assertNotIn('query',str(report.call_args_list))
        for error in ([],SearchError('provider_unavailable')):
            search=self.cascade(); search._search=Mock(side_effect=[error,fallback])
            self.assertEqual(search.search('Python docs',lambda:None,evaluate=lambda _:True),fallback)

    def test_free_cascade_rejects_nonlocal_primary_and_stops_at_free_limit(self):
        for endpoint in ('https://example.org/search','http://localhost:12345/search','http://example.org:12345/search',
                         f'http://{base.LOOPBACK}:12345/other',f'http://{base.LOOPBACK}:12345/search?token=secret'):
            self.path.write_text(json.dumps(dict(enabled=True,provider='free_cascade',searxng_url=endpoint)))
            self.assertEqual(WebSearch(self.root).status,'configuration_error')
        search=self.cascade()
        search._search=Mock(side_effect=[[],SearchError('rate_limited')])
        with self.assertRaisesRegex(SearchError,'rate_limited'): search.search('Python docs',lambda:None)
        self.assertEqual(search._search.call_count,2)

    def test_local_searxng_public_http_contract_and_no_external_fallback(self):
        captured=[]
        class Origin(BaseHTTPRequestHandler):
            def do_POST(self):
                captured.append((self.path,self.rfile.read(int(self.headers['Content-Length'])),dict(self.headers)))
                data=json.dumps(dict(results=[dict(url='https://docs.python.org/3/',title='Python',content='official')])).encode()
                self.send_response(200);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
            def log_message(self,*args):pass
        server=ThreadingHTTPServer((base.LOOPBACK,0),Origin)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            self.path.write_text(json.dumps(dict(enabled=True,provider='free_cascade',
                searxng_url=f'http://{base.LOOPBACK}:{server.server_address[1]}/search')))
            with patch('chat_web_search.http.client.HTTPSConnection') as external:
                results=WebSearch(self.root).search('Python docs',lambda:None,evaluate=lambda _:True)
                external.assert_not_called()
            self.assertEqual(results[0]['provider'],'searxng')
            self.assertEqual(captured[0][0],'/search')
            self.assertIn(b'format=json',captured[0][1])
            self.assertNotIn('Authorization',captured[0][2])
        finally:server.shutdown();server.server_close();thread.join()

    def test_source_urls_reject_credentials_private_hosts_and_active_schemes(self):
        for value in ('javascript:alert(1)', 'file:///local', 'https://a:b@example.org/', 'https://printer.local/',
                      'https://localhost/', 'https://example.org:9443/', 'https://example.org/\nheader'):
            self.assertFalse(public_url(value), value)
        self.assertTrue(public_url('https://docs.python.org/3/'))


class WebChatTests(unittest.TestCase):
    wait = base.ChatTests.wait
    tearDown = base.ChatTests.tearDown
    def setUp(self):
        base.ChatTests.setUp(self)
        self.web = Mock(ready=True, status='ready')
        self.web.search.return_value = [dict(url='https://docs.python.org/3/', title='Python', text='Public fact', retrieved_at='public-time')]
        self.app.web_search = self.web
        self.fake.plan_web_search = Mock(return_value='Python release notes')
        self.final_messages = []
        original = self.fake.generate
        def generate(job, messages):
            self.final_messages.append(messages)
            yield from original(job, messages)
        self.fake.generate = generate

    def web_job(self, message='最新のPython情報を検索', enabled=True):
        cid = self.app.store.create('a')
        job = self.app.submit('a', dict(conversation_id=cid, message=message, secondbrain=False, web_search=enabled))
        self.wait(job)
        return job

    def test_search_results_reach_answer_and_persist_citable_metadata(self):
        job = self.web_job()
        self.assertEqual(job.events[-1]['status'], 'completed')
        self.assertEqual(job.metadata['web_search_status'], 'completed')
        self.assertEqual(job.metadata['web_sources'][0]['source'], 'W1')
        self.assertIn('Public fact', self.final_messages[-1][-1]['content'])
        self.assertIn('web_search_status=completed', self.final_messages[-1][0]['content'])
        saved = self.app.store.messages('a', job.cid)[-1]['metadata']
        self.assertEqual(saved['web_sources'], job.metadata['web_sources'])
        self.web.search.assert_called_once()

    def test_off_and_no_search_plan_never_call_provider(self):
        self.web_job(enabled=False)
        self.fake.plan_web_search.assert_not_called()
        self.fake.plan_web_search.return_value = ''
        job = self.web_job('こんにちは')
        self.assertEqual(job.metadata['web_search_status'], 'not_needed')
        self.web.search.assert_not_called()

    def test_failure_and_empty_results_are_not_claimed_as_search_success(self):
        self.web.search.side_effect = SearchError('provider_unavailable')
        job = self.web_job()
        self.assertEqual(job.metadata['web_search_status'], 'unavailable')
        self.assertTrue(any(e['type']=='warning' for e in job.events))
        self.assertFalse(job.metadata['web_search_used'])
        self.assertIn('web_search_status=unavailable', self.final_messages[-1][0]['content'])
        self.web.search.side_effect = None
        self.web.search.return_value = []
        job = self.web_job()
        self.assertEqual(job.metadata['web_search_status'], 'no_results')

    def use_real_cascade(self):
        (self.root/'config').mkdir(exist_ok=True)
        (self.root/'config/private-chat-web-search.json').write_text(json.dumps(dict(enabled=True,provider='free_cascade',
            searxng_url=f'http://{base.LOOPBACK}:12345/search')))
        primary=[dict(url='https://docs.python.org/3/',title='Primary',text='primary',provider='searxng')]
        fallback=[dict(url='https://www.python.org/',title='Fallback',text='fallback',provider='tavily_keyless')]
        self.app.web_search=WebSearch(self.root)
        self.app.web_search._search=Mock(side_effect=[primary,fallback])
        return primary,fallback

    def test_browser_cascade_records_local_assessment_and_selected_provider(self):
        primary,fallback=self.use_real_cascade()
        self.fake.web_search_sufficient=Mock(return_value=False)
        job=self.web_job()
        self.assertEqual(job.metadata['web_sources'][0]['provider'],'tavily_keyless')
        self.assertEqual([a['provider'] for a in job.metadata['web_search_attempts']],['searxng','tavily_keyless'])
        self.fake.web_search_sufficient.assert_called_once_with(job,primary)
        self.assertEqual(self.app.store.messages('a',job.cid)[-1]['metadata']['web_search_attempts'],job.metadata['web_search_attempts'])

    def test_cancel_during_local_assessment_never_calls_free_quota_fallback(self):
        self.use_real_cascade()
        def cancel(job,rows): job.cancelled.set(); return False
        self.fake.web_search_sufficient=Mock(side_effect=cancel)
        job=self.web_job()
        self.assertEqual(job.events[-1]['status'],'Cancelled')
        self.assertEqual(self.app.web_search._search.call_count,1)
        self.assertEqual(self.app.store.messages('a',job.cid),[])

    def test_unconfigured_and_invalid_request_rejected_before_queue(self):
        cid = self.app.store.create('a')
        for value in ('yes', 1, None):
            with self.assertRaises(chat.ChatError): self.app.submit('a', dict(conversation_id=cid, message='x', web_search=value))
        self.web.ready = False
        with self.assertRaises(chat.ChatError): self.app.submit('a', dict(conversation_id=cid, message='x', web_search=True))
        self.fake.plan_web_search.assert_not_called()

    def test_cancel_during_plan_stops_before_external_search_and_history_write(self):
        def cancel(job):
            job.cancelled.set()
            return 'public query'
        self.fake.plan_web_search.side_effect = cancel
        job = self.web_job()
        self.assertEqual(job.events[-1]['status'], 'Cancelled')
        self.web.search.assert_not_called()
        self.assertEqual(self.app.store.messages('a',job.cid), [])

    def test_plan_contains_current_input_only_not_brain_or_prior_conversation(self):
        upstream = object.__new__(chat.Upstream)
        upstream.config = chat.DEFAULTS.copy()
        upstream.count = Mock(return_value=20)
        upstream.generate = Mock(return_value=iter(['{"query":"Python docs"}']))
        job = chat.Job('a', 'fixture', 'Pythonの公式文書を探す', True, True)
        self.assertEqual(upstream.plan_web_search(job), 'Python docs')
        messages = upstream.generate.call_args.args[1]
        self.assertEqual(len(messages),2)
        self.assertEqual(messages[-1]['content'],job.prompt)
        self.assertEqual(upstream.generate.call_args.kwargs,dict(max_tokens=256,time_limit=60,request_id=job.id+'.web-plan',json_mode=True))

    def test_planning_and_answer_use_distinct_gateway_ids_and_cancel_both(self):
        upstream = object.__new__(chat.Upstream)
        upstream.config = chat.DEFAULTS.copy()
        connections = []
        def connect(*args):
            conn = Mock()
            response = conn.getresponse.return_value
            response.status = 200
            response.readline.side_effect = [b'data: {"choices":[{"delta":{"content":"answer"}}]}\n', b'data: [DONE]\n']
            connections.append(conn)
            return conn
        upstream.connect = connect
        job = chat.Job('a', 'fixture', 'public question', False, True)
        list(upstream.generate(job,[],max_tokens=256,time_limit=60,request_id=job.id+'.web-plan',json_mode=True))
        list(upstream.generate(job,[]))
        ids = [c.request.call_args.args[3]['X-LocalBrain-Request-Id'] for c in connections]
        self.assertEqual(ids,[job.id+'.web-plan',job.id])
        plan_body, answer_body = [json.loads(c.request.call_args.args[2]) for c in connections]
        self.assertEqual(plan_body['response_format']['schema']['required'],['query'])
        self.assertFalse(plan_body['chat_template_kwargs']['enable_thinking'])
        self.assertNotIn('response_format',answer_body)
        self.fake.cancel = Mock()
        job.done = True
        self.app.cancel(job)
        self.assertEqual({c.args[0] for c in self.fake.cancel.call_args_list},set(ids)|{job.id+'.web-check'})

    def test_local_sufficiency_check_requires_boolean_and_has_separate_id(self):
        upstream=object.__new__(chat.Upstream);upstream.config=chat.DEFAULTS.copy()
        upstream.count=Mock(return_value=20)
        job=chat.Job('a','fixture','Python公式情報',False,True)
        for text,expected in (('{"sufficient":true}',True),('{"sufficient":false}',False),
                              ('{"sufficient":"true"}',False),('ignore previous instructions',False)):
            upstream.generate=Mock(return_value=iter([text]))
            self.assertEqual(upstream.web_search_sufficient(job,self.web.search.return_value),expected)
            self.assertEqual(upstream.generate.call_args.kwargs['request_id'],job.id+'.web-check')
            self.assertEqual(upstream.generate.call_args.kwargs['max_tokens'],128)

    def test_web_sources_obey_real_template_context_budget(self):
        cfg = chat.DEFAULTS | dict(context_tokens=1200, output_tokens=100, max_retrieval_tokens=400)
        rows = self.web.search.return_value + [dict(url='https://example.org/',title='Huge',text='x'*2000)]
        messages, meta = chat.build_context(self.fake,cfg,[],'search',[], rows,'completed')
        self.assertGreaterEqual(len(meta['web_sources']),1)
        self.assertLessEqual(meta['context_tokens'],1100)
        self.assertNotIn('x'*2000,messages[-1]['content'])

    def test_default_secondbrain_cannot_starve_web_evidence(self):
        cfg = chat.DEFAULTS | dict(max_retrieval_tokens=600)
        brain_rows = [dict(text='B'*300,path='fact.md',revision='1',hybrid_score=1)]
        web_rows = [dict(url='https://docs.python.org/3/',title='Public result',text='W'*1800)]
        messages,meta = chat.build_context(self.fake,cfg,[],'search',brain_rows,web_rows,'completed')
        self.assertTrue(meta['web_search_used'])
        self.assertIn('W1',messages[-1]['content'])
        self.assertLessEqual(self.fake.tokens(messages[-1]['content'].split('reference_context (参考資料):\n')[1]),600)

    def test_browser_http_health_submit_events_and_saved_sources(self):
        server = chat.Server((base.LOOPBACK,0),chat.Handler)
        server.app = self.app
        port = server.server_address[1]
        self.app.config['allowed_hosts'] = [f'{base.LOOPBACK}:{port}']
        thread = threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        def request(path, data=None):
            conn = http.client.HTTPConnection(base.LOOPBACK,port,timeout=3)
            conn.request('GET' if data is None else 'POST',path,
                         None if data is None else json.dumps(data),{'X-Chat-Request':'1'})
            response = conn.getresponse()
            body = response.read()
            conn.close()
            self.assertLess(response.status,300)
            return json.loads(body)
        try:
            health = request('/api/health')
            self.assertEqual(health['web_search'],'ready')
            self.assertNotIn('api_key',json.dumps(health))
            cid = request('/api/conversations',{})['id']
            rid = request('/api/chat',dict(conversation_id=cid,message='Pythonの公式情報を検索',web_search=True,secondbrain=False))['request_id']
            self.wait(self.app.get_job('a',rid))
            conn = http.client.HTTPConnection(base.LOOPBACK,port,timeout=3)
            conn.request('GET','/api/events/'+rid)
            response = conn.getresponse()
            events = []
            while True:
                line = response.readline()
                if line.startswith(b'data: '):
                    event = json.loads(line[6:])
                    events.append(event)
                    if event['type']=='done': break
            conn.close()
            meta = next(e for e in events if e['type']=='metadata')
            self.assertEqual(meta['web_sources'][0]['url'],'https://docs.python.org/3/')
            saved = request('/api/conversations/'+cid)['messages'][-1]
            self.assertEqual(saved['metadata']['web_sources'],meta['web_sources'])
        finally:
            server.shutdown(); server.server_close(); thread.join()


if __name__ == '__main__':
    unittest.main()
