# Fixed Gateway release and deployment operations

This adds a staged release boundary to the integrated Slack recovery candidate. It does not itself deploy, merge main or start a model.

## Code versus stable data

The first managed Gateway release uses:

- code: a commit-bound directory under ProgramData/LocalBrainServer/releases
- verified entry: ProgramData/LocalBrainServer/gateway_entry.py
- active pointer: ProgramData/LocalBrainServer/gateway-current.json
- stable data root: the existing local deployment folder, recorded privately in intent.json

Configuration, TLS credentials, databases, models, llama.cpp dependencies, knowledge and logs remain at the stable data root. They are not copied into code releases or sent to another PC. The release manifest contains Git-tracked product/source/procedure files only. The new ProgramData tree grants Administrators/SYSTEM full control and Users read/execute. Existing data ACLs are not changed.

The Gateway task keeps its existing SYSTEM principal, executable, working directory, schedule/settings and security descriptor. Only its arguments change to Python isolated mode (-I -B), the verified entry and the fixed local pointer. Registration uses UPDATE, not CREATE_OR_UPDATE. A missing/changed task is held.

site_settings separates CODE_ROOT from an optional absolute LOCALBRAIN_DATA_ROOT. Gateway code uses the stable data root for config/state and its frozen CODE_ROOT for Manage-LocalBrain.ps1. Release-owned management uses frozen code for future Brain/Proxy/Gateway child starts, explicit stable Brain --root and the existing stable model/binary paths. No tuning or authentication setting is changed. Reviewer dispatches from future release-owned Brain code use the release wrapper and stable data root.

The existing independently scheduled Chat and Reviewer tasks and running Brain/Proxy/Embedding processes are not retargeted or restarted by this first Gateway switch. Their identities remain protected. They retain their existing startup paths, so main sync can change code used on their next independent launch. This limit is explicit; a later component release switch must record its own tested version and identity. Do not describe the entire installation as already frozen.

## Preparation only

Prepare-GatewayRelease.ps1 defaults to Describe. Explicit Prepare requires a clean exact candidate commit, the prior production file/dependency manifest, classified DB baseline and observed task/listener identities. It verifies them, refuses existing prepared payload/intent or unknown ProgramData content, then stages only into the workspace packet. It does not perform DDL, change tasks, stop processes, write production files or request UAC.

The private intent binds source commit/tree, every staged file, Python executable, protected source/config hashes and ACLs, DB identity/ACL metadata and the full typed DB baseline. Prepared assets must not be silently replaced after a partial failure.

The snapshot helper and every payload file are read from the exact immutable Git commit blobs. Text payloads retain committed LF bytes; checkout CRLF conversion and later working-tree edits cannot change the staged release. Binary blobs are copied without text conversion. Preparation verifies HEAD and cleanliness again after staging.

## Database boundary

release_db.py snapshots all SQLite tables, including sqlite_sequence and any shadow tables, using typed cell encoding and sorted row hashes. It records schema, user_version and integrity inside one read-only transaction per DB. The five DB snapshots are independent; this is not an atomic cross-database checkpoint.

Only the restricted store receives additive DDL:

    CREATE TABLE retry_links(
        previous_id TEXT PRIMARY KEY,
        next_id TEXT UNIQUE NOT NULL,
        root_id TEXT NOT NULL,
        attempt_index INTEGER NOT NULL
    )

The primary/unique constraints create the two implicit indexes. Existing table rows and user_version must remain unchanged. An existing retry table is accepted only for the exact known DDL, columns, two nonpartial implicit unique indexes and absence of triggers/FKs/unknown objects. Unexpected schema, busy/unknown state or unclassified baseline drift holds the operation. Full DB v2 evidence permits only the fixed index-search-counters-v1 predicate described in METRICS-DEPLOYMENT-CONTRACT.md: all business rows stay exact, the complete known metrics profile is bound to its full typed digest, and only the original-baseline cumulative search pair may increase within 32. All other changes HOLD. Passing checks never refresh the baseline. No general DB initialization or history reclassification is part of migration.

