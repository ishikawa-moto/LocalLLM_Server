import hashlib
import importlib.util
import json
import pathlib
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("localbrain", ROOT / "src" / "localbrain.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
from draft_review import DraftReviewer, statements
from approved_apply import ApplyEngine, HOST_OWNER, Rejected, review_hash
from decision_review import DecisionReviewer, POLICY, validate_automatic_approval
from site_settings import site_value


class DraftReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="draft-review-test-")
        self.brain = module.Brain(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def source(self, name, text, project="general"):
        relative = f"raw/{name}.md"
        path = self.brain.docs / relative
        path.write_text(text, encoding="utf-8")
        checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        module.atomic_json(path.with_suffix(".md.meta.json"), {
            "status": "active", "evidence_level": "confirmed", "project": project,
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

    def test_unlisted_project_can_be_reviewed_but_review_cannot_authorize_adoption(self):
        for args in (("init", "-b", "fixture"), ("config", "user.name", "Review Fixture"),
                     ("config", "user.email", "fixture@example.invalid")):
            subprocess.run(["git", "-C", str(self.brain.docs), *args], check=True, capture_output=True)
        project = "review-fixture"
        sentences = ["The first synthetic source documents retry behavior.",
                     "The second synthetic source documents task identity."]
        paths = [self.source(str(i), sentence + "\n", project) for i, sentence in enumerate(sentences)]
        refs = [{**{k: v for k, v in ApplyEngine(self.brain).snapshot(path).items()
                   if k in ("path", "expected_hash", "expected_metadata_hash")}, "relevant_location": "L1"} for path in paths]
        proposal_id = "review-only-example"
        body = "\n\n".join(sentences)
        draft = self.brain.propose_bound_decision(proposal_id, "Synthetic review", body, project, refs)
        policy = {"version": 1, "enabled": True, "policy": POLICY,
                  "host_policy_hash": "a" * 64, "projects": ["other-project"]}
        policy_path = self.brain.root / "config" / "decision-review-policy.json"
        policy_path.parent.mkdir(exist_ok=True)
        module.atomic_json(policy_path, policy)
        client = {"approval_owner": HOST_OWNER, "address": site_value('client_address', 'localbrain-client', self.brain.root),
                  "certificate_sha256": "b" * 64, "source": "mTLS Gateway / trusted Continue approval"}
        calls = []

        def judge(packet, number):
            calls.append(number)
            return {"verdict": "approve", "items": [{"id": i + 1, "source_path": path, "source_quote": sentences[i]}
                                                     for i, path in enumerate(paths)]}

        reviewer = DecisionReviewer(self.brain, judge)
        with patch("draft_review_queue.signal"):
            with self.assertRaisesRegex(Rejected, "Review scope differs"):
                reviewer.register(proposal_id, "proposal", policy["host_policy_hash"], "test-task", "test-workspace", client)
            reviewer.register(proposal_id, "review_only", policy["host_policy_hash"], "test-task", "test-workspace", client)
        self.assertEqual(reviewer.review(self.brain.docs / draft["path"])["state"], "reviewed_only")
        self.assertEqual(calls, [1, 2])
        receipt = reviewer.status(proposal_id)
        self.assertEqual(receipt["apply_state"], "not_started")
        self.assertEqual(self.brain.get_proposal(proposal_id)["status"], "draft")
        # Even a later allowlist addition cannot turn the exact review-only receipt into an adoption grant.
        policy["projects"].append(project)
        module.atomic_json(policy_path, policy)
        with patch("draft_review_queue.signal"), self.assertRaisesRegex(Rejected, "another exact request"):
            reviewer.register(proposal_id, "proposal", policy["host_policy_hash"], "test-task", "test-workspace", client)
        payload = {"request_id": proposal_id, "project": project, "title": "Synthetic review", "content": body,
                   "references": refs, "origin_proposal_id": proposal_id, "expected_proposal_hash": draft["proposal_hash"]}
        now = int(time.time())
        payload["approval"] = {"version": 3, "owner": HOST_OWNER, "approval_id": "test-approval", "task_id": "test-task",
                               "request_id": proposal_id, "operation": "decision", "proposal_id": proposal_id,
                               "proposal_hash": draft["proposal_hash"], "decision_hash": review_hash("decision", payload),
                               "issued_at": now, "expires_at": now + 300, "provenance": "reviewer-approved-model-proposal",
                               "host_policy_hash": policy["host_policy_hash"], "review_receipt_hash": receipt["review_receipt_hash"],
                               "reaffirmation_receipt_hash": None}
        with self.assertRaisesRegex(Rejected, "cannot authorize automatic adoption"):
            validate_automatic_approval(self.brain, "decision", payload, client)


if __name__ == "__main__":
    unittest.main()
