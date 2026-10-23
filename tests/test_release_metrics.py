"""Public full-DB fixtures for bounded search-counter evidence; no live config/models."""
import ast,copy,hashlib,importlib.util,json,pathlib,socket,sqlite3,subprocess,sys,tempfile,time,types,unittest,urllib.request
from unittest.mock import patch
import localbrain,slack_gateway,control_gateway as gateway
from site_settings import LOOPBACK
REPO=pathlib.Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('release_metrics_db',REPO/'deployment/release_db.py')
db=importlib.util.module_from_spec(spec);spec.loader.exec_module(db)

class MetricsReleaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=pathlib.Path(self.tmp.name);self.brain=localbrain.Brain(self.root)
        self.store=slack_gateway.RestrictedStore(self.root)
        with self.store.db() as conn:conn.execute('SELECT 1')
        for key in db.METRIC_KEYS:self.brain.metric(key,0)
        self.brain.metric('search_requests',10);self.brain.metric('search_hits',4)
        for relative in ('runtime/gateway-jobs.sqlite3','runtime/chat.sqlite3'):
            conn=sqlite3.connect(self.root/relative)
            try:
                conn.execute('CREATE TABLE fixture(id TEXT PRIMARY KEY,value)')
                conn.execute("INSERT INTO fixture VALUES ('PUBLIC',1)");conn.commit()
            finally:conn.close()
    def snapshot(self):return db.snapshot_root(self.root)
    def sql(self,statement,args=(),label='index'):
        conn=sqlite3.connect(self.root/db.DATABASES[label])
        try:conn.execute(statement,args);conn.commit()
        finally:conn.close()
    def reject(self,before,reason=None):
        result=db.classify(before,self.snapshot())
        self.assertFalse(result['compatible'],result)
        if reason:self.assertEqual(result['reason_code'],reason)
        return result
    def search_delta(self,requests,hits=0):
        self.brain.metric('search_requests',requests);self.brain.metric('search_hits',hits)
    def legacy(self,snapshot):
        old=copy.deepcopy(snapshot);old['schema']='localbrain.all-table-snapshot.v1'
        index=old['databases']['index'];index.pop('metrics_profile')
        index['logical_sha256']=db.digest({k:v for k,v in index.items() if k!='logical_sha256'})
        old['aggregate_sha256']=db.digest(old['databases'])
        return old
    def test_source_inventory_covers_all_metric_producers_including_dynamic_kinds(self):
        literals=set();kinds=set();dynamic=0
        for path in (REPO/'src').glob('*.py'):
            tree=ast.parse(path.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if not isinstance(node,ast.Call) or not isinstance(node.func,ast.Attribute):continue
                if node.func.attr=='create_draft':
                    self.assertIsInstance(node.args[0],ast.Constant)
                    kinds.add(node.args[0].value)
                if node.func.attr=='metric':
                    arg=node.args[0]
                    if isinstance(arg,ast.Constant):literals.add(arg.value)
                    else:
                        dynamic+=1;self.assertEqual(ast.unparse(arg),"kind + '_drafts'")
        self.assertEqual(dynamic,1)
        self.assertEqual(db.METRIC_KEYS,literals|{kind+'_drafts' for kind in kinds})
    def test_same_rows_bind_profile_and_full_typed_table_digest(self):
        observed=self.snapshot();index=observed['databases']['index']
        self.assertEqual(index['tables']['metrics']['typed_rows_sha256'],
                         db.row_digest(index['metrics_profile']['counters'].items()))
        db.validate_snapshot(observed)
        self.assertTrue(db.classify(observed,observed)['compatible'])
    def test_brain_status_gateway_health_status_heartbeat_do_not_change_any_db(self):
        before=self.snapshot()
        controller=gateway.LifecycleController(gateway.GatewayConfig(),lambda *a,**k:self.fail('model start'),
            health=lambda p:False,audit_fn=lambda *a,**k:None)
        class Request:
            allowed=lambda s:True
            send_json=lambda s,status,value:(status,value)
            server=types.SimpleNamespace(slack_service=None)
            headers={}
        request=Request()
        def health(port):
            self.brain.status();return False
        with patch.object(gateway,'CONTROLLER',controller,create=True),patch.object(gateway,'healthy',side_effect=health),\
             patch.object(gateway,'listening',return_value=True),patch.object(gateway,'resource_status',return_value={}):
            for path in ('/health','/v1/control/status'):
                request.path=path;self.assertEqual(gateway.Handler.do_GET(request)[0],200)
            request.path='/v1/control/heartbeat';self.assertEqual(gateway.Handler.do_POST(request)[0],200)
        self.brain.status()
        self.assertEqual(before,self.snapshot())
    def test_actual_search_and_context_change_only_the_bounded_pair(self):
        path=self.brain.docs/'wiki'/'public.md';path.write_text('PUBLIC fixed evidence',encoding='utf-8')
        self.brain.index();before=self.snapshot()
        self.assertTrue(self.brain.search('PUBLIC')['results'])
        self.assertFalse(self.brain.search('NO_MATCH_934683')['results'])
        self.brain.context('PUBLIC')
        after=self.snapshot();comparison=db.classify(before,after)
        self.assertTrue(comparison['compatible'],comparison)
        self.assertEqual((comparison['search_requests_delta'],comparison['search_hits_delta']),(3,2))
        self.assertNotEqual(before['aggregate_sha256'],after['aggregate_sha256'])
        self.assertNotEqual(before['databases']['index']['tables']['metrics'],after['databases']['index']['tables']['metrics'])
    def test_actual_http_health_is_readonly_and_authenticated_search_context_update_pair(self):
        with socket.socket() as sock:sock.bind((LOOPBACK,0));port=sock.getsockname()[1]
        process=subprocess.Popen([sys.executable,'-B',str(REPO/'src/localbrain.py'),'--root',str(self.root),'serve','--port',str(port)],
                                 stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        def stop():
            process.terminate();process.wait(timeout=10)
        self.addCleanup(stop)
        base=f'http://{LOOPBACK}:{port}'
        for _ in range(50):
            try:
                with urllib.request.urlopen(base+'/health',timeout=1) as response:self.assertEqual(response.status,200)
                break
            except OSError:time.sleep(.1)
        else:self.fail('Public Brain fixture did not start')
        before=self.snapshot()
        with urllib.request.urlopen(base+'/health',timeout=2) as response:self.assertEqual(response.status,200)
        self.assertEqual(before,self.snapshot())
        for route in ('/search','/context'):
            request=urllib.request.Request(base+route,data=json.dumps({'query':'PUBLIC'}).encode(),
                headers={'Authorization':'Bearer '+self.brain.keys['read']})
            with urllib.request.urlopen(request,timeout=2) as response:self.assertEqual(response.status,200)
        result=db.classify(before,self.snapshot())
        self.assertTrue(result['compatible'],result)
        self.assertEqual((result['search_requests_delta'],result['search_hits_delta']),(2,0))
    def test_find_wrappers_each_produce_one_search_pair(self):
        before=self.snapshot()
        self.brain.find_entity('PUBLIC');self.brain.find_concept('PUBLIC');self.brain.find_synthesis('PUBLIC')
        result=db.classify(before,self.snapshot())
        self.assertTrue(result['compatible'],result);self.assertEqual(result['search_requests_delta'],3)
    def test_bounded_delta_is_always_anchored_to_original_baseline(self):
        before=self.snapshot();self.search_delta(32,1)
        self.assertTrue(db.classify(before,self.snapshot())['compatible'])
        self.search_delta(1)
        self.reject(before,'SEARCH_COUNTER_DELTA')
    def test_counter_decrease_is_rejected(self):
        before=self.snapshot();self.search_delta(-1);self.reject(before,'SEARCH_COUNTER_DELTA')
    def test_hits_increase_without_requests_is_rejected_including_mid_search_baseline(self):
        self.search_delta(1);before=self.snapshot() # Snapshot between the two existing writer transactions.
        self.brain.metric('search_hits',1);self.reject(before,'SEARCH_COUNTER_DELTA')
    def test_nonsearch_counter_even_normal_producer_is_held(self):
        before=self.snapshot();self.brain.metric('fact_drafts',1);self.reject(before,'NONSEARCH_COUNTER_DRIFT')
    def test_business_content_changes_are_rejected_for_every_db(self):
        for label in db.DATABASES:
            with self.subTest(database=label):
                before=self.snapshot()
                if label=='index':self.sql("INSERT INTO documents VALUES('PUBLIC','h','p','active','e','{}')")
                elif label=='restricted':self.sql("INSERT INTO retired_ids VALUES('PUBLIC_KIND','PUBLIC','PUBLIC_SESSION')",label=label)
                elif label=='semantic':self.sql("INSERT INTO vectors VALUES('PUBLIC','p','r',1,x'00')",label=label)
                else:self.sql("UPDATE fixture SET value=2",label=label)
                self.reject(before)
    def test_index_same_count_business_text_change_is_rejected(self):
        self.sql("INSERT INTO documents VALUES('PUBLIC','h','p','active','e','{}')")
        before=self.snapshot();self.sql("UPDATE documents SET metadata='changed'")
        self.reject(before,'NONMETRICS_TABLE_DRIFT')
    def test_unknown_key_and_unknown_types_are_rejected_with_full_digest_preserved(self):
        for statement in ("INSERT INTO metrics VALUES('unknown_public',1)",
                          "UPDATE metrics SET value='PUBLIC_TEXT' WHERE key='search_hits'",
                          "UPDATE metrics SET value=1.5 WHERE key='search_hits'",
                          "UPDATE metrics SET value=x'01' WHERE key='search_hits'",
                          "UPDATE metrics SET value=-1 WHERE key='search_hits'"):
            with self.subTest(sql=statement):
                before=self.snapshot();self.sql(statement)
                current=self.snapshot()
                self.assertIn('typed_rows_sha256',current['databases']['index']['tables']['metrics'])
                self.assertFalse(db.classify(before,current)['compatible'])
                self.sql("DELETE FROM metrics WHERE key='unknown_public'")
                self.sql("UPDATE metrics SET value=4 WHERE key='search_hits'")
    def test_missing_request_key_cannot_validate_positive_hits(self):
        self.sql("DELETE FROM metrics WHERE key='search_requests'")
        snapshot=self.snapshot()
        self.assertEqual(db.classify(snapshot,snapshot)['reason_code'],'METRICS_HITS_EXCEED_REQUESTS')
    def test_unknown_key_in_unchanged_baseline_is_also_rejected(self):
        self.sql("INSERT INTO metrics VALUES('unknown_public',0)")
        snapshot=self.snapshot();self.assertFalse(db.classify(snapshot,snapshot)['compatible'])
    def test_known_key_insertion_or_deletion_is_rejected(self):
        before=self.snapshot();self.sql("DELETE FROM metrics WHERE key='fact_drafts'")
        self.reject(before,'METRICS_KEY_SET_DRIFT')
        deleted=self.snapshot();self.sql("INSERT INTO metrics VALUES('fact_drafts',0)")
        self.reject(deleted,'METRICS_KEY_SET_DRIFT')
    def test_schema_trigger_and_version_changes_are_rejected(self):
        before=self.snapshot();self.sql('CREATE INDEX extra_metrics ON metrics(value)')
        self.reject(before)
        self.sql('DROP INDEX extra_metrics');before=self.snapshot()
        self.sql("CREATE TRIGGER extra_metrics AFTER UPDATE ON metrics BEGIN SELECT 1; END")
        self.reject(before);self.sql('DROP TRIGGER extra_metrics')
        before=self.snapshot();self.sql('PRAGMA user_version=1');self.reject(before,'INDEX_SCHEMA_OR_VERSION_DRIFT')
    def test_profile_tampering_does_not_hide_a_full_row_change(self):
        before=self.snapshot();after=copy.deepcopy(before)
        after['databases']['index']['metrics_profile']['counters']['search_requests']+=1
        index=after['databases']['index'];index['logical_sha256']=db.digest({k:v for k,v in index.items() if k!='logical_sha256'})
        after['aggregate_sha256']=db.digest(after['databases'])
        self.assertEqual(db.classify(before,after)['reason_code'],'METRICS_PROFILE_TYPED_DIGEST')
    def test_additive_migration_replaces_only_restricted_expected_record(self):
        with self.store.db() as conn:conn.execute('DROP TABLE retry_links')
        before=self.snapshot();migration=db.migrate(self.root,before['databases']['restricted'])
        self.search_delta(2,1);current=self.snapshot()
        self.assertTrue(db.classify(before,current,migration)['compatible'])
        self.assertFalse(db.classify(before,current)['compatible'])
        self.sql("UPDATE fixture SET value=2",label='chat')
        self.assertFalse(db.classify(before,self.snapshot(),migration)['compatible'])
    def test_migration_does_not_refresh_search_anchor(self):
        before=self.snapshot();migration=db.migrate(self.root,before['databases']['restricted'])
        self.search_delta(32);self.assertTrue(db.classify(before,self.snapshot(),migration)['compatible'])
        self.search_delta(1);self.assertFalse(db.classify(before,self.snapshot(),migration)['compatible'])
    def test_legacy_unique_digest_proof_preserves_original_and_observations(self):
        old=self.snapshot();legacy=self.legacy(old);unchanged=copy.deepcopy(legacy)
        self.search_delta(3,2);current=self.snapshot()
        proof=db.upgrade_legacy(legacy,current)
        self.assertEqual(legacy,unchanged)
        self.assertEqual(proof['derived_baseline'],old)
        self.assertEqual(proof['observed_snapshot'],current)
        self.assertFalse(proof['producer_identity_proven'])
    def test_legacy_exact_content_is_uniquely_proven(self):
        current=self.snapshot()
        self.assertEqual(db.upgrade_legacy(self.legacy(current),current)['derived_baseline'],current)
    def test_legacy_unknown_nonsearch_or_excess_change_never_refreshes_baseline(self):
        legacy=self.legacy(self.snapshot());self.brain.metric('fact_drafts',1)
        with self.assertRaisesRegex(ValueError,'LEGACY_TYPED_DIGEST_NOT_UNIQUELY_PROVEN'):db.upgrade_legacy(legacy,self.snapshot())
        self.brain.metric('fact_drafts',-1);self.search_delta(33)
        with self.assertRaisesRegex(ValueError,'LEGACY_TYPED_DIGEST_NOT_UNIQUELY_PROVEN'):db.upgrade_legacy(legacy,self.snapshot())
    def test_legacy_business_drift_and_invalid_profile_are_rejected(self):
        legacy=self.legacy(self.snapshot());self.sql("UPDATE fixture SET value=2",label='gateway')
        with self.assertRaisesRegex(ValueError,'LEGACY_NONMETRICS_DATABASE_DRIFT'):db.upgrade_legacy(legacy,self.snapshot())
        legacy=self.legacy(self.snapshot());self.sql("INSERT INTO metrics VALUES('unknown_public',0)")
        with self.assertRaisesRegex(ValueError,'METRICS_UNKNOWN_KEY'):db.upgrade_legacy(legacy,self.snapshot())

if __name__=='__main__':unittest.main()
