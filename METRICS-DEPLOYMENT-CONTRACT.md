# Strict search-counter classification during Gateway release

This repairs deployment's rejection of every normal index statistics update. It does not change Brain, Gateway, models, authentication, business schemas or request admission. It does not deploy or reuse any previous Apply/intent/GO approval.

## Current source and exact SQLite contract

The table belongs to runtime/index.sqlite3. Brain.__init__ declares:

    CREATE TABLE metrics (key TEXT PRIMARY KEY, value INTEGER NOT NULL DEFAULT 0)

The only schema objects for this table are that table and sqlite_autoindex_metrics_1. The classifier checks exact normalized DDL, table_xinfo (including hidden columns/defaults), the one nonpartial implicit unique PK index and its BINARY key/rowid metadata, no FK, no triggers and no other index. SQLite does not enforce a non-NULL TEXT PK or integer-only storage in this schema; the classifier separately requires known TEXT keys, unique keys and nonnegative SQLite INTEGER values through 2^63-1.

The sole tracked SQL value writer is Brain.metric in src/localbrain.py. It performs an UPSERT adding int(amount). That general method is not itself a restricted API: future/direct callers or SQL writers could supply something else. This release predicate rejects unrecognized keys, invalid storage types and unexpected content rather than treating the table name as trustworthy.

