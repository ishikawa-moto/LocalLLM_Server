import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest

SRC=pathlib.Path(__file__).resolve().parents[1]/"src"/"localbrain.py"
sys.path.insert(0,str(SRC.parent))
spec=importlib.util.spec_from_file_location("localbrain_pipeline_test",SRC)
module=importlib.util.module_from_spec(spec); sys.modules[spec.name]=module; spec.loader.exec_module(module)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="localbrain-pipeline-"); self.root=pathlib.Path(self.temp.name)
        (self.root/"config").mkdir()
        (self.root/"config"/"secondbrain.json").write_text(json.dumps({"semantic_enabled":False,"features":{
            "auto_fact_promotion":True,"auto_entity_update":True,"auto_concept_update":True,
            "auto_synthesis":False,"idle_librarian":False},"librarian_chunk_bytes":4096}),encoding="utf-8")
        self.brain=module.Brain(self.root); self.pipeline=self.brain.pipeline
    def tearDown(self): self.temp.cleanup()

    def source(self,name,text):
        path=self.brain.docs/"raw"/name; path.write_text(text,encoding="utf-8"); raw=path.read_bytes()
        meta={"project":"test","status":"active","evidence_level":"confirmed","source_id":"source:"+module.digest(raw),
              "source_path":"raw/"+name,"source_type":"markdown","source_date":"2026-09-14","retrieved_at":module.now(),"source_hash":module.digest(raw)}
        module.atomic_json(path.with_suffix(".md.meta.json"),meta); return "raw/"+name

    def test_direct_fact_promotes_with_full_provenance_and_reuses_page(self):
        first=self.source("one.md","LocalBrain uses mTLS.\n")
        result=self.pipeline.promote_fact(first,{"claim":"LocalBrain uses mTLS.","subject":"LocalBrain","predicate":"transport","target_kind":"entity"})
        self.assertEqual(result["status"],"promoted")
        side=self.brain.docs/"wiki"/"entities"/"localbrain.md.meta.json"; metadata=json.loads(side.read_text(encoding="utf-8"))
        provenance=metadata["facts"][0]["sources"][0]
        self.assertEqual(set(provenance),{"source_id","source_path","source_type","source_date","retrieved_at","source_hash","relevant_location","confidence","knowledge_type"})
        second=self.source("two.md","Independent source.\nLocalBrain uses mTLS.\n")
        self.pipeline.promote_fact(second,{"claim":"LocalBrain uses mTLS.","subject":"LOCALBRAIN","predicate":"transport","target_kind":"entity"})
        metadata=json.loads(side.read_text(encoding="utf-8")); self.assertEqual(len(metadata["facts"]),1)
        self.assertEqual(metadata["facts"][0]["confidence"],"confirmed")

    def test_unverifiable_fact_and_conflict_stay_drafts(self):
        source=self.source("facts.md","The configured port is GATEWAY_PORT.\n")
        unverified=self.pipeline.promote_fact(source,{"claim":"The port is secure.","subject":"Gateway","predicate":"port","target_kind":"entity"})
        self.assertEqual(unverified["status"],"draft")
        self.pipeline.promote_fact(source,{"claim":"The configured port is GATEWAY_PORT.","subject":"Gateway","predicate":"port","target_kind":"entity"})
        other=self.source("other.md","The configured port is ALTERNATE_GATEWAY_PORT.\n")
        conflict=self.pipeline.promote_fact(other,{"claim":"The configured port is ALTERNATE_GATEWAY_PORT.","subject":"Gateway","predicate":"port","target_kind":"entity"})
        self.assertEqual(conflict["reason"],"contradiction")
        self.assertEqual(len(list((self.brain.docs/"wiki"/"entities").glob("*.md"))),1)

    def test_synthesis_and_decision_wait_for_review(self):
        source=self.source("notes.md","Evidence A.\nEvidence B.\n")
        other=self.source("other.md","Independent evidence.\n")
        results=self.pipeline.process_candidates(source,{"facts":[],"syntheses":[{"title":"Cross source","claim":"A pattern","source_paths":[source,other]}],
            "decisions":[{"decision":"Use mTLS","evidence_quote":"Evidence A.","reason":"Limit access","alternatives":["none"],"rejected_alternatives":["HTTP"],
                          "rejected_reasons":["clear text"],"evidence":[source],"conditions_for_reconsideration":"Network redesign"}]})
        self.assertEqual(results["syntheses"][0]["status"],"draft")
        decision=pathlib.Path(self.brain.docs/results["decisions"][0]["path"]).read_text(encoding="utf-8")
        for heading in ("Decision","Reason","Alternatives","Rejected alternatives","Rejected reasons","Evidence","Date","Conditions for reconsideration"):
            self.assertIn("## "+heading,decision)
        self.assertFalse(list((self.brain.docs/"wiki"/"synthesis").glob("*.md")))

    def test_inferred_decision_without_exact_choice_is_rejected(self):
        source=self.source("recommendation.md","It may be useful to consider mTLS.\n")
        result=self.pipeline.process_candidates(source,{"facts":[],"syntheses":[],"decisions":[{"decision":"Adopt mTLS","reason":"security"}]})
        self.assertEqual(result["decisions"][0]["status"],"rejected")

    def test_single_source_summary_is_not_synthesis(self):
        source=self.source("single.md","Only one source.\n")
        result=self.pipeline.process_candidates(source,{"facts":[],"syntheses":[{"title":"Summary","claim":"Only one source","source_paths":[source]}],"decisions":[]})
        self.assertEqual(result["syntheses"][0]["status"],"rejected")

    def test_secret_source_never_promotes(self):
        source=self.source("secret.md","api_key = sk-abcdefghijklmnopqrstuvwxyz123456\n")
        result=self.pipeline.promote_fact(source,{"claim":"api_key = sk-abcdefghijklmnopqrstuvwxyz123456","subject":"Credential","predicate":"value","target_kind":"entity"})
        self.assertEqual(result["status"],"blocked"); self.assertEqual(result["reason"],"source-secret")
        self.assertFalse(list((self.brain.docs/"drafts").rglob("*.md")))

    def test_ai_source_cannot_seed_auto_fact(self):
        source=self.source("ai.md","A generated model claim.\n")
        side=self.brain.safe_path(source).with_suffix(".md.meta.json"); metadata=json.loads(side.read_text(encoding="utf-8")); metadata["allow_fact_promotion"]=False
        module.atomic_json(side,metadata)
        result=self.pipeline.promote_fact(source,{"claim":"A generated model claim.","subject":"Claim","predicate":"value","target_kind":"concept"})
        self.assertEqual(result["status"],"draft"); self.assertEqual(result["reason"],"ai-source-requires-review")

    def test_ai_source_fact_candidates_are_bounded_and_aggregated(self):
        source=self.source("conversation.md","Generated claim.\n")
        side=self.brain.safe_path(source).with_suffix(".md.meta.json"); metadata=json.loads(side.read_text(encoding="utf-8")); metadata["allow_fact_promotion"]=False; module.atomic_json(side,metadata)
        facts=[{"claim":"Generated claim.","subject":f"Topic {number}","predicate":"value","target_kind":"entity"} for number in range(40)]
        result=self.pipeline.process_candidates(source,{"facts":facts,"syntheses":[],"decisions":[]})
        self.assertEqual(len(result["facts"]),1); self.assertIn("fact-review/",result["facts"][0]["path"])
        self.assertEqual(len(list((self.brain.docs/"drafts"/"fact-review").glob("*.md"))),1)

    def test_queue_is_idempotent_and_fake_librarian_runs(self):
        source=self.source("queue.md","LocalBrain is local.\n")
        self.assertTrue(self.pipeline.enqueue(source)["queued"]); self.assertFalse(self.pipeline.enqueue(source)["queued"])
        class Extractor:
            def extract(_,source_path,text,metadata):
                return {"facts":[{"claim":"LocalBrain is local.","subject":"LocalBrain","predicate":"location","target_kind":"entity"}],"syntheses":[],"decisions":[]}
        result=self.pipeline.run_queue(extractor=Extractor()); self.assertEqual(result["pending"],0)
        self.assertTrue((self.brain.docs/"wiki"/"entities"/"localbrain.md").exists())

    def test_direct_raw_drop_is_discovered_and_secret_is_not_queued(self):
        plain=self.brain.docs/"raw"/"dropped.md"; plain.write_text("Directly copied source.\n",encoding="utf-8")
        secret=self.brain.docs/"raw"/"secret-drop.txt"; secret.write_text("connection_string=Server=db;Password=abcdefghijklmnop\n",encoding="utf-8")
        result=self.pipeline.discover_raw(); self.assertEqual(result["discovered"],1); self.assertEqual(result["quarantined"],1)
        state=json.loads(self.pipeline.queue_path.read_text(encoding="utf-8")); self.assertEqual([row["source_path"] for row in state["items"]],["raw/dropped.md"])
        secret_meta=json.loads(secret.with_suffix(".txt.meta.json").read_text(encoding="utf-8")); self.assertEqual(secret_meta["status"],"archived")
        self.brain.index(); self.assertFalse(self.brain.search("Password=abcdefghijklmnop")["results"])

    def test_legacy_raw_metadata_is_enriched_but_not_auto_trusted(self):
        path=self.brain.docs/"raw"/"legacy.md"; path.write_text("Legacy generated claim.\n",encoding="utf-8")
        module.atomic_json(path.with_suffix(".md.meta.json"),{"project":"old","status":"active","evidence_level":"confirmed"})
        self.pipeline.discover_raw(); metadata=json.loads(path.with_suffix(".md.meta.json").read_text(encoding="utf-8"))
        self.assertTrue(metadata["source_id"].startswith("source:")); self.assertFalse(metadata["allow_fact_promotion"])


if __name__=="__main__": unittest.main()
