# Server integration audit and isolated correction

Date: 2026-10-23. Scope: read-only production inspection, isolated candidate and public fixtures only. No deployment, main merge, model execution, real Slack posting, original-request replay, authentication change or production DB mutation is part of this change.

## Relationship to running code

The observed main was ec26ab902c920647f8dba0d3f3489eed4af0f821. Production had four uncommitted output-aware deadline changes: src/slack_gateway.py, tests/test_slack.py, tests/test_slack_interruptions.py and SLACK-INTERRUPTION-DIAGNOSTICS.md.

Their captured patch SHA256 is 3e5c597cc6a2f56f240b157e4f7fcfbbc7992d12f56a0dc15adce000c4b81324. It matches the existing output-aware deployment receipt. This candidate preserves that correction and adds the execution-contract fixes described in SERVER-EXECUTION-CONTRACT.md. It does not rewrite those production files.

Observed production source SHA256:

| File | SHA256 |
|---|---|
| src/control_gateway.py | af87ee9394a1e5f266d3e5a4ce72321b31a971b7eb9dd518f479c02839b74765 |
| src/slack_gateway.py | 21beac736bc9907641a3a23eb02d59b8fe9c9aae41f062b615235030019d3d50 |

The Gateway PID and birth time matched the output-aware deployment receipt, and its task definition remained unchanged. Normal privileges did not expose the SYSTEM process image/command line, so this audit does not claim a fresh loaded-image/source hash attestation. Configured dependencies were hashed separately from loaded processes.

Read-only control status reported Gateway/Brain/Proxy healthy, no loaded Qwen model and zero active/queued jobs. Reviewer task was Ready with no task instance; its saved marker was not pending. These observations do not prove all Client RAM queues or every independently dispatched reviewer process are idle.

The historical Client capability attestation was preserved. This audit did not spoof the Host identity or make a fresh Host-authenticated capabilities request, and does not certify a current Client/Server integration result.

## Reproduced defects

1. Replaying a reserved live request returned interrupted even while its model callback was still running. A surviving active reservation also looked terminal through replay although bound status correctly said unresolved.
2. Cancellation accepted after lease release could still be followed by a complete receipt.
3. Registration was removed before terminal persistence. During that gap, status could say unresolved even though an owned finalizer was saving the result.
4. Cancel transport I/O ran under the global registration lock, unnecessarily blocking registration/status/retirement users of that lock.

The candidate preserves registrations through finalization, serializes cancel acceptance against terminal finalization, prevents terminal overwrite, uses running/unresolved for nonterminal replay and exposes an explicitly limited read-only activity snapshot. The execution-state correction itself does not introduce retries, force resolution, a DB reset or an implicit maintenance lock; bounded recovery is governed separately by SLACK_RECOVERY.md.

## Validation

- Initial public contract reproduction: 10 tests, 4 assertion failures and 6 errors. Errors include intentionally absent candidate-only capability/snapshot APIs; the concurrency/state failures were also observed directly.
- Final Slack fixture suite: 107 PASS, 0 failures, 0 errors, 0 skips; includes 14 new contract regressions.
- Existing Gateway fixture suite: 14 PASS, 0 failures, 0 errors, 0 skips.
- Network access was restricted to listeners bound by the public test runner. No production configuration, model endpoint, Slack account or private data was used.

Local evidence retains hashes for 103 tracked files, 23 configuration files and 59 configured dependency binaries. Private configuration values, operational receipts and DB payloads are excluded from this PR. The configured Python reported 3.12.14; installed cryptography was 50.0.2 while requirements-gateway.txt pins 50.0.1. That dependency discrepancy is reported without changing or installing production packages. GGUF weights were not rehashed and no live model compatibility claim is made.

## Remaining boundary

Client consumption of v2 nonterminal responses/cancel acceptance, actual Host capability negotiation, cold-start latency, private input/OCR behavior and real inference remain unverified. The activity snapshot is not a global quiescence fence. This candidate has not been deployed or merged into main.

## Parent integration

The onsite audit commit `2cdd796869cf696bd65e563c72c2ca0719b49a7f` is integrated into the existing bounded-recovery candidate (Server PR 4). The output-aware deadline implementation is shared, rather than duplicated. Both `localbrain.slack-execution.v2` and `quiescent-attempt-v1` are advertised; the pre-existing recovery lineage, quiescence probe and owned-socket safeguards remain. After resolving these feature-branch conflicts, the full combined public regression passed 187 tests with zero failures, errors or skips. Client in-memory transport fixtures also passed 26 checks for running/unresolved deferral, immutable status binding, no second generation transport and terminal result recovery. No production checkout or service is modified by this integration.