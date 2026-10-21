# Slack Server candidate contract

Status: Server-side contract, ready for review against main. The bounded local startup path has public-input qualification; the approved installation also has a verified Chat approval entry and enabled human-bound application. Full Slack and mobile approval qualification remains separate. The separate Client repository owns Socket Mode, OCR, durable acknowledgement, Session/job retention, user administration, HTTPS/mobile approvals and outbound Slack delivery. Those features are not implemented by this Server change.

Actual public-fixture request/reply pairs for both knowledge and Rule are in `tests/fixtures/slack-contract.json`. Its certificate, approver and owner are fixture values, not deployment identities.

## Configuration and identity

Copy neither production configuration nor credentials into a development worktree. The two example files are disabled placeholders. Actual `config/slack-server.json`, `config/slack-web.json`, `config/private-site.json`, keys and runtime databases remain ignored.

Every Slack request uses the existing Gateway mTLS listener and allowed IP check, plus the exact configured `client_address`, `X-LocalBrain-Approval-Owner` equal to `host_owner`, and the lowercase SHA256 of the DER Windows Host client certificate pinned in `slack-server.json`. A generic trusted client certificate or a Slack member name is insufficient. Use the deployed private-site owner verbatim, including case; the public example owner is only the default. Missing/invalid configuration fails closed (503); wrong Host identity returns 403. Capabilities can be read after Host authentication, even when `enabled` is false.

`human_apply_enabled` defaults to false. Enable it only after separately qualifying the Client Host authentication, administrator registration, exact proposal display, explicit human action, CSRF protection and receipt persistence. Server trusts the authenticated Host's attestation; receipt SHA256 is integrity binding, not an independent digital signature or proof that a human saw a screen. No Slack message directly approves anything. The approved installation now enables this flag and its authenticated capabilities were checked. Public fixtures qualify the approval contract; actual adoption of private proposals and physical other-device approval remain separate qualifications.

## Common exact JSON input

All POST requests reject unknown top-level fields and require:

```json
{"restriction":{"origin":"slack","dataClass":"slack_restricted","externalAi":"deny","cloudFallback":false}}
```

All IDs below match `[A-Za-z0-9_-]{8,80}`. IDs are opaque, not channel names, Slack text, user names or secrets. Request body limit is 65,536 bytes; duplicate JSON keys, nonfinite numbers, duplicate Content-Length and Transfer-Encoding are rejected. All responses are nonstreaming JSON with no-store. No user-supplied model, URL, tools, functions, executable or cloud fallback is accepted.

`GET /v1/slack/capabilities` returns `localbrain.slack-capabilities.v1` and truthful flags. `automatic_adoption`, `ordinary_secondbrain_adoption`, `external_ai` and `cloud_fallback` are false. `model_cold_start` is true only when both `enabled` and the optional `model_cold_start` boolean in the local configuration are true. Omission defaults to false; invalid types or unexpected configuration fields fail closed. Public examples keep startup disabled. `rule_proposal` follows `enabled`; `rule_activation` requires both `enabled` and `human_apply_enabled`. The existing ordinary DecisionProtocol has no safe Slack Rule contract; this candidate adds a separate, tightly limited contract below.

## Chat and review

`POST /v1/slack/chat` exact fields:

```json
{"request_id":"request_fixture_0001","session_id":"session_fixture_01","context":"","prompt":"Public fixture question","max_tokens":1024,"timeout_seconds":30,"restriction":{"origin":"slack","dataClass":"slack_restricted","externalAi":"deny","cloudFallback":false}}
```

`POST /v1/slack/review` replaces `context` and `prompt` with a single string `source`; all other fields are identical. `max_tokens` must be an integer 64..2048; it is clamped to 1024. `timeout_seconds` must be an integer 5..120; it is clamped to 30 for chat and 60 total for review. Combined chat input or review source is at most 16,000 UTF8 bytes. The first review output is at most 8,000 bytes, so the fresh second prompt plus output reservation stays below the unchanged 32K context. No context/KV/model settings are changed.

Chat success: `{"status":"complete","text":"...","classification":{...}}`.

Review runs two fresh local calls, one user prompt each. The first proposes `{kind:"knowledge",body,evidence}` or `{kind:"rule",body,evidence,delta}`; the second independently checks the source and candidate, returning `{pass:boolean,evidence,issues:string[]}`. Evidence must be a literal source quote; invalid output, unsupported evidence, issues, unsafe Rule output or a failed verdict yields `candidate:null`. A model verdict is not human authorization and is not a proof of factual correctness.