Tracked producer inventory (verified by AST against all src/*.py):

| Keys | Tracked producer and meaning |
| --- | --- |
| search_requests, search_hits | Brain.search: after a valid search completes, add 1 request, then int(bool(final_results)) hits |
| duplicate_sources_avoided | KnowledgePipeline.enqueue sees the source hash already queued |
| fact_promotions_blocked | promote_fact rejects secret-bearing source |
| contradictions_detected | promote_fact finds conflicting active claim |
| fact_promotions | promote_fact completes the promotion path |
| drafts_blocked_for_secret | create_draft rejects secret-bearing candidate |
| fact_drafts, conflict_drafts, fact-review_drafts, synthesis_drafts, decision_drafts | create_draft increments kind + "_drafts" after writing/committing the corresponding draft; these finite kinds come from the tracked call sites |
| synthesis_rejections | process_candidates lacks two eligible current sources |
| decision_rejections | process_candidates lacks verbatim source decision evidence |
| librarian_failures | _run_queue_locked catches an item failure |

There are 15 known keys. A key need not exist yet. A comparison requires the exact same key set on both sides; newly inserted known keys also HOLD. All 13 non-search counters must remain identical.

Brain.context and find_entity/find_concept/find_synthesis call search and inherit its pair of updates. The Brain HTTP /search, /context and find routes dispatch to those functions after role authentication. In public fixtures, /health (Brain.status), Gateway /health and /v1/control/status, and control heartbeat do not update metrics. Gateway heartbeat changes an in-memory timestamp. Brain status reads metrics/vector counts and the file inventory. Search has the existing knowledge transaction guard, including interrupted-apply recovery if one exists; no claim is made that an arbitrary search can never trigger other business recovery. Any observed nonmetrics DB content change is still rejected.

## Full evidence and narrow predicate

Every snapshot retains all five DBs, all SQLite tables (including sqlite_sequence and shadow tables), all typed-row digests, schema digest, user_version and integrity. One read-only transaction is used per DB, not an atomic cross-DB checkpoint.

For index only, the same fetched metrics row buffer inside the same transaction also produces a validated key/integer profile. The profile is cryptographically checked against the complete typed table digest. Unknown key names/values or SQL are not disclosed in an invalid profile; the full typed table digest is still saved.

Comparison accepts exactly:

- every non-index DB record remains exact, except the explicitly verified restricted migration result;
- index schema/version/table set and every nonmetrics table's row count and full typed digest remain exact;
- metrics has the same known keys, valid INTEGER values and all 13 non-search values unchanged;
- both states satisfy hits <= requests;
- 0 <= hits_delta <= requests_delta <= 32.

The 32 bound is a fixed per-operation classification limit, not a rate limit, model setting or configurable relaxation. All checks stay anchored to the original baseline, including checks after migration. Passing a check never refreshes the baseline. Further valid requests beyond the cumulative bound HOLD.

This proves that contents are consistent with the bounded search pair. It does not authenticate the writer, establish a maintenance/admission fence or prove which request caused the update. The complete before/current snapshots and the explicit delta classification are retained even when hashes differ. Unknown schema/key/type, non-search change, business change, decrement, excess bound or inconsistent pair must HOLD.

The existing writer updates requests and hits in two transactions. A baseline taken between them can later yield hits_delta > requests_delta and HOLD despite legitimate activity. No auto-baseline update hides this ambiguity.

## Legacy evidence without unconditional capture

The previous v1 snapshot contains a complete typed metrics table digest but no individual counter profile. Its opaque hash cannot simply be relabeled as benign.

UpgradeLegacy is a separate explicit read-only proof. It keeps the original v1 bytes untouched, captures a v2 observation and first requires all old nonmetrics rows/schema/version/integrity/counts to match. Using the current complete known profile, it enumerates only the 561 possible backward pairs 0 <= hits_delta <= requests_delta <= 32. Exactly one prior complete typed metrics digest must reproduce the old recorded digest. No match, ambiguous result, unknown profile, non-search change or business drift fails closed, with no expanded search.

The result retains the original aggregate binding, algorithm version, observed complete v2 snapshot, separately derived v2 baseline and exact classification/delta. It reconstructs contents consistent with the old typed digest; it does not claim the original updater was a search request. Preparation accepts only a separately fixed v2 baseline and saves its fresh full observation/comparison. It never invokes UpgradeLegacy or replaces a baseline automatically.

## Explicit adoption of audited operational statistics

If the bounded legacy content proof fails, UpgradeLegacy remains failed and its search range never expands. A separate operator-authorized AdoptLegacyMetrics mode can establish a new operational baseline only after checking the old snapshot's own digests/integrity, exact old nonmetrics DB/table/schema/version/count evidence, and the current metrics table's complete strict schema, known key set and INTEGER values. A nonempty decision reference is required. This is a deliberate new operation, not a retry or implicit fallback of UpgradeLegacy, Check, Prepare or Apply.

The independent adoption receipt keeps the old complete snapshot and new complete observation, marks past counter values and producer identity as unproven, records that all business records match, and names the adopted v2 baseline. It does not assert that old key values or causes were benign or unchanged. No old DB or metrics row is written or deleted. Unknown current key/type/schema or any old business difference rejects adoption.

The old v1/new v2 bytes, typed digests, explicit decision reference and receipt are separately SHA-bound before a new intent. Future checks stay anchored to that new fixed baseline with the same strict search-pair predicate. Passing checks never adopts another baseline. Prior Apply/intent/GO approval cannot authorize the new intent.


## Apply integration and alternatives

For the new maintenance release, Prepare and pre-fence Apply perform only the full-evidence CheckBusiness preview. The existing bounded search classifier is unchanged. A separate explicit AdoptFencedMetrics may run once after native UAC under both physically owned producer protocols; RELEASE-MAINTENANCE-FENCE.md specifies its narrow three-counter predicate, business exactness, ordering and limitations.

The effective baseline is saved from those validated same-buffer bytes to a new CreateNew artifact with a separately checked SHA. Post-adoption Check never refreshes it, including after migration. The restricted expected record can be replaced only by the SHA-bound validated migration.after; index counters always remain anchored to that single effective baseline. Every comparison result is retained before refusal; HOLD saves the phase and a fixed public failure code while suppressing arbitrary/private exception text.

A formal universal admission fence would require coordination for every possible writer. This operation uses only the two existing tracked producer protocols for a short critical section. It does not hold the knowledge byte lock over installation, native UAC waiting, Scheduler operations or network health probes. The cooperative budget and existing search timeout implications are explicitly documented.

No historical metrics are deleted; no table is excluded; equal counts are never treated as content equality. A later schema/policy extension requires new source, fixtures, hashes and approval.

## Qualification boundary

Public fixtures execute actual Brain search/context/find functions and an isolated authenticated Brain HTTP server, actual Gateway status/heartbeat handler code with fixture authentication/network/resource stubs, all-table comparisons, adversarial schema/key/type/business changes, bounded legacy proof and Apply state guards. They use no production config, model, Slack post or original request.

Native ACL/task execution and any production apply remain separate. The old b90 script, intent3452, execution receipt and expired GO remain immutable. A new commit, release manifest, intent and review are required before any new native UAC; parent owns GitHub main merge.

## Separate short maintenance operation

RELEASE-MAINTENANCE-FENCE.md specifies the separate, explicitly authorized one-time operational adoption under the existing two producer protocols. This supersedes the unfenced Apply/Prepare integration described above for the new release candidate only. The normal two-search-key/32 classifier, legacy failure evidence and immutable old baselines remain unchanged. Pre-fence preview never adopts; post-fence checks use one fixed SHA-bound effective baseline.
