# Slack execution contract v2 (candidate)

This document describes the isolated candidate, not the running ServerPC. Deployment and main merge require a separate authorized operation.

## Deadline and bounds

The candidate preserves the observed output-aware deadline correction:

    output_tokens = min(requested_max_tokens, 1024)
    input_allowance = 30 + 15 * ceil(max(0, UTF8_input_bytes - 2048) / 2048)
    output_allowance = 30 * ceil(output_tokens / 256)
    deadline_seconds = min(requested_timeout_seconds, 120,
                           max(input_allowance, output_allowance))

Chat accepts 5–120 seconds and up to 16,000 UTF-8 input bytes. Review has two passes within one absolute deadline of at most 60 seconds. The model output cap stays 1,024 tokens, concurrency stays one, and foreground priority stays higher than restricted background work. These bounds do not promise completion within a particular latency.

## Attempt identity and idempotency

ServerPC has no standalone Attempt identifier. A Host attempt is bound to request_id, the canonical SHA256 of the action and complete request, session_id and operation. A Client attempt record must preserve that binding. The same request_id with changed input conflicts. A new identifier is a new operation, not a transport retry.

| Observation | Meaning | Automatic model retry |
|---|---|---|
| complete | A complete result was persisted; an exact replay returns it | No |
| interrupted | An interrupted result was persisted; an exact replay returns its safe metadata | No |
| running | The same reserved request is registered in this server process | No |
| unresolved | A nonterminal reservation exists without matching live ownership, or persistence is uncertain | No |
| deferred, retry_safe=true | No durable reservation was made by this attempt | Only the exact same bound request under the existing admission policy |
| unknown from status | No matching persisted record is visible; this is not proof that no attempt happened | No |

An active/orphan reservation is never converted into a persisted interruption merely by replay or status lookup. Legacy persisted interrupted receipts retain their existing minimal metadata; the candidate does not invent a historical reason. Schema and historical rows are not migrated.

A terminal receipt can only replace this operation's active reservation. A later finalizer cannot overwrite an already terminal result. When saving a terminal result fails, the caller has an uncertain operation: preserve its identifier and inspect status; do not fabricate success, delete the reservation, or run the model again.

## Cancellation boundary

The existing session-bound cancel request shape is unchanged. cancelled=true acknowledges that cancellation was accepted before this operation entered terminal finalization. It is not proof that the transport has drained or that an interrupted receipt is already durable. The Host must observe the bound status until a terminal result or an unresolved state is established.

An accepted cancellation before finalization prevents a complete result from being persisted, including the interval after model lease release and before terminal saving. Once terminal finalization begins, late cancellation returns cancelled=false. The in-memory registration remains until terminal saving has completed or failed.

Transport abort happens outside the service registration lock and the per-run cancellation lock. Lease cancellation retains its first known cause. explicit_cancel means a cancel request reached ServerPC; it does not identify whether the human clicked Cancel or Client timeout/shutdown logic sent it. No caller-provided exception, private source, OCR, answer or arbitrary reason text is exported as diagnostics.

## Activity observation

Authenticated control status gains restricted_activity with schema localbrain.slack-activity.v1. It returns counts only:

- scope: chat_review_apply
- available, stable
- active_requests, active_applications, nonterminal_runs
- quiescent, snapshot_only=true, admission_fenced=false

Counts cover registered chat/review operations through terminal persistence, in-flight apply operations, and nonterminal durable runs. An observed registration cycle invalidates stability even if counts return to zero. Missing/unsafe/locked/unreadable storage produces available=false and quiescent=false without initializing or migrating the DB.

This snapshot is not a maintenance lease. It does not block new requests and does not cover Client RAM queues, proposal/retirement writes or every external requester. It must never authorize production stop/restart on its own. Maintenance still needs a separately established requester/admission fence and fresh identity/state checks.

## Capability negotiation

The existing Host-only authentication boundary is unchanged. Capabilities add:

- execution_contract: localbrain.slack-execution.v2
- nonterminal_states: running, unresolved
- cancel_ack_is_terminal: false
- attempt_identity: request_id+request_hash+session_id+operation
- quiescence_scope: chat_review_apply_snapshot_only

Existing privacy flags stay false for external_ai, cloud_fallback, automatic_adoption and ordinary_secondbrain_adoption. Clients must understand the new nonterminal responses before production deployment. A real Host-authenticated capability observation and Client integration are separate validation steps; source inspection and public stubs do not establish them.