A failure before SQLite commit rolls back only that uncommitted transaction. After commit, later failure preserves the additive schema and current state; no backup DB or historical runtime is restored.

## Parent coordination and native Windows boundary

Apply-GatewayRelease.ps1 defaults to Describe; Inspect is read-only. Apply requires the exact intent hash, a fresh hash-bound apply-go.json stating ClientMaintenanceConfirmedApplyGO, zero Client active/pending requests, no new requests until completion and the parent message reference. Its 15-minute operation window starts from that GO record. This gate is workflow coordination under existing human authorization.

No GO record is generated during preparation. Parent sends the actual GO after observing the final hashes. Native Windows UAC must then be requested once through the legitimate consent prompt; this procedure neither clicks nor bypasses it. Normal sandbox escalation is not a substitute for that consent.

The elevated operation first reads the live SYSTEM image/command line and scheduler parent and matches them to the fixed task/GUID/PID/birth binding. It fails before stop if those remain unknown. Config/dependencies/source hashes, file ownership, full DB snapshots with the strict classified search-pair predicate, current idle and protected service identities are rechecked. No separate read-only UAC is needed when the same bounded Apply preflight can establish the image identity.

## Apply sequence and failure handling

1. Acquire the single-operation mutex and refuse any existing/pending execution receipt.
2. Verify GO, fixed assets, old task/image identity, source/config/DB baselines and idle/base services. Save the receipt durably.
3. Install and verify the new code-only ProgramData release and permissions while the old Gateway still runs.
4. Save stop intent; stop only the exact owned Gateway instance. Wait for Ready, zero instances and zero listener. Never kill an unknown process.
5. Recheck all five DBs. Run the single additive restricted migration and verify every previous business row/table and classify the bounded original-baseline search counters in full retained evidence.
6. Update only the known Gateway task action, preserving its security/settings/principal. Save the new task definition binding.
7. Before one Run, verify Brain health, Proxy listener, protected identities and post-migration DB baseline again.
8. Save the Run attempt before calling it, then durably save its returned instance GUID before observing PID/birth/health. Bind a new identity once; never adopt a replacement or retry Run.
9. GET status must report the exact release commit and idle/base health. Check DB/config/source/other service identities again and save SERVER_DEPLOYED_AWAITING_CLIENT_HOST_QUALIFICATION.

Busy, unknown ownership, hash/schema drift, startup/health failure or incomplete receipt produces HOLD and preserves current state. The Gateway may remain stopped. There is no automatic old-runtime/config/DB restoration and no second start. This script sends no inference, Slack post, original request or Host-authentication bypass.

## Git/main synchronization and cleanup

Git checkout, staged payload, installed release and running process are different states. A pull changes checkout; it does not promote the pointer or attest the running release. Deployment receipt and status release_commit identify the running version.

Parent owns GitHub main merge. The Server owner waits for the exact merge SHA before local main sync. Verify that the known four-file production diff still equals its preserved public patch and is included in the integrated candidate; if unknown changes exist, hold. Only then consume that exact known patch with a checked reverse application and perform a normal fast-forward to the parent-supplied merge SHA. Do not reset, force-push or discard unknown changes. Record merge SHA alongside the installed source commit/tree.

After the new configuration and required functions have been qualified, remove only identified unused fixtures/old releases/worktrees/backups. Keep the active release/pointer, stable data/models/auth/dependencies, required tasks, final receipt and reproducible Git source/procedure. Cleanup is not performed during PREPARED and must not delete working code or a still-needed component startup path.

## Qualification limits

Public regression/stub guards validate logic, metadata, state transitions and additive migration on disposable fixtures. Native SYSTEM image access, ACL application/task action update, actual Windows switch, Client Host-authenticated capabilities and limited real model qualification remain separate execution steps. Maintain Qwen context32768/KVq4 and the existing Host-only TLS/restriction/foreground-priority contracts.

For the new fixed release maintenance operation, follow RELEASE-MAINTENANCE-FENCE.md. UAC wait and all Scheduler operations remain outside the knowledge byte lock. Both existing producer protocols and a single explicit full-evidence adoption are required; no implicit baseline refresh is permitted.
