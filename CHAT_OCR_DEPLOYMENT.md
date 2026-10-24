# Chat OCR forward activation

The candidate is not a production-readiness receipt. A separate, exact Apply
intent and fresh human maintenance approval are required before production work.
The default Apply mode describes the operation. Validate verifies staged public
artifacts and runtime hashes; it opens no production database and makes no request.
Apply does not elevate itself.

## Stable history and configuration

Code and Web assets are loaded from one immutable, manifest-verified release.
Both SYSTEM tasks use the protected `chat_ocr_entry.py`, a fixed spec hash, and an
explicit role. Chat receives the same explicit data root through its environment
and `--root`. Site defaults, TLS and existing Chat/Gateway/Slack configuration
come from that data root. Private configuration is never copied into the release.

The existing Chat database retains its path and file identity. Gateway jobs and
Slack restricted storage are also existing-only. Every connection checks identity
and uses SQLite `mode=ro` while held, `mode=rw` after activation, or always `ro`
for read-only telemetry and OCR conversation checks. The deployment path never
uses `rwc`, initializes existing tables, changes their journal mode, repairs their
schema, adopts a new baseline, restores an old DB, or creates another history.

Startup validates column types, nullability, defaults, primary keys, unique-index
properties and required indexes against reference DDL executed only in memory.
Missing/incompatible schema or an unexpected trigger causes a hold. Before/after
checks compare existing DB identities, schema-catalog hashes, main-file bytes and
any nonempty WAL. SQLite may allocate SHM or an empty WAL during a read-only
connection; those are not described as history changes. Live WAL is never hidden
using SQLite `immutable=1`.

Only the new OCR feature configuration, mailbox database and its generated spool
are new data. Existing authentication, model settings, Slack policy, private keys,
source checkout and knowledge storage are outside the mutation targets. Base
process/listener identities and protected-file hashes/ACLs must remain unchanged.

## Single forward attempt

The future Apply performs this sequence, without automatic script retry:

1. Verify the exact approved intent, all source/helper/runtime bytes, protected
   files, complete task definitions and SDDL, old PID/birth/command bindings,
   target absence and a fresh maintenance GO bound to both Server and Client.
   All Chat/Slack/Continue users must have confirmed idle and no new requests.
2. Acquire the existing Librarian barrier protocol and take fresh authenticated
   idle snapshots. Snapshots are counts only; the old services do not have a
   global admission fence. Unknown or busy state prevents progress.
3. Temporarily disable automatic starts of the fixed pair, then normally stop
   Chat and Gateway once each. No force kill or stale-lock deletion occurs.
   Disabled tasks must have no instance/process/listener before continuing.
4. Verify the existing storage, install new Admin/SYSTEM-protected release/spec/
   entry directories, and update only the task actions while both are disabled.
   Task XML and SDDL must match the complete predicted Scheduler-normalized
   definitions. Registration ignores registration triggers and preserves ACEs.
5. Create the new enabled OCR configuration, restore Enabled for the verified new
   entries, and request Gateway then Chat startup once each. Only boot triggers
   or no triggers are accepted. Original Scheduler retry settings are retained;
   the script does not issue a second Stop/Run. Any later Scheduler start must
   pass the same immutable entry/spec/database guards.
6. Both new services initially refuse ordinary requests. Existing authentication
   remains first. Only metadata health and the pinned mTLS readiness check are
   available. Chat worker, OCR maintenance and idle control wait for activation;
   startup never automatically starts base/model processes. First activation
   passively reconciles an already-running model for normal Slack admission.
7. Recheck held process/endpoint bindings, existing storage and protected state.
   Publish the durable, protected, release/spec/manifest/root/DB-bound activation
   GO atomically as the final production commit point. Future legitimate starts
   can reuse that approval without binding it to an obsolete PID or DB contents.

Receipts are written only in a new protected attempt directory, never through a
user-writable packet temporary file. Python helpers execute the exact hash-checked
bytes. No model generation, OCR job dispatch, private image, external AI request,
database migration or old-version restore forms part of Apply.

Before activation, failure retains evidence and requires a new forward repair.
It does not restore old code/DBs or silently retry. A failure during the old-task
transition may leave an old service running: the receipt explicitly says its
admission state is unconfirmed. It claims both new services are held only after
both new bindings were observed. An atomic GO already committed is reported as
committed even if a subsequent receipt write fails.

## Readiness and verification limits

`GET /v1/chat-ocr/readiness` uses the existing mTLS listener, actual socket peer,
existing role certificate pin, and the existing Host owner header
`X-LocalBrain-Approval-Owner` for the worker. It returns NOT_READY before activation.
After the protected activation checks and authentication of the current socket,
it returns `CHAT_OCR_SERVER_READY`, the exact source, existing-history-preserved
flag, authenticated-endpoint flag, UTC verification time and a scope description.
Apply checks the real loopback mTLS health/readiness path. A ClientPC receipt must
come from a real ClientPC request; it is not manufactured from a local fixture.
This readiness metadata does not claim a real OCR image or Qwen generation test.

Public tests cover held admission, shared-history preservation, wrong/missing DB,
WAL visibility, schema/unique constraints, malformed or stale GO, manifest/entry
tampering, fresh-interpreter artifact verification and gate reconstruction.
The Scheduler fixture uses real in-memory definition canonicalization and mocked
registered-task/folder methods. It obtains/registers no real task. Actual SYSTEM
registration, service restart, cross-machine mTLS and private OCR remain future
production validation; no preparation report should claim they already passed.
