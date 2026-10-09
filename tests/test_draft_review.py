import hashlib
import importlib.util
import json
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("localbrain", ROOT / "src" / "localbrain.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
from draft_review import DraftReviewer, statements


class DraftReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="draft-review-test-")
        self.brain = module.Brain(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def source(self, name, text):
        relative = f"raw/{name}.md"
        path = self.brain.docs / relative
        path.write_text(text, encoding="utf-8")
        checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        module.atomic_json(path.with_suffix(".md.meta.json"), {
            "status": "active", "evidence_level": "confirmed",
            "source_id": "source:" + checksum, "source_hash": checksum,
            "source_type": "md", "source_date": "2026-09-28T00:00:00+00:00"})
        return relative

    def test_two_source_review_promotes_and_is_idempotent(self):
        one = self.source("one", "Port GATEWAY_PORT is protected by mTLS on the Gateway.\n")
        two = self.source("two", "SecondBrain search combines exact and semantic matches.\n")
        body = "Port GATEWAY_PORT is protected by mTLS.\n\nSecondBrain search combines exact and semantic matches."
        created = self.brain.propose("review-0001", "Verified summary", body, references=[one, two])
        calls = []

        def judge(packet, pass_number):
            calls.append(pass_number)
            return {"verdict": "approve", "items": [
                {"id": 1, "source_path": one, "source_quote": "Port GATEWAY_PORT is protected by mTLS on the Gateway."},
                {"id": 2, "source_path": two, "source_quote": "SecondBrain search combines exact and semantic matches."}]}

        reviewer = DraftReviewer(self.brain, judge)
        draft = self.brain.docs / created["path"]
        result = reviewer.review(draft)
        self.assertEqual(result["state"], "approved")
        self.assertEqual(calls, [1, 2])
        promoted = self.brain.docs / result["promoted_to"]
        meta = json.loads(promoted.with_suffix(".md.meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["review_passes"], 2)
        self.assertEqual(len(meta["statements"]), 2)
        self.assertTrue(self.brain.search("Port GATEWAY_PORT")["results"])
        self.assertEqual(reviewer.review(draft)["state"], "skipped")

    def test_missing_evidence_never_calls_model_or_enters_search(self):
        created = self.brain.propose("review-0002", "Unverified", "UNVERIFIED-REVIEW-TEXT")
        def judge(*_):
            self.fail("model called without sources")
        result = DraftReviewer(self.brain, judge).review(self.brain.docs / created["path"])
        self.assertEqual(result["state"], "needs_evidence")
        self.assertFalse(self.brain.search("UNVERIFIED-REVIEW-TEXT")["results"])
        self.assertEqual(DraftReviewer(self.brain, judge).run_once()["results"], [])

    def test_unsupported_quote_keeps_draft(self):
        one = self.source("one", "Port GATEWAY_PORT is protected by mTLS on the Gateway.\n")
        two = self.source("two", "SecondBrain search combines exact and semantic matches.\n")
        created = self.brain.propose("review-0003", "False claim", "Port GATEWAY_PORT is open to all clients.", references=[one, two])
        def judge(*_):
            return {"verdict": "approve", "items": [{"id": 1, "source_path": one,
                    "source_quote": "An invented quote that is not in any source."}]}
        result = DraftReviewer(self.brain, judge).review(self.brain.docs / created["path"])
        self.assertEqual(result["state"], "needs_evidence")
        self.assertFalse(list((self.brain.docs / "wiki" / "synthesis").glob("auto-*.md")))

    def test_stale_source_hash_blocks_approval(self):
        one = self.source("one", "Port GATEWAY_PORT is protected by mTLS on the Gateway.\n")
        two = self.source("two", "SecondBrain search combines exact and semantic matches.\n")
        (self.brain.docs / one).write_text("The source was changed after provenance was recorded.\n", encoding="utf-8")
        created = self.brain.propose("review-0005", "Stale evidence", "Port GATEWAY_PORT is protected by mTLS.", references=[one, two])
        reviewer = DraftReviewer(self.brain, lambda *_: self.fail("stale evidence reached model"))
        self.assertEqual(reviewer.review(self.brain.docs / created["path"])["state"], "needs_evidence")

    def test_decision_language_in_writeback_stays_draft(self):
        one = self.source("one", "Port GATEWAY_PORT is protected by mTLS on the Gateway.\n")
        two = self.source("two", "SecondBrain search combines exact and semantic matches.\n")
        created = self.brain.propose("review-0006", "Policy proposal", "We decided to delete old sources.", references=[one, two])
        reviewer = DraftReviewer(self.brain, lambda *_: self.fail("decision-like writeback reached model"))
        self.assertEqual(reviewer.review(self.brain.docs / created["path"])["state"], "needs_evidence")

    def test_decision_is_never_automatically_reviewed(self):
        created = self.brain.propose("review-0004", "Decision", "Change production model", kind="decision")
        reviewer = DraftReviewer(self.brain, lambda *_: self.fail("decision reached model"))
        self.assertEqual(reviewer.review(self.brain.docs / created["path"])["state"], "skipped")

    def test_statement_ids_cover_paragraphs_and_bullets(self):
        parts = statements("# Title\n\nIntro.\n\n- A fact.\n- Another fact.")
        self.assertEqual([part["id"] for part in parts], [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
