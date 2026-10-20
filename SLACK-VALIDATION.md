# Slack Server validation

## Implemented scope and bounded startup qualification

The Server can opt in to local model startup for validated, Host-authenticated chat. The public configuration example keeps enabled, human_apply_enabled and model_cold_start false. Internal mTLS, Host source/owner checks, certificate pinning, restricted-data egress rules and existing model/context/KV settings are preserved.

- The complete isolated public regression suite passed: **142 tests, zero failures, errors or skips**, including nine new startup tests and the existing SSE, cancellation, restriction, lifecycle, adoption and retirement tests. Fixtures use test-created loopback listeners rather than production configuration or models.
- Startup tests cover cold and warm state, coalescing concurrent starts, foreground work joining STARTING, queued/active foreground priority, base-service readiness, owned-start stop exclusion, failure without automatic retry or StopLLM, invalid input and non-starting read/review paths.
- Limited independent read-only review reported no findings for the startup delta and its exact three-target deployment/rollback procedure. This does not constitute independent qualification of every feature in this PR.
- A separately authorized public-input check confirmed STOPPED → STARTING → READY → completed response through the existing authenticated Client path. The first chat returned explicit pre-inference deferred with retry_safe:true; only the identical request was submitted after readiness. There was one inference, its public marker matched, its Session retired and final idle was confirmed. No forced stop or additional model trial was used.
- Existing SSE handling accepts role-null, finish and usage-only metadata while preserving malformed-content, completion-marker, frame-size and cancellation checks. Public tests exercise these boundaries.

This bounded check does not qualify every real Slack, OCR/compression, reconnection/recovery, human knowledge/Rule application, paired retention/outbox, priority-load or other PC/mobile workflow. Client authentication and attachment implementation are maintained separately. No private Slack body, OCR text, generated answer, credentials or database contents are included in this repository or its review evidence.

## Approved Chat entry and human approval

On 2026-10-21 a separately approved installation deployed the Chat entry and enabled human-bound restricted knowledge and enumerated Slack-response Rule application. The six-target rollout used exact source/config preimages and owned normal Chat/Gateway restarts. Other model, authentication and service settings were preserved. No inference or production application test was run for this rollout.

- The live Chat entry was enabled and its static HTML/script hashes matched the reviewed public source. It links directly to the Client HTTPS password page from below the new-chat button. HTTP chat does not authenticate or proxy approval. An already open Chat page needs reloading to display the entry.
- ClientPC independently checked HTTPS login 200 and unauthenticated proposal access 401 without an administrator browser certificate. Existing Host mTLS/IP/owner authentication reported human restricted adoption and Rule activation enabled, with automatic/ordinary SecondBrain adoption, external AI and cloud fallback disabled.
- The unchanged approval/menu sources passed 42 approval tests and 12 Chat tests, plus two focused public approval-boundary scenarios. ClientPC ran 101 isolated public tests. These are separately scoped runs, not a replacement or inflated total for the earlier 142-test full regression. Candidates alone left accepted knowledge/Rule unchanged; forged approval failed; the exact Host receipt applied each public knowledge/Rule fixture once. Limited independent review of the fixed rollout reported no findings.
- The actual rollout retained old receipts and backups. Current target hashes and service PID/birth/listener bindings matched the owned deployment. Current direct Chat task-instance rereading was permission-limited; its exact deployment receipt was retained. No extra UAC or restart was used to recheck it.

Candidates remain pending until an authenticated administrator explicitly approves their exact content. This qualification does not assert that a real private proposal was adopted, a production Rule was changed as a test, or a phone approval workflow was exercised. The public templates still default to disabled.

## Lifecycle and retry boundaries

Only a validated authenticated chat can request startup, and only when the local opt-in flag permits it. STARTING is shared rather than launching parallel starts. Foreground admission remains ahead of chat and review. A failed background start does not call StopLLM or automatically try again. Status, capabilities and autonomous review do not start a model.

An explicit deferred response before durable reservation is retry-safe; the Host may later resubmit the identical request after readiness. A timeout, interrupted response or uncertain reservation is not permission to infer again. Preserve existing request/Session identity and inspect durable status. Do not manufacture a cold test by stopping a warm model.

## Deployment and rollback

The implemented scope is ready for review against main; the remaining qualifications are explicit limits. Merging does not deploy or activate a site. Each production change requires its exact authorized scope, known preimages and independently reviewed procedure. The startup change replaces two Gateway sources and adds only the local model_cold_start configuration flag. The public disabled example is not a production configuration.

Before stopping the registered Gateway task, verify fresh safe-idle, exact task instance GUID/PID/process birth, executable and argument binding, unchanged protected dependencies and exact source/config hashes. Persist the scheduler-returned start identity before health checks. Do not kill a PID, repair a task or stop other services to perform this rollout.

Rollback requires the owned execution receipt, exact known old/new bytes and backup hashes, unchanged dependencies and a new safe-idle observation. Unknown edits, busy state, unreachable status, an incomplete receipt or a replacement process require reconciliation. Preserve current private databases, accepted knowledge, Rule policy, receipts and retirement tombstones; never restore stale state that resurrects a retired Session.

## Earlier fixture evidence

The initial candidate passed 109 public fixture tests, followed by 16 retirement-only tests. The SSE follow-up subsequently passed the complete 133-test suite. The current 142-test result is a new complete run for the startup source change, not a sum presented as a single historical run.

Retirement fixtures cover authentication, strict restriction/schema checks, no inference, Session-only cleanup, preservation of accepted knowledge/Rule/adopted two-pass audit, exact replay, active/unresolved/application/writer deferral, admission races, tombstones and transaction rollback. SQLite secure_delete covers deleted database cells; forensic removal from WAL, backups and storage remains outside that fixture claim.
