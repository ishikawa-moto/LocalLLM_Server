# Slack interruption metadata and bounded Chat deadlines

This candidate changes only the local ServerPC Slack execution path and its public stub tests. It does not enable browser Internet search, cloud fallback, tools, model retries, ordinary knowledge adoption, or a new service.

## Deadline contract

The request's timeout_seconds remains an integer from 5 through 120. The server advertises chat_timeout_seconds=120 as its maximum. Chat execution uses:

    min(requested_seconds, 120, 30 + 15 * ceil(max(0, UTF8_input_bytes - 2048) / 2048))

Input bytes include context, the newline separator, and prompt; the 16,000-byte ceiling stays unchanged. Output stays capped at 1,024 model tokens. Small prompts receive at most 30 seconds, a synthetic 10,902-byte input receives at most 105 seconds, and the upper input boundary receives at most 120 seconds. A caller requesting 30 seconds still receives at most 30 seconds.

This is a bounded scheduling allowance, not a measured latency or successful-answer guarantee. Server and Client must agree on capabilities, the requested timeout, and the HTTP waiting allowance before cutover. An old Client that still requests or waits only 30 seconds will not benefit. The deadline is absolute across execution and review passes; review remains capped at 60 seconds total. Connected stream cancellation uses the existing owned socket; the numeric-loopback TCP connection attempt itself is capped at one second.

## Safe terminal metadata

New interrupted receipts keep status=interrupted and retry_safe=false. They may add a fixed reason:

- deadline_exceeded: execution's absolute deadline expired.
- foreground_preempted: a normal foreground request cancelled this background lease.
- background_preempted: a higher-priority restricted background operation cancelled it.
- explicit_cancel: the authenticated Host cancelled the same request and session.
- transport_timeout, upstream_failure, invalid_output, background_unavailable, cancelled, execution_failed: stable categories with no exception text.

The first lease cancellation cause wins. Marking cancellation while holding the controller condition lock performs no transport I/O. Foreground admission remains atomic with cancellation, background concurrency stays one, and no interrupted request is automatically resubmitted.

diagnostics contains only whitelisted numeric values (input_bytes, max_tokens, requested_timeout_seconds, deadline_seconds, elapsed_ms, started_at_unix_ms, finished_at_unix_ms) and a fixed phase (admission/generation/validation). Elapsed time uses the monotonic clock; wall-clock timestamps are correlation metadata and may move if the system clock changes. No prompt, OCR text, exception message, path, credentials, or provider output enters this metadata. It remains in the separate restricted receipt JSON; ordinary telemetry receives no Slack identifiers.

The authenticated status endpoint and an exact same-request replay return only sanitized metadata. Legacy active/interrupted receipts without a cause remain interrupted/retry_safe=false without an invented historical reason. No database schema migration or rewrite is required, and existing failed requests are never reexecuted by this candidate.

## Validation and rollout boundary

Public tests use synthetic input and stub/fixture transports only. They cover the byte/deadline boundaries, actual foreground socket interruption, explicit cancellation, first-cause retention, single background lease, legacy receipt behavior, replay/status metadata, delayed timer callbacks, and secret-bearing exception suppression.

Production deployment, Gateway restart, model startup, real inference, original-request resubmission, and real Slack posting are outside preparation. Before a separate cutover approval, bind the exact source hashes, compare against the currently loaded historical Gateway/Slack sources as well as main, confirm Client timeout compatibility, preserve existing configuration and DB, obtain no-active/no-queued request maintenance confirmation, check fresh idle status, and prepare an owned-identity-only rollback. An independent review and actual deployment/integration validation have not been performed by this packet.
