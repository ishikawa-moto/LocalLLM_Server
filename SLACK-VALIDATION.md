# Slack Server candidate validation — 2026-10-19

Candidate only; no deployment, service restart, actual Slack message or real Qwen generation occurred.

- Baseline a9a911e public fixture regression: **109 tests, zero failures/errors/skips**, including 42 Slack contract, scheduling, socket cancellation, restricted storage, human adoption, Rule, integrity and status tests. Final run: 18.843 seconds. This is test-suite time, not model performance.
- JavaScript syntax and diff consistency: PASS. Existing shared LAN Chat history, host/origin guards, normal lifecycle queue and draft-review regressions pass.
- Blocked local SSE fixture: foreground admission interrupts the original background socket, discards partial output and acquires the shared inference slot within the test's two-second bound. Real Qwen cancellation time is unqualified.
- Rule scope: only Slack locale/style enums, immutable before/delta/after and base/new hashes, stale-base rejection, no ordinary Actor/Continue/security/auth/path changes. Failure after a Rule write rolls back Rule state, acceptance and approval consumption together.
- Human authority: exact pinned Host certificate/IP/owner, proposal/review/target/base/new/Session/administrator/expiry/nonce/request binding; duplicate application is idempotent. The Host's actual administrator UI and certificate-to-administrator registration are not tested here.
- Persisted proposal/review bodies are rehashed before use. Body edits under old stored hashes reject. Unknown restricted database schemas fail closed without automatic migration.
- Derived content carries the restricted classification; no normal knowledge/index/TaskMemory/export integration is called. Public test connections are constrained to test-created loopback listeners; one original Brain regression uses a separate temporary public HTTP child server. No real Slack source was supplied.

Review: self-review only. Independent review, paired Client execution/canonicalization, real mTLS handshake, native administrator/CSRF/receipt flow, paired Host retirement/outbox integration and physical purge across WAL/backups, real Slack, mobile/LAN reachability, production throughput and cold-start priority qualification remain open. `enabled` and `human_apply_enabled` are false in configuration examples. Model/context/KV/auth/service configuration is unchanged. No claim is made that the complete Client/Server Slack feature is production-ready.

## Rollout and rollback boundaries

Keep the draft PR unmerged until the paired qualifications are complete. Use the original production source hash and current Git state as the starting point; stop on drift. Resolve actual Gateway and Chat launch/task bindings rather than assuming historical names. With separate human authorization, verify safe-idle, preserve exact bytes of all replaced source files, create a private SQLite backup if restricted state exists, then stop only the approved services before replacing the seven product files. Configure the Host certificate pin and HTTPS entry separately; do not copy example placeholders as real settings.

For code rollback, stop only those approved services after safe-idle, require each deployed file to still match the frozen candidate hash, restore its exact pre-change source bytes and remove only newly introduced files matching the frozen candidate. Unknown edits mean HOLD. Do not delete or restore the live restricted database over newer Sessions/approvals; retain it privately for reconciliation. Rule transaction failures roll back automatically. Reversing a successful Rule needs a new exact human-approved inverse proposal against the current base, rather than silently rewinding unrelated state.

## Retirement-only candidate addition

The added `POST /v1/slack/retire` follows the exact contract in SLACK-SERVER.md. Host controls policy and commits its deletion/outbox first; no Server retention scheduler was added. Sixteen **additional retirement-only** public-fixture tests pass, zero failures/errors/skips (2.674 seconds). The previous 109 tests were not rerun for this addition. No production, new worker, model, branch or service operation was performed.

The added tests cover existing Host authentication, strict schema/restriction, no inference, Session-only cleanup and zeroed deleted DB cells, preserved accepted knowledge/Rule/two-pass audits and applied status, idempotency, live/uncertain/application/writer deferral without cancellation or lock theft, both admission-retirement orderings, duplicate pre-reservation ownership, retired Session/ID resurrection denial and rollback after a partial deletion failure. Self-review only; independent review and the paired Host outbox/retention, real Slack/LAN/mobile and production performance qualifications remain open. Do not describe these separate test runs as one combined rerun.

Source rollback after using retirement must preserve tombstone enforcement or leave Slack disabled. Retain adopted audit and the current private database; do not restore stale state that resurrects deleted Sessions.
