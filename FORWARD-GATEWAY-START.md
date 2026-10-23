# Forward start after the verified registration hold

This is a separately reviewed, start-only operation. Do not rerun the original Apply, reuse its UAC approval/GO, restore old code or DBs, repeat the migration/adoption, or launch a model.

## Proven current state

The first native Apply stopped the owned Gateway once, adopted the operational baseline once, committed and verified restricted retry_links, and released both locks. Registration succeeded, but its post-registration string-hash check held before any start. TaskUpdated=false in that receipt does not mean registration was absent.

The exact cause is proven against the saved planned hash: removing only the added RegistrationInfo/SecurityDescriptor node from current Definition.XmlText reproduces the entire pre-registration definition digest. Current task arguments, SYSTEM principal, Python image and external security descriptor match the intended values. The task is Ready with no instances. This is stronger than inferring benign formatting from an unchanged action.

Scheduler returns different registered-XML and in-memory definition serializations, including omission/expansion and ordering of default fields. Future Apply explicitly includes the existing SDDL in the planned definition, normalizes the captured registered XML through Scheduler's in-memory schema, requires the complete canonical definition and actual security descriptor to match, and only then pins that registered raw XML. Unknown setting/security differences still refuse.

## Fixed start-only scope

Resume-GatewayRelease.ps1 is restricted to the old b661b353 function library and 4f996de1 producer-barrier library. It verifies their exact byte buffers and imports function definitions only, never an old program body. It has no calls to stop, register/update tasks, install code, adopt metrics, migrate DBs or acquire the knowledge byte lock.

A new recovery intent binds the prior full HOLD receipt, original intent, installed original source commit/manifest/bootstrap/pointer, all installed object ACLs, current raw task XML, full Definition.XmlText, external SDDL, arguments, saved pre-registration hash and all three existing committed artifacts. The current definition's one descriptor-node insertion must still reproduce that saved hash, and its descriptor must equal the unchanged external SDDL. No general XML tolerance is used for this recovery.

The original receipt and migration/adoption files stay immutable. A new receipt is created using CreateNew; unknown existing evidence cannot be overwritten. Only after initial creation succeeds does the worker own later updates. Existing artifacts must retain their fixed hashes, and the original full-DB classifier uses the single already-adopted effective anchor and restricted migration receipt. It never captures another baseline.

The operation requires a new exact human-approved UAC and a new ClientMaintenanceConfirmedResumeGO with a 15-minute TTL and explicit integer active0/queued0 and no-new-requests promise. Inspect is read-only and requires neither a GO nor administrator operation. Native preflight failures are recorded to the new owned receipt.

After exact Ready0instance/listener checks and protected-source/configuration/base-process/installed-code/ACL/full-DB checks, the worker owns the existing Librarian presence barrier, invokes the already-qualified StartExact once, records Scheduler GUID before PID/birth observation, and qualifies image/command/Scheduler parent/release/status/database results. It keeps the barrier through final checks, releases it in finally, and saves success only after release. Busy, drift, unknown identity, failed Run or failed health holds without retry or automatic restore.

The installed Gateway source stays at the already-installed original candidate. A new repository commit containing maintenance helpers is not a second application deployment. Client-side authenticated capabilities, Client Slack startup, public live model tests and main merge belong to the parent task. Production local-main synchronization remains deferred until successful recovery and an exact known-diff audit.

## Qualification boundary

Public fixtures use Scheduler NewTask in memory only, without any registered task operation, to reproduce the descriptor insertion and test complete-definition validation. They reject unrelated settings/security changes, forged GO fields with both Int32 and Int64 count paths, modified existing artifacts and unknown receipt replacement. Start orchestration stubs cover producer busy, DB drift before admission, single Run failure cleanup and successful final checks. They use no production XML, SID, data, TLS credentials, model or network request.

Actual SYSTEM start and runtime integration remain a separate native qualification. A future failure requires preserving its evidence and a new forward plan, not re-running either operation.
