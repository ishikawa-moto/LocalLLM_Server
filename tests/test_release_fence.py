"""Public fixtures for explicit one-time operational adoption under caller-owned fences."""
import copy,json,pathlib,sys,types,unittest
from unittest.mock import patch
import test_release_metrics as fixtures
import knowledge_pipeline
db=fixtures.db

class FencedMetricsTests(unittest.TestCase):
    setUp=fixtures.MetricsReleaseTests.setUp
    snapshot=fixtures.MetricsReleaseTests.snapshot
    sql=fixtures.MetricsReleaseTests.sql
    search_delta=fixtures.MetricsReleaseTests.search_delta
    token='00000000-0000-0000-0000-000000000001'
    def test_preview_keeps_complete_evidence_and_does_not_adopt(self):
        old=self.snapshot();original=copy.deepcopy(old)
        self.brain.metric('duplicate_sources_avoided',106);self.search_delta(64,20)
        new=self.snapshot();result=db.operational_comparison(old,new)
        self.assertTrue(result['compatible'],result);self.assertFalse(result['baseline_adopted'])
        self.assertEqual(result['counter_deltas'],{'duplicate_sources_avoided':106,'search_requests':64,'search_hits':20})
        self.assertEqual(old,original);self.assertEqual(new,self.snapshot())
        self.assertFalse(db.classify(old,new)['compatible'])
    def test_every_other_operational_counter_is_still_exact(self):
        for key in db.METRIC_KEYS-db.FENCED_ADOPTION_KEYS:
            with self.subTest(key=key):
                old=self.snapshot();self.brain.metric(key)
                result=db.operational_comparison(old,self.snapshot())
                self.assertFalse(result['compatible']);self.assertEqual(result['reason_code'],'NONSEARCH_COUNTER_DRIFT')
    def test_business_change_in_each_database_is_rejected(self):
        for label in db.DATABASES:
            with self.subTest(label=label):
                old=self.snapshot()
                if label=='index':self.sql("INSERT INTO documents VALUES('PUBLIC','h','p','active','e','{}')")
                elif label=='restricted':self.sql("INSERT INTO retired_ids VALUES('PUBLIC_KIND','PUBLIC','PUBLIC_SESSION')",label=label)
                else:self.sql('CREATE TABLE public_extra(value)',label=label)
                self.assertFalse(db.operational_comparison(old,self.snapshot())['compatible'])
    def test_counter_decrease_and_unpaired_hits_refused(self):
        old=self.snapshot();self.brain.metric('duplicate_sources_avoided',-1)
        self.assertFalse(db.operational_comparison(old,self.snapshot())['compatible'])
        self.brain.metric('duplicate_sources_avoided',1);self.brain.metric('search_hits')
        self.assertFalse(db.operational_comparison(old,self.snapshot())['compatible'])
    def test_unknown_keys_types_schema_and_known_key_insertion_refused(self):
        old=self.snapshot();self.sql("INSERT INTO metrics VALUES('PUBLIC_UNKNOWN',1)")
        self.assertFalse(db.operational_comparison(old,self.snapshot())['compatible'])
        self.sql("DELETE FROM metrics WHERE key='PUBLIC_UNKNOWN'")
        self.sql("UPDATE metrics SET value='PUBLIC_TEXT' WHERE key='search_requests'")
        self.assertFalse(db.operational_comparison(old,self.snapshot())['compatible'])
        self.sql("UPDATE metrics SET value=10 WHERE key='search_requests'")
        self.sql("CREATE INDEX public_extra ON metrics(value)")
        self.assertFalse(db.operational_comparison(old,self.snapshot())['compatible'])
        self.sql("DROP INDEX public_extra");self.sql("DELETE FROM metrics WHERE key='fact_promotions'")
        old=self.snapshot();self.brain.metric('fact_promotions',0)
        self.assertFalse(db.operational_comparison(old,self.snapshot())['compatible'])
    def test_receipt_binds_both_full_snapshots_and_one_fixed_future_anchor(self):
        old=self.snapshot();self.brain.metric('duplicate_sources_avoided',53);self.search_delta(80,10)
        current=self.snapshot();receipt=db.adopt_fenced_metrics(old,current,'PUBLIC-DECISION',self.token)
        self.assertEqual(receipt['source_baseline'],old);self.assertEqual(receipt['observed_snapshot'],current)
        self.assertEqual(receipt['adopted_baseline'],current);self.assertFalse(receipt['automatic_baseline_refresh'])
        self.assertEqual(receipt['fence_ownership_verified_by'],'caller');self.assertFalse(receipt['producer_identity_proven'])
        anchor=receipt['adopted_baseline'];self.search_delta(32,5)
        self.assertTrue(db.classify(anchor,self.snapshot())['compatible'])
        self.search_delta(1);self.assertFalse(db.classify(anchor,self.snapshot())['compatible'])
    def test_new_duplicate_after_adoption_is_rejected_by_normal_classifier(self):
        old=self.snapshot();anchor=db.adopt_fenced_metrics(old,old,'PUBLIC',self.token)['adopted_baseline']
        self.brain.metric('duplicate_sources_avoided')
        self.assertEqual(db.classify(anchor,self.snapshot())['reason_code'],'NONSEARCH_COUNTER_DRIFT')
    def test_missing_reference_or_invalid_token_and_business_drift_refused(self):
        old=self.snapshot()
        for reference,token in [('',self.token),('PUBLIC',''),('PUBLIC','not-an-owner')]:
            with self.assertRaises(ValueError):db.adopt_fenced_metrics(old,old,reference,token)
        self.sql("INSERT INTO retired_ids VALUES('PUBLIC_KIND','PUBLIC','PUBLIC_SESSION')",label='restricted')
        with self.assertRaises(ValueError):db.adopt_fenced_metrics(old,self.snapshot(),'PUBLIC',self.token)
    def test_actual_pipeline_existing_marker_has_no_extract_or_discovery(self):
        pipeline=knowledge_pipeline.KnowledgePipeline(self.brain)
        marker=self.brain.runtime/'librarian.lock';marker.write_text(json.dumps({'pid':123,'started_at':'PUBLIC'}))
        before=self.snapshot();saved=marker.read_bytes()
        with patch.object(pipeline,'_run_queue_locked',side_effect=AssertionError('must not discover/extract')),\
             patch.object(knowledge_pipeline.subprocess,'run',return_value=types.SimpleNamespace(stdout='123')):
            self.assertTrue(pipeline.run_queue(extractor=object())['locked'])
        self.assertEqual(marker.read_bytes(),saved);self.assertEqual(before,self.snapshot())