Review success has exact keys `status`, `candidate`, `review`, `review_hash`. `candidate` is null, `{kind:"knowledge",body,evidence,review:{second,source_sha256}}`, or the same object with `kind:"rule"` and `delta`. A Rule delta is a nonempty subset of `locale` (`ja` or `en`) and `style` (`brief` or `normal`); all other keys/values reject. `review` is `{schema:"localbrain.slack-review.v1",request_id,session_id,source_sha256,passes:[string,string],candidate,classification}`. Both actual raw local evaluation outputs are retained in restricted storage. `review_hash` is canonical SHA256 of this entire review object. Client owns and retains the original source; Server binds its UTF8 SHA256.

Scheduling shares the actual foreground inference semaphore and admission condition. Priority is foreground > Slack chat > Slack review. A validated, Host-authenticated chat may request one asynchronous normal model start when the local opt-in flag is enabled and base services are ready. Concurrent requests share STARTING, and foreground work retains priority. Review, status and capabilities never request startup. With startup disabled, a stopped model is left stopped. Busy or not-yet-ready requests return `{"status":"deferred","retry_safe":true}` without reserving the request ID or running inference. Only that explicit pre-inference result permits retrying the identical request after readiness; uncertain requests are never blindly retried. A failed background start reaches FAILED, returns `local_model_start_failed` for chat and does not automatically retry or stop the model. Slack does not queue over foreground work or overlap inference. A chat arriving during review cancels review and defers until release. Foreground admission cancels a background lease and shuts down its original loopback socket, including when HTTPResponse has detached the connection socket. No busy-check/POST race is used for admission.

After reservation, timeout, preemption, cancellation, transport failure or uncertain restart returns `{"status":"interrupted","retry_safe":false}` and discards partial text. Never automatically rerun such an ID. A deliberate retry must be a separately recorded attempt with a new ID; do not replay an offline Slack backlog. Completed identical requests return the persisted result; a changed request with the same ID is rejected. HTTP errors contain stable error codes, never source/model exception text or paths.

`POST /v1/slack/cancel`: exact `{request_id,session_id,restriction}`. Returns `{cancelled:boolean}`. The authenticated Host must own the Session; the Server also checks the active Session match. Cancellation remains possible between the two review passes. Ordinary `/v1/control/job-status` does not expose Slack identifiers.

## Durable status (no execution)

`POST /v1/slack/status` exact input `{operation,request_id,session_id,request_hash,restriction}`, where operation is `chat`, `review` or `apply`. All IDs use the same grammar; request_hash is lowercase 64-hex. For chat/review, hash canonical `{action:operation,value:the_original_request_object}`. For apply, hash the entire original apply request object (including its receipt_hash and restriction). Match this value in the Host's immutable durable reservation.

Unknown returns `{state:"unknown",retry_safe:false}`. It does not prove that a concurrent/uncertain request was never accepted. A completed run returns `{state:"complete",request_hash,result,retry_safe:false}`; an atomic applied receipt returns `{state:"applied",request_hash,result,retry_safe:false}`. Active, interrupted and restart-uncertain runs return `{state:"running"|"interrupted"|"unresolved",request_hash,retry_safe:false}`. Wrong Session/hash rejects with `request_binding_mismatch` (409). Status never calls the model or applies anything. Do not automatically resend uncertain applications; reconcile their exact status or retain HOLD.

## Immutable restricted proposal

`POST /v1/slack/proposal`: exact `{proposal_id,review_request_id,session_id,restriction}`. Requires a completed, non-null Server review in the same Session. Client cannot substitute candidate content. Returns `{status:"proposed_restricted",proposal_hash,payload}`. Payload exact fields:

```text
schema = localbrain.slack-proposal.v1
proposal_id, session_id, review_hash, candidate, classification, target, base_hash, new_hash
```

For knowledge, `target` is `restricted-knowledge`, `base_hash` is an empty string, and `new_hash` is the candidate hash. For Rule, `target` is exactly `slack-response-policy`, and the proposal also contains `rule_diff:{before,delta,after}`. Before/after are exact `{locale,style}` values; default is `{locale:"ja",style:"normal"}`. Base/new hashes bind the complete before/after rule object `{schema:"localbrain.slack-rule.v1",target:"slack-response-policy",values:{locale,style},classification}`. Empty changes reject. A repeated proposal returns its original immutable base even after a later adoption.

The proposal hash is SHA256 of canonical payload JSON. Repeating the identical proposal is idempotent; using its ID for different content is rejected. Source SHA256, both actual evaluations, the exact Rule target/diff/base/new hashes and classification stay bound through the candidate/review/proposal hashes.

## Human-only restricted adoption

