# Short release maintenance fence

This is a separate, explicit maintenance operation for periodic Librarian statistics during native-UAC wait. It does not widen the normal search-counter predicate or automatically replace an earlier baseline. Parent Client maintenance GO and approval of this new fixed script/intent are required. Previous intent/GO/UAC receipts are not reusable.

## Scope and order

Preparation and Inspect never acquire production locks, stop services or adopt a new baseline. Inspect verifies read-only task/assets/identity only. Prepare and Apply preflight retain a complete before/current observation and preview only: the same known metrics key set, valid strict schema/integer profile, monotonic duplicate_sources_avoided and search_requests/search_hits with hits delta <= requests delta. Every other counter and all business rows, table sets, schema, versions and integrity must remain exact.

After regular Windows UAC, Apply verifies fixed assets/configuration, exact SYSTEM process image/command, Scheduler identity/PID/birth, protected listeners and fresh Server idle plus Client maintenance GO. It installs only the manifested code release outside the short knowledge lock. Then:

1. Create runtime/librarian.lock exclusively with the existing run_queue presence protocol, using the maintenance process PID/time and a unique token. Any existing file, including a stale or unreadable one, refuses before stopping Gateway. It is never removed or adopted.
2. With this producer barrier held, stop the exact approved Gateway once and wait for Scheduler Ready. This wait and all Scheduler COM operations are outside the knowledge byte lock. Already dispatched Librarian children encounter the same marker. An already running child owns the existing marker and causes refusal.
3. Acquire byte 0 of the existing one-byte runtime/knowledge-transaction.lock using the same Windows byte-range protocol as msvcrt.LK_NBLCK. Require bound path, length, creation time, ACL and bytes; never create, truncate, replace, delete or call the runtime guard/recovery function. Refuse contention after two seconds, with no repeated deployment retry.
4. Under both owned fences, perform exactly one AdoptFencedMetrics from the original SHA-bound v2 snapshot. Save full original/current/adopted records and comparison to CreateNew fenced-adoption.json, then a separate SHA-bound database-fenced-baseline.json. No prior metrics receipt is relabeled as proven history. Only the audited three counters may differ at this intentional adoption; other twelve counters and all business records stay exact. The helper is read-only; physical ownership is verified by the caller, not by an arbitrary CLI token.
5. Under that same byte lock, add and verify only restricted retry_links, retaining existing rows and user_version. Full comparison uses the fixed adopted baseline plus the restricted-only migration receipt.
6. Always release the knowledge byte lock before updating Scheduler or starting Gateway once. Keep the Librarian marker through task update, startup and final status/database qualification, then release the owned marker in finally. Success is saved only after both releases.
7. Thereafter normal Check stays anchored to the single effective baseline: all thirteen non-search counters exact and 0 <= hits delta <= requests delta <= 32 cumulatively. Checks never refresh it. New duplicate discovery, even if legitimate, holds. Unknown key/type/schema/business changes hold at every stage.

No old source/DB restoration, model startup, real Slack post, original request replay, kill of unknown processes, repeated start or automatic rebaseline is part of this operation.

## Producer coverage and limitations

The tracked producer inventory remains in METRICS-DEPLOYMENT-CONTRACT.md. Search/context/find and enqueue/discover_raw/promote_fact/create_draft/process_candidates use the existing knowledge guard. run_queue as a whole does not: extraction and its librarian_failures metric can occur outside that guard. The exclusive Librarian presence barrier is therefore also required. Acquiring the knowledge byte lock alone is insufficient.

Deployment takes only the OS byte lock; it does not construct Brain or call knowledge_guard.guard, whose acquisition may recover interrupted applies. Existing producers are unchanged. Unknown direct SQL writers and writers outside these protocols are not universally fenced: full exact business checks still reject observed differences. Five read-only DB snapshots use one transaction per DB, not one atomic cross-DB checkpoint.

The byte-lock section has a 20-second cooperative budget, checked at boundaries before/after helpers. This is not a hard interruption guarantee for native disk, process or SQLite calls. No install, Scheduler COM, readiness wait or network probe belongs in that section. Existing search writers may wait up to their existing 30-second guard timeout; parent maintenance must prohibit new work during this short interval. Time overrun holds and finally releases; committed additive migration is preserved for forward investigation.

The producer marker may span the bounded stop/start readiness waits as well as the short byte-lock section. Its ownership is held via the same Windows file object from CreateNew with denied write/delete sharing. Release uses handle-based delete disposition; no post-close pathname deletion can remove a replacement. Failure to confirm ownership or delete disposition preserves evidence and holds. Failed partial creation is cleaned only through that original handle.

## Public qualification

Windows fixture uses the actual knowledge_guard OS protocol on isolated public paths in both directions, two-second busy refusal, producer release after exceptions, actual run_queue locked response without discovery/extraction, denied marker rename/replacement, existing unknown marker preservation and no pathname cleanup. Python fixtures cover one-time receipt contents, strict unknown/business/schema/counter rejection, original baseline preservation and cumulative post-adoption checks. PowerShell fixtures cover pre-stop busy refusal, stop outside the byte lock, single adoption, failure/budget finally release, and Scheduler/start only after byte-lock release with producer barrier still held.

These fixtures do not qualify native SYSTEM task execution, production runtime integration or hard I/O deadlines. A new fixed manifest/intent and fresh parent maintenance GO must be reviewed before legitimate native UAC. Parent owns main merge.
