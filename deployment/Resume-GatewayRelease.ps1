#Requires -Version 7.2
param([ValidateSet('Describe','Inspect','Resume')][string]$Mode='Describe',
 [string]$RecoveryPacket='',[string]$ApprovedRecoveryIntentSha256='',[string]$ParentGoSha256='')
$ErrorActionPreference='Stop'
if($Mode -eq 'Describe'){@{mode='Describe';stop_limit=0;registration_limit=0;start_limit=1;database_migrations=0;baseline_adoptions=0;automatic_restore=$false;model_requests=0}|ConvertTo-Json;exit 0}
function ResolveResumePath([string]$path,[string]$base){
 $full=[IO.Path]::GetFullPath($path);$base=[IO.Path]::GetFullPath($base).TrimEnd('\')
 if(-not $full.StartsWith($base+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'RESUME_PATH_OUTSIDE_ROOT'}
 for($part=$full;$part;$part=[IO.Path]::GetDirectoryName($part)){
  if((Test-Path -LiteralPath $part) -and ((Get-Item -LiteralPath $part -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)){throw 'RESUME_REPARSE_REFUSED'}
 }
 return $full
}
function ReadResumeJson([string]$path,[string]$sha){
 $bytes=[IO.File]::ReadAllBytes($path)
 if($sha -notmatch '^[a-f0-9]{64}$' -or [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($bytes)).ToLowerInvariant() -cne $sha){throw 'RESUME_FIXED_JSON_BYTES_DRIFT'}
 return ([Text.Encoding]::UTF8.GetString($bytes)|ConvertFrom-Json -AsHashtable)
}
function ImportResumeDefinitions([byte[]]$bytes){
 $t=$null;$e=$null;$a=[Management.Automation.Language.Parser]::ParseInput([Text.Encoding]::UTF8.GetString($bytes),[ref]$t,[ref]$e)
 if($e.Count){throw 'TRUSTED_FUNCTION_PARSE_ERROR'}
 # Return definitions to the caller's script scope; never evaluate any program body.
 return @($a.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$false)|ForEach-Object {$_.Extent.Text})
}
function AssertResumeMaintenance {
 if((FileHash $recoveryIntentPath) -cne $ApprovedRecoveryIntentSha256 -or (FileHash $intentPath) -cne $intentHash){throw 'RESUME_INTENT_DRIFT'}
 if($Mode -eq 'Inspect'){return}
 $g=ReadResumeJson (ResolveResumePath (Join-Path $resumePacket 'apply-go.json') $resumePacket) $ParentGoSha256
 if($g.phase -cne 'ClientMaintenanceConfirmedResumeGO' -or $g.intent_sha256 -cne $ApprovedRecoveryIntentSha256 -or
  $g.script_sha256 -cne $resumeIntent.script_sha256 -or $g.no_new_requests_until_finished -cne $true -or
  ($g.client_active_requests -isnot [long] -and $g.client_active_requests -isnot [int]) -or
  ($g.client_queued_requests -isnot [long] -and $g.client_queued_requests -isnot [int]) -or
  $g.client_active_requests -ne 0 -or $g.client_queued_requests -ne 0 -or $g.parent_message_reference -notmatch '\S'){throw 'RESUME_PARENT_GO_INVALID'}
 $date=if($g.created_utc -is [DateTime]){$g.created_utc.ToUniversalTime()}else{[DateTimeOffset]::Parse([string]$g.created_utc).UtcDateTime}
 if($date -gt [DateTime]::UtcNow -or ([DateTime]::UtcNow-$date).TotalMinutes -gt 15){throw 'RESUME_PARENT_GO_EXPIRED'}
}
function AssertResumePrior($prior){
 if($prior.Phase -cne 'HOLD_PRESERVE_CURRENT_STATE_NO_AUTOMATIC_RESTORE' -or
  $prior.FailureCode -cne 'TASK_DEFINITION_DRIFT' -or $prior.FailedPhase -cne 'task-action-update-requested' -or
  $prior.IntentSha256 -cne $intentHash -or $prior.ScriptSha256 -cne $resumeIntent.original_script_sha256 -or
  @($prior.StoppedTasks).Count -ne 1 -or $prior.StoppedTasks[0].role -cne 'gateway' -or
  $prior.StoppedTasks[0].operation_mode -cne 'Apply' -or @($prior.OwnedStarts).Count -ne 0 -or
  $prior.KnowledgeFence.released -cne $true -or $prior.LibrarianBarrier.released -cne $true -or
  $prior.Migration.status -cne 'ADDITIVE_MIGRATION_VERIFIED' -or $prior.Migration.existing_rows_unchanged -cne $true -or
  $prior.TaskUpdate.new_arguments_sha256 -cne $resumeIntent.arguments_sha256 -or
  $prior.TaskUpdate.new_definition_sha256 -cne $resumeIntent.before_registration_definition_sha256 -or
  $prior.TaskUpdate.security_sha256 -cne $resumeIntent.security_sha256){throw 'PRIOR_HOLD_NOT_EXACTLY_QUALIFIED'}
 foreach($name in @('EffectiveBaselineArtifact','MigrationArtifact','FencedAdoptionArtifact')){
  $b=$prior[$name];$fixed=$resumeIntent.existing_artifacts[$name]
  $p=SafePath $b.path $packet
  if($b.path -cne $fixed.path -or $b.sha256 -cne $fixed.sha256 -or (FileHash $p) -cne $fixed.sha256){throw 'EXISTING_COMMITTED_ARTIFACT_DRIFT'}
 }
}
function AssertResumeTaskProof {
 $task=GetTask 'gateway'
 if((StringHash $task.Definition.XmlText) -cne $resumeIntent.definition_xmltext_sha256 -or
  (StringHash $task.GetSecurityDescriptor(7)) -cne $resumeIntent.security_sha256){throw 'RESUME_TASK_DEFINITION_OR_SECURITY_DRIFT'}
 $xml=$task.Definition.XmlText
 $settings=[Xml.XmlReaderSettings]::new();$settings.DtdProcessing=[Xml.DtdProcessing]::Prohibit
 $reader=[Xml.XmlReader]::Create([IO.StringReader]::new($xml),$settings);$doc=[Xml.XmlDocument]::new();$doc.XmlResolver=$null
 try{$doc.Load($reader)}finally{$reader.Dispose()}
 $ns=[Xml.XmlNamespaceManager]::new($doc.NameTable);$ns.AddNamespace('t','http://schemas.microsoft.com/windows/2004/02/mit/task')
 $nodes=@($doc.SelectNodes('/t:Task/t:RegistrationInfo/t:SecurityDescriptor',$ns))
 if($nodes.Count -ne 1 -or (StringHash $nodes[0].InnerText) -cne $resumeIntent.security_sha256){throw 'RESUME_DESCRIPTOR_NOT_EXISTING_SECURITY'}
 $pattern='(?m)^    <SecurityDescriptor>[^<]*</SecurityDescriptor>\r?\n'
 if([regex]::Matches($xml,$pattern).Count -ne 1 -or
  (StringHash ([regex]::Replace($xml,$pattern,''))) -cne $resumeIntent.before_registration_definition_sha256){throw 'RESUME_FULL_PRIOR_DEFINITION_PROOF_FAILED'}
 return $task
}
function AssertResumeInstalled {
 $pointerPath=SafePath (Join-Path $intent.program_root 'gateway-current.json') $intent.program_root
 $pointer=ReadResumeJson $pointerPath $resumeIntent.pointer_sha256
 $release=SafePath (Join-Path $intent.program_root ('releases\'+$intent.source_commit)) $intent.program_root
 if($pointer.source_commit -cne $intent.source_commit -or $pointer.release_root -cne $release -or
  $pointer.data_root -cne $root -or $pointer.manifest_sha256 -cne $intent.manifest_sha256){throw 'INSTALLED_POINTER_DRIFT'}
 $m=GetFixedManifest
 if((FileHash (Join-Path $release 'release-manifest.json')) -cne $intent.manifest_sha256){throw 'INSTALLED_MANIFEST_DRIFT'}
 foreach($b in $m.files){if((FileHash (SafePath (Join-Path $release $b.path) $release)) -cne $b.sha256){throw 'INSTALLED_CODE_BYTES_DRIFT'}}
 $entry=@($m.files|Where-Object {$_.path -ceq 'deployment/gateway_entry.py'})
 if($entry.Count -ne 1 -or
  (FileHash (Join-Path $intent.program_root 'gateway_entry.py')) -cne $entry[0].sha256 -or
  (FileHash (Join-Path $packet 'payload\deployment\gateway_entry.py')) -cne $entry[0].sha256){throw 'INSTALLED_BOOTSTRAP_DRIFT'}
 $actual=@(Get-ChildItem -LiteralPath $intent.program_root -Recurse -Force|ForEach-Object {$_.FullName})+@($intent.program_root)
 $bound=@($resumeIntent.installed_acl_bindings|ForEach-Object {$_.path})
 if(@(Compare-Object ($actual|Sort-Object) ($bound|Sort-Object)).Count){throw 'INSTALLED_OBJECT_SET_DRIFT'}
 foreach($b in $resumeIntent.installed_acl_bindings){
  $null=SafePath $b.path 'C:\ProgramData'
  if((StringHash (Get-Acl -LiteralPath $b.path).Sddl) -cne $b.acl_sha256){throw 'INSTALLED_ACL_DRIFT'}
 }
}
function SaveInitialResumeReceipt($record){
 $record.UpdatedUtc=[DateTime]::UtcNow.ToString('o')
 $path=SafePath $receiptPath $packet
 $bytes=[Text.UTF8Encoding]::new($false).GetBytes(($record|ConvertTo-Json -Depth 20)+[char]10)
 try{$f=[IO.FileStream]::new($path,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)}
 catch [IO.IOException]{throw 'RESUME_INITIAL_RECEIPT_CREATE_NEW_REFUSED'}
 try{$f.Write($bytes);$f.Flush($true)}finally{$f.Dispose()}
}
function AssertResumePreflight($record){
 AssertMaintenance;AssertAssets;AssertProtected;AssertKnownFiles;AssertBase $record
 $null=AssertResumeTaskProof
 AssertReadyForStart $record 'gateway'
 AssertResumeInstalled
 AssertDbState $record
}
function InvokeResumeStart($record){
 AssertMaintenance;AssertAssets;AssertProtected;AssertKnownFiles;AssertBase $record
 $null=AssertResumeTaskProof
 AssertReadyForStart $record 'gateway'
 AssertResumeInstalled
 AssertDbState $record
 AcquireLibrarianBarrier $record
 try{
  AssertLibrarianBarrier $record
  $null=AssertResumeTaskProof
  # Only the existing owned-stop successor may be started. No stop/update/install/migration/adoption.
  StartExact $record 'gateway'
  AssertMaintenance;AssertAssets;AssertProtected;AssertKnownFiles;AssertBase $record;AssertIdle $record
  $null=AssertResumeTaskProof
  AssertResumeInstalled
  $record.FinalGateway=AssertOwned $record 'gateway'
  $record.AdminAfterImage=AssertAdminImage $record.FinalGateway
  AssertLibrarianBarrier $record
 }finally{ReleaseLibrarianBarrier $record}
 if(-not $record.LibrarianBarrier.released){throw 'RESUME_BARRIER_RELEASE_UNCONFIRMED'}
}
$resumePacket=[IO.Path]::GetFullPath($RecoveryPacket)
$recoveryIntentPath=ResolveResumePath (Join-Path $resumePacket 'intent.json') $resumePacket
$resumeIntent=ReadResumeJson $recoveryIntentPath $ApprovedRecoveryIntentSha256
if($resumeIntent.schema -cne 'localbrain.gateway-start-only-intent.v1' -or $resumeIntent.packet -cne $resumePacket -or
 $resumeIntent.original_script_sha256 -cne 'b661b35329d20470ad00b55cb29527a1042d03b358e1322942f2f24d9fad27b9' -or
 $resumeIntent.fence_module_sha256 -cne '4f996de1618c2d06f43b326a0b1f5f53117cbec4a67b56bbbeb542acc1157ac9' -or
 $resumeIntent.automatic_restore -cne $false -or $resumeIntent.stop_limit -ne 0 -or $resumeIntent.start_limit -ne 1 -or
 $resumeIntent.registration_limit -ne 0 -or $resumeIntent.database_migrations -ne 0 -or $resumeIntent.baseline_adoptions -ne 0){throw 'START_ONLY_FIXED_SCOPE_DRIFT'}
$null=ReadResumeJson $recoveryIntentPath $ApprovedRecoveryIntentSha256
if([Convert]::ToHexString([Security.Cryptography.SHA256]::HashData([IO.File]::ReadAllBytes($PSCommandPath))).ToLowerInvariant() -cne $resumeIntent.script_sha256){throw 'RESUME_SCRIPT_BYTES_DRIFT'}
$packet=[IO.Path]::GetFullPath($resumeIntent.original_packet)
$null=ResolveResumePath $resumePacket $packet
$originalScript=ResolveResumePath (Join-Path $packet 'Apply-GatewayRelease.ps1') $packet
$bytes=[IO.File]::ReadAllBytes($originalScript)
if([Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($bytes)).ToLowerInvariant() -cne $resumeIntent.original_script_sha256){throw 'ORIGINAL_FUNCTION_LIBRARY_BYTES_DRIFT'}
foreach($definition in (ImportResumeDefinitions $bytes)){. ([scriptblock]::Create($definition))}
$intentPath=SafePath (Join-Path $packet 'intent.json') $packet
$intentHash=$resumeIntent.original_intent_sha256
$intent=ReadResumeJson $intentPath $intentHash
$root=$intent.data_root
if($intent.source_commit -cne $resumeIntent.installed_source_commit -or
 $intent.program_root -cne 'C:\ProgramData\LocalBrainServer' -or $intent.database_policy -cne 'index-search-counters-v1'){throw 'ORIGINAL_RELEASE_SCOPE_DRIFT'}
$priorPath=SafePath (Join-Path $packet 'execution.json') $packet
$prior=ReadResumeJson $priorPath $resumeIntent.original_execution_sha256
AssertResumePrior $prior
$fenceBytes=[IO.File]::ReadAllBytes((SafePath (Join-Path $packet 'payload\deployment\release_fence.ps1') $packet))
if([Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($fenceBytes)).ToLowerInvariant() -cne $resumeIntent.fence_module_sha256){throw 'ORIGINAL_FENCE_LIBRARY_BYTES_DRIFT'}
foreach($definition in (ImportResumeDefinitions $fenceBytes)){. ([scriptblock]::Create($definition))}
# Override imported Apply-mode maintenance gate for this separately approved start-only operation.
function AssertMaintenance {AssertResumeMaintenance}
$script:taskXmlHash=$resumeIntent.registered_xml_sha256
$script:taskArgsHash=$resumeIntent.arguments_sha256
$receiptPath=SafePath (Join-Path $resumePacket 'execution.json') $packet
$record=@{Schema='localbrain.gateway-start-only-execution.v1';Phase='resume-preflight';RecoveryIntentSha256=$ApprovedRecoveryIntentSha256;
 ScriptSha256=$resumeIntent.script_sha256;OriginalIntentSha256=$intentHash;OriginalReceiptSha256=$resumeIntent.original_execution_sha256;
 BeforeTasks=$prior.BeforeTasks;StoppedTasks=$prior.StoppedTasks;OwnedStarts=@();BaseListeners=$prior.BaseListeners;
 DatabaseBefore=$prior.DatabaseBefore;DatabaseAfterMigration=$prior.DatabaseAfterMigration;DatabaseChecks=@();
 EffectiveBaselineArtifact=$prior.EffectiveBaselineArtifact;MigrationArtifact=$prior.MigrationArtifact;TaskUpdated=$true;
 TaskUpdateVerifiedBy='exact_current_definition_and_hash_proven_descriptor_insertion';LibrarianBarrier=$null;
 ExplicitModelRequests=0;DatabaseMigrations=0;BaselineAdoptions=0;RegistrationOperations=0;StopOperations=0;AutomaticRestore=$false;ParentGoSha256=$ParentGoSha256}
if($Mode -eq 'Inspect'){
 AssertResumePreflight $record
 @{status='INSPECTED_START_ONLY_READONLY';task_state=3;instances=0;source_commit=$intent.source_commit;database_comparison=$record.LastDatabaseComparison;production_operations=0}|ConvertTo-Json -Depth 6;exit 0
}
AssertMaintenance
$who=[Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if(-not $who.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){throw 'NATIVE_WINDOWS_UAC_REQUIRED'}
$mutex=[Threading.Mutex]::new($false,'Global\LocalBrain-GatewayRelease-20261023')
if(-not $mutex.WaitOne(0)){$mutex.Dispose();throw 'ANOTHER_RELEASE_WORKER_ACTIVE'}
$ownsReceipt=$false
try{
 if((Test-Path -LiteralPath $receiptPath) -or (Test-Path -LiteralPath ($receiptPath+'.pending'))){throw 'EXISTING_RESUME_EXECUTION_NO_RETRY'}
 SaveInitialResumeReceipt $record
 $ownsReceipt=$true
 AssertResumePreflight $record
 InvokeResumeStart $record
 $record.Phase='SERVER_RESUMED_AWAITING_CLIENT_HOST_QUALIFICATION';SaveReceipt $record
 @{status=$record.Phase;receipt_sha256=FileHash $receiptPath;source_commit=$intent.source_commit;native_start_limit=1;model_requests=0}|ConvertTo-Json
}catch{
 $record.FailureCode=PublicFailureCode $_;$record.FailedPhase=$record.Phase;$record.Phase='HOLD_RESUME_CURRENT_STATE_NO_RETRY_NO_RESTORE'
 if($ownsReceipt){try{SaveReceipt $record}catch{}}
 throw 'START_ONLY_RESUME_HELD_INSPECT_RECEIPT_NO_RETRY'
}finally{$mutex.ReleaseMutex();$mutex.Dispose()}