`POST /v1/slack/apply`: exact `{request_id,proposal_id,proposal_hash,session_id,approval,restriction}`. Disabled unless `human_apply_enabled` is true. `approval` has exactly:

```json
{
  "schema":"localbrain.slack-host-approval.v1",
  "approval_id":"approval_fixture_01",
  "nonce":"nonce_fixture_0001",
  "approver_id":"admin_fixture_001",
  "action":"accept_restricted",
  "proposal_id":"proposal_fixture_01",
  "proposal_hash":"__CANONICAL_PROPOSAL_SHA256__",
  "review_hash":"__CANONICAL_REVIEW_SHA256__",
  "session_id":"session_fixture_01",
  "target":"__EXACT_PROPOSAL_TARGET__",
  "base_hash":"__EXACT_PROPOSAL_BASE_HASH__",
  "new_hash":"__EXACT_PROPOSAL_NEW_HASH__",
  "issued_at":0,
  "expires_at":0,
  "host_owner":"ClientPC-windows-host",
  "host_certificate_sha256":"__PINNED_HOST_CERTIFICATE_SHA256__",
  "receipt_hash":"__CANONICAL_RECEIPT_SHA256__"
}
```

Times are integer Unix seconds: `issued_at <= now < expires_at`, lifetime 1..600 seconds. `receipt_hash` hashes the approval object without `receipt_hash`. All hashes use UTF8 JSON, sorted keys, compact separators, no ASCII escaping, no NaN. Host must use an implementation matching those canonical bytes (including its Unicode escaping); display and persist the exact proposal before issuing a receipt. IDs, proposal/review hashes, target, base/new hashes, Session, action, Host owner/certificate, administrator, expiry and nonce are bound. Client must display the exact target and before/after Rule diff before approval. The public `tests/fixtures/slack-canonical.json` vector includes Japanese, supplementary Unicode, newline and punctuation to check Host canonicalization. The outer request ID is additionally bound by the durable application hash.

Approval ID, nonce, outer request ID and one accepted row per proposal are consumed atomically in SQLite. While the Session is open, identical application retries return the original result even after expiry; changed replays reject. After retirement, reconcile the preserved application through status rather than resubmitting apply. Success includes `{status:"applied_restricted",proposal_id,proposal_hash,target,new_hash,classification}`, **not ordinary SecondBrain adoption**. It writes only restricted storage. A Rule changes only the enumerated Slack chat policy in that same SQLite transaction. The current base hash is checked under the write transaction; stale concurrent proposals reject without consuming approval. Later Slack chats use the resulting locale/style in their system prompt. Ordinary Actor prompts, Continue rules, arbitrary files, Git/shell/admin operations, security, permissions and authentication are unchanged. No LLM is called during adoption.

## Restricted data boundary

Server state lives only in ignored `runtime/slack-restricted/state.sqlite3` (runs, proposals, approval receipts, accepted restricted items, enumerated Slack rule state). Symlink/reparse storage paths are rejected. Nothing is inserted into normal SecondBrain, Chat history, embeddings, TaskMemory or normal source-control/export paths. Original and derived data carry `origin=slack`, `data_class=slack_restricted`, `external_ai=deny`, `cloud_fallback=false`. Restricted route access logging is suppressed; normal lifecycle telemetry gets only a generic background role, no Slack request/Session/source IDs.

Ordinary Gateway proxy routes reject explicit Slack restriction metadata, including nested classification or `X-LocalBrain-Data-Class: slack_restricted`. Server cannot infer classification after a trusted Host removes it. Client RestrictedExecution egress enforcement is mandatory and remains to be jointly qualified. This is not an OS sandbox or a promise against an administrator copying the restricted database. Treat whole-machine/local backups as private; never upload runtime or copied source/receipts as review packets. Host-notified logical Session retirement is implemented below. Server does not run its own 30-day/7-day policy or scheduler. Corpus reading/search and paired retention/outbox qualification remain open; production use remains HOLD.

## Host-notified Session retirement

`POST /v1/slack/retire` uses the same enabled-feature guard, exact Host IP/owner/pinned mTLS certificate, framing limits and restriction policy. Input has exactly `{request_id,session_id,restriction}`; IDs use the same opaque 8..80-character grammar. No reason, source, path, age or model options are accepted. `session_retirement` in capabilities follows `enabled`.

The Windows Host commits its own Session deletion and a non-PII retirement outbox in one transaction after applying the 30-day/7-day rules. Before doing so, it must protect Sessions with sending/uncertain/unresolved applications. This three-field request cannot report the Host's private uncertainty state: Server does not independently infer it or certify that the Host applied its retention rules correctly. Host must persist/retry the same outbox ID on lost responses; it must not reconstruct deleted Slack history.

