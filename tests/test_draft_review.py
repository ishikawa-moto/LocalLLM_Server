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
from draft_review import DraftReviewer, ReviewLease, statements
from approved_apply import ApplyEngine, HOST_OWNER, Rejected, review_hash
from decision_review import CombinedReviewer, DecisionReviewer, MODEL, POLICY, SCHEMA
from draft_review_queue import drain
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

    def bound_review(self, proposal_id, source_count=2):
        for args in (("init", "-b", "fixture"), ("config", "user.name", "Review Fixture"),
                     ("config", "user.email", "fixture@example.invalid")):
            subprocess.run(["git", "-C", str(self.brain.docs), *args], check=True, capture_output=True)
        project = "review-fixture"
        sentences = [f"Synthetic source {i} covers the decision {proposal_id}." for i in range(source_count)]
        paths = [self.source(f"{proposal_id}-{i}", sentence + "\n", project) for i, sentence in enumerate(sentences)]
        refs = [{**{k: v for k, v in ApplyEngine(self.brain).snapshot(path).items()
                   if k in ("path", "expected_hash", "expected_metadata_hash")}, "relevant_location": "L1"} for path in paths]
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

        payload = {"request_id": proposal_id, "project": project, "title": "Synthetic review", "content": body,
                   "references": refs, "origin_proposal_id": proposal_id, "expected_proposal_hash": draft["proposal_hash"]}
        return DecisionReviewer(self.brain, judge), self.brain.docs / draft["path"], policy_path, client, payload, calls

    def register(self, reviewer, draft, client, mode):
        with patch("draft_review_queue.signal"):
            return reviewer.register(draft.stem, mode, "a" * 64, "test-task", "test-workspace", client)

    def approval(self, reviewer, payload, provenance, reaffirmation_hash=None):
        proposal_id = payload["request_id"]
        receipt = reviewer.status(proposal_id)
        now = int(time.time())
        return {**payload, "approval": {"version": 3, "owner": HOST_OWNER,
                "approval_id": "approval-" + proposal_id, "task_id": "test-task",
                "request_id": proposal_id, "operation": "decision", "proposal_id": proposal_id,
                "proposal_hash": payload["expected_proposal_hash"], "decision_hash": review_hash("decision", payload),
                "issued_at": now, "expires_at": now + 300, "provenance": provenance,
                "host_policy_hash": "a" * 64, "review_receipt_hash": receipt["review_receipt_hash"],
                "reaffirmation_receipt_hash": reaffirmation_hash}}

    def test_unlisted_project_two_passes_authorize_adoption(self):
        for mode in ("proposal", "user_request"):
            with self.subTest(mode=mode):
                reviewer, draft, policy_path, client, payload, calls = self.bound_review("auto-" + mode)
                self.assertEqual(self.register(reviewer, draft, client, mode)["state"], "queued")
                self.assertEqual(reviewer.review(draft)["state"], "approved")
                self.assertEqual(calls, [1, 2])
                # Dropping the legacy list after review does not invalidate a receipt bound to this Host policy hash.
                policy = json.loads(policy_path.read_bytes())
                del policy["projects"]
                module.atomic_json(policy_path, policy)
                provenance = ("reviewer-approved-model-proposal" if mode == "proposal"
                              else "reviewer-approved-requested-decision")
                request = self.approval(reviewer, payload, provenance)
                engine = ApplyEngine(self.brain)
                http, result = engine.apply("decision", request, client)
                self.assertEqual((http, result["status"]), (200, "applied"), result)
                self.assertEqual(engine.apply("decision", request, client), (http, result))
                self.assertEqual(len(list((self.brain.docs / 'decisions').glob(f"*{draft.stem}.md"))), 1)

    def test_first_and_second_pass_return_once_and_allow_exact_native_reaffirmation(self):
        for mode in ("proposal", "user_request"):
            for failed_pass in (1, 2):
                with self.subTest(mode=mode, failed_pass=failed_pass):
                    reviewer, draft, _, client, payload, calls = self.bound_review(f"{mode}-return-{failed_pass}")
                    approve = reviewer.judge
                    reason = "The selected evidence does not substantiate the recommendation."

                    def judge(packet, number):
                        if number == failed_pass:
                            calls.append(number)
                            return {"verdict": "needs_evidence", "reason": reason}
                        return approve(packet, number)

                    reviewer.judge = judge
                    self.register(reviewer, draft, client, mode)
                    self.assertEqual(reviewer.review(draft)["state"], "returned")
                    status = reviewer.status(draft.stem)
                    record = status["review_receipt"]
                    self.assertEqual(record["return_count"], 1)
                    self.assertEqual(record["review_passes"], failed_pass - 1)
                    self.assertEqual(len(record["passes"]), failed_pass - 1)
                    self.assertEqual(record["issues"], [{"code": "reviewer_issues", "message": reason, "pass_number": failed_pass}])
                    self.assertEqual(status["apply_state"], "not_started")
                    with patch("draft_review_queue.signal") as signal:
                        self.assertEqual(reviewer.register(draft.stem, mode, "a" * 64, "test-task", "test-workspace", client), status)
                        signal.assert_not_called()
                    self.assertEqual(reviewer.review(draft)["state"], "unchanged")
                    self.assertEqual(calls, list(range(1, failed_pass + 1)))

                    request = self.approval(reviewer, payload, "human-reaffirmed-returned-decision", "c" * 64)
                    engine = ApplyEngine(self.brain)
                    for overrides, expected in (({"reaffirmation_receipt_hash": None}, "reaffirmation_required"),
                                                ({"review_receipt_hash": "c" * 64}, "stale_review")):
                        invalid = {**request, "approval": {**request["approval"], **overrides}}
                        with self.assertRaises(Rejected) as failure:
                            engine.host_approval("decision", invalid, client)
                        self.assertEqual(failure.exception.code, expected)
                    with self.assertRaises(Rejected) as failure:
                        engine.host_approval("decision", request, {**client, "certificate_sha256": "d" * 64})
                    self.assertEqual(failure.exception.code, "review_identity_mismatch")
                    with self.assertRaises(Rejected) as failure:
                        automatic = ("reviewer-approved-model-proposal" if mode == "proposal"
                                     else "reviewer-approved-requested-decision")
                        engine.host_approval("decision", self.approval(reviewer, payload, automatic), client)
                    self.assertEqual(failure.exception.code, "review_not_approved")

                    http, result = engine.apply("decision", request, client)
                    self.assertEqual((http, result["status"]), (200, "applied"), result)
                    metadata = engine.snapshot(result["destination"])["metadata"]
                    self.assertEqual(metadata["human_reaffirmation_receipt_hash"], "c" * 64)
                    self.assertEqual(metadata["review_receipt_hash"], status["review_receipt_hash"])
                    self.assertEqual(metadata["review_issues"], record["issues"])
                    self.assertEqual(metadata["review_state"], "human_reaffirmed_with_issues")
                    self.assertTrue(metadata["do_not_treat_as_fact"])

    def test_model_proposal_with_one_source_returns_before_model_and_keeps_source_requirement(self):
        reviewer, draft, policy_path, client, payload, calls = self.bound_review("insufficient-example", source_count=1)
        policy = json.loads(policy_path.read_bytes())
        policy["projects"] = []
        module.atomic_json(policy_path, policy)
        self.register(reviewer, draft, client, "proposal")
        result = reviewer.review(draft)
        self.assertEqual(result["state"], "returned")
        self.assertEqual(result["issues"][0]["code"], "needs_evidence")
        self.assertEqual(calls, [])
        request = self.approval(reviewer, payload, "human-reaffirmed-returned-decision", "c" * 64)
        with self.assertRaises(Rejected) as failure:
            ApplyEngine(self.brain).host_approval("decision", request, client)
        self.assertEqual(failure.exception.code, "needs_evidence")

    def test_timeout_is_terminal_and_cannot_be_reaffirmed_or_use_legacy_approval(self):
        reviewer, draft, _, client, payload, calls = self.bound_review("unknown-example")
        approve = reviewer.judge

        def judge(packet, number):
            if number == 1:
                return approve(packet, number)
            calls.append(number)
            raise TimeoutError("synthetic lost response")

        reviewer.judge = judge
        self.register(reviewer, draft, client, "proposal")
        self.assertEqual(reviewer.review(draft)["state"], "review_unresolved")
        status = reviewer.status(draft.stem)
        self.assertEqual(status["review_receipt"]["review_passes"], 1)
        self.assertFalse(reviewer.is_unfinished(draft))
        self.assertEqual(self.register(reviewer, draft, client, "proposal"), status)
        self.assertEqual(reviewer.review(draft)["state"], "unchanged")
        self.assertEqual(calls, [1, 2])
        request = self.approval(reviewer, payload, "human-reaffirmed-returned-decision", "c" * 64)
        engine = ApplyEngine(self.brain)
        with self.assertRaises(Rejected) as failure:
            engine.host_approval("decision", request, client)
        self.assertEqual(failure.exception.code, "reaffirmation_required")
        legacy = {k: v for k, v in request["approval"].items()
                  if k not in ("host_policy_hash", "review_receipt_hash", "reaffirmation_receipt_hash")}
        legacy.update(version=1, provenance="human-reviewed-model-proposal")
        with self.assertRaises(Rejected) as failure:
            engine.host_approval("decision", {**payload, "approval": legacy}, client)
        self.assertEqual(failure.exception.code, "review_receipt_required")

    def test_interrupted_worker_is_reconciled_without_replaying_review(self):
        reviewer, draft, _, client, _, calls = self.bound_review("interrupted-example")
        self.register(reviewer, draft, client, "proposal")
        approve = reviewer.judge

        def judge(packet, number):
            if number == 2:
                raise KeyboardInterrupt("synthetic process interruption")
            return approve(packet, number)

        combined = CombinedReviewer(self.brain, judge)
        lease = ReviewLease(self.brain.runtime)
        self.assertTrue(lease.acquire())
        try:
            with self.assertRaises(KeyboardInterrupt):
                combined.review(draft)
            self.assertEqual(drain(self.brain, reviewer=combined)["status"], "coalesced")
            self.assertEqual(reviewer.status(draft.stem)["state"], "reviewing")
        finally:
            lease.release()
        self.assertEqual(drain(self.brain, reviewer=combined)["processed"], 0)
        record = reviewer.status(draft.stem)["review_receipt"]
        self.assertEqual(record["state"], "review_unresolved")
        self.assertEqual(record["error_type"], "InterruptedReview")
        self.assertEqual(record["review_passes"], 1)
        self.assertEqual(calls, [1])

    def test_legacy_review_only_stays_non_adoptable(self):
        reviewer, draft, _, client, payload, calls = self.bound_review("review-only-example")
        with self.assertRaises(Rejected) as failure:
            self.register(reviewer, draft, client, "review_only")
        self.assertEqual(failure.exception.code, "legacy_review_mode")
        module.atomic_json(reviewer.record_path(draft.stem), {
            "schema": SCHEMA, "policy": POLICY, "model": MODEL, "proposal_id": draft.stem,
            "proposal_hash": payload["expected_proposal_hash"], "request_mode": "review_only",
            "host_policy_hash": "a" * 64, "task_id": "test-task", "workspace_id": "test-workspace",
            "client_certificate_sha256": "b" * 64, "state": "queued", "issues": [], "review_passes": 0})
        self.assertEqual(self.register(reviewer, draft, client, "review_only")["state"], "queued")
        self.assertEqual(reviewer.review(draft)["state"], "reviewed_only")
        self.assertEqual(calls, [1, 2])
        self.assertEqual(self.brain.get_proposal(draft.stem)["status"], "draft")
        with self.assertRaises(Rejected) as failure:
            self.register(reviewer, draft, client, "proposal")
        self.assertEqual(failure.exception.code, "review_id_conflict")
        for provenance, native_hash in (("reviewer-approved-model-proposal", None),
                                        ("human-reaffirmed-returned-decision", "c" * 64)):
            request = self.approval(reviewer, payload, provenance, native_hash)
            with self.assertRaises(Rejected) as failure:
                ApplyEngine(self.brain).host_approval("decision", request, client)
            self.assertEqual(failure.exception.code, "review_only_no_adoption")


if __name__ == "__main__":
    unittest.main()