Exact success: `{status:"retired",session_id,request_id,adopted_knowledge_preserved:true}`. Exact busy: `{status:"deferred",retry_safe:true}`. Any active Session execution (including the admission-before-reservation window), local application in progress, unfinished durable run or SQLite writer contention defers without canceling jobs, starting models or releasing someone else's lock. Restart-uncertain active runs stay protected until separately reconciled; there is no timeout-based forced cleanup.

One SQLite transaction removes that Session's unadopted proposals, chat results and unadopted review results, including stored evaluation text. It preserves every accepted restricted knowledge/Rule record, current Slack Rule state, approval/nonce/application receipts, adopted proposals and the complete two-pass review audit bound to each accepted record. Other Sessions are unchanged. Required adoption-audit inconsistency rejects with `retirement_integrity` (409); partial failures roll back payload deletion and tombstones together. Applied status receipts remain readable with their original Session/request hash after retirement.

Retirement leaves opaque Session and purged request/proposal ID tombstones plus the exact metadata-only retirement response. The same retirement ID/Session returns that response; reusing that retirement ID for another Session rejects `retirement_id_conflict` (409). A new retirement ID for an already retired Session is an idempotent metadata-only acknowledgement for the new ID. No source content enters access/audit logs or Git.

Late chat/review/proposal/apply attempts against a retired Session reject `session_retired` (409), subject to the existing authentication/enablement checks. Purged request/proposal IDs cannot be rebound to a new Session (`request_retired` / `proposal_retired`, 409). A new incarnation of an expired Slack thread therefore needs a new Session ID and fresh request/proposal IDs. Tombstones are not age-expired or exported. Ordinary model status does not expose them.

Deletion enables SQLite `secure_delete` for the operation. This is logical record retirement with deleted database cells zeroed, not a claim of forensic erasure of old WAL frames, external backups, process memory or the entire filesystem. No VACUUM, forced checkpoint, arbitrary file deletion, model call or service operation occurs. The public fixture tests zeroed deleted content in normal rollback-journal mode; WAL/backups and paired production retirement remain unqualified. Never roll back to a Slack-enabled Server version that ignores tombstones; disable Slack or keep the compatible guard, and never restore an older database to revive retired Sessions.

See `tests/fixtures/slack-retire.json` for exact public input/output examples.

## Chat menu only

`GET /api/slack-approval-entry` returns `{enabled:false}` or `{enabled:true,url}` from the separate `slack-web.json`. The URL must be HTTPS, the exact private-site Client host, path `/slack/approvals`, with no credentials, query, fragment, escape or redirect. The menu uses a direct no-referrer/noopener link; Server never proxies approvals, cookies, tokens or administrator actions. Existing LAN-shared Chat history is unchanged.

Install the Client's HTTPS trust chain using the approved device process, verify its certificate/name, then bookmark the direct HTTPS approval URL on each phone. The current Client page uses administrator password login rather than a browser client certificate; internal Host mTLS is unchanged. Never bypass a certificate error. The legacy HTTP Chat page is a convenience menu, not the approval trust anchor; use the verified HTTPS bookmark for approval.

## Validation and deployment boundary

Run `python -B tests/run_public_tests.py`. This generates a temporary ignored **public fixture** site configuration, refuses an existing real configuration, restricts parent test connections to loopback listeners opened by tests, and removes its own generated configuration. Normal tests use temporary Brain roots and one temporary public HTTP child server. Production settings/keys/models are not copied. TLS Host identity is tested at the HTTP handler with a certificate adapter. The approved installation additionally passed actual Host mTLS/capability and HTTPS password-gate checks, the Chat entry check, bounded public Slack/startup probes and one saved-result text attachment return, as detailed in `SLACK-VALIDATION.md` and the paired Client validation. Broad production throughput, physical mobile approval, real private-proposal adoption, deployed retention/recovery, physical purge across WAL/backups and general end-to-end retry/delivery remain unqualified. The additional retirement-only public fixture suite is `python -B tests/run_public_tests.py test_slack_retire.py`; it does not rerun the prior 109 cases.

Limited independent read-only reviews of the SSE/startup deltas and fixed approval-entry/application rollout reported no findings. They are not a general security or model-quality guarantee. The implemented scope is ready for review; remaining qualifications are listed in `SLACK-VALIDATION.md`. Each installation still requires its own exact authorization, known source/config preimages, maintenance quiescence, scoped normal restarts and reviewed rollback. Keep public examples disabled; only the local operator selects activation in private configuration. Review or merge never performs deployment, enrollment, model requests or knowledge application.
