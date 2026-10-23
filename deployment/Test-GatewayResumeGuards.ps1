#Requires -Version 7.2
$ErrorActionPreference='Stop'
$paths=@('Apply-GatewayRelease.ps1','Resume-GatewayRelease.ps1','release_fence.ps1')
$asts=@{}
foreach($name in $paths){
 $tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot $name),[ref]$tokens,[ref]$errors)
 if($errors.Count){throw 'PUBLIC_PARSE_ERROR'}
 $asts[$name]=$ast
 foreach($f in @($ast.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$false))){. ([scriptblock]::Create($f.Extent.Text))}
}
$script:checks=@()
function Equal($a,$b){if($a -cne $b){throw 'PUBLIC_ASSERTION_FAILED'}}
function MustThrow([scriptblock]$body,[string]$expected){
 $caught=$false;try{& $body}catch{if($_.Exception.Message -notlike ('*'+$expected+'*')){throw};$caught=$true}
 if(-not $caught){throw 'EXPECTED_PUBLIC_REFUSAL'}
}
function Check([string]$name,[scriptblock]$body){& $body;$script:checks+=@($name)}
function SaveReceipt($record){$script:saveCount++}
$service=New-Object -ComObject Schedule.Service;$service.Connect()
$publicSecurity='O:SYG:SYD:(A;;FA;;;SY)(A;;FA;;;BA)'
$publicXml=@'
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.3" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>PUBLIC fixed task fixture</Description>
    <URI>\PUBLIC-RESUME-FIXTURE</URI>
  </RegistrationInfo>
  <Principals>
    <Principal id="Author"><UserId>S-1-5-18</UserId><RunLevel>HighestAvailable</RunLevel></Principal>
  </Principals>
  <Settings><ExecutionTimeLimit>PT0S</ExecutionTimeLimit><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy></Settings>
  <Triggers><BootTrigger /></Triggers>
  <Actions Context="Author"><Exec><Command>C:\PUBLIC\python.exe</Command><Arguments>PUBLIC-NEW-TARGET</Arguments><WorkingDirectory>C:\PUBLIC</WorkingDirectory></Exec></Actions>
</Task>
'@
$definition=$service.NewTask(0);$definition.XmlText=$publicXml
$publicBefore=$definition.XmlText;$publicBeforeHash=StringHash $publicBefore
$definition.RegistrationInfo.SecurityDescriptor=$publicSecurity
$publicAfter=$definition.XmlText;$publicAfterHash=StringHash $publicAfter
function NewTaskStub([string]$xml,[string]$security){
 $t=[pscustomobject]@{Xml=$xml;Definition=[pscustomobject]@{XmlText=$xml};Security=$security}
 $t|Add-Member ScriptMethod GetSecurityDescriptor {param($mask)return $this.Security}
 return $t
}
function NewRecord {return @{Phase='PUBLIC';LibrarianBarrier=$null;OwnedStarts=@();StoppedTasks=@(@{role='gateway'});BaseListeners=@();TaskUpdated=$true;DatabaseChecks=@()}}
Check 'actual Scheduler memory reproduces observed descriptor insertion hash mismatch' {
 Equal ($publicBeforeHash -eq $publicAfterHash) $false
 MustThrow {VerifiedRegisteredTaskXml (NewTaskStub $publicAfter $publicSecurity) $publicBeforeHash (StringHash $publicSecurity)} 'REGISTERED_TASK_DEFINITION_OR_SECURITY_DRIFT'
}
Check 'explicit SDDL planned definition matches complete registered schema serialization' {
 Equal (VerifiedRegisteredTaskXml (NewTaskStub $publicAfter $publicSecurity) $publicAfterHash (StringHash $publicSecurity)) $publicAfter
}
Check 'unrelated Scheduler setting change refuses despite unchanged action' {
 $changed=$publicAfter.Replace('<Priority>7</Priority>','<Priority>6</Priority>')
 Equal ($changed -eq $publicAfter) $false
 MustThrow {VerifiedRegisteredTaskXml (NewTaskStub $changed $publicSecurity) $publicAfterHash (StringHash $publicSecurity)} 'REGISTERED_TASK_DEFINITION_OR_SECURITY_DRIFT'
}
Check 'external task security change refuses' {
 MustThrow {VerifiedRegisteredTaskXml (NewTaskStub $publicAfter 'PUBLIC-UNKNOWN-SECURITY') $publicAfterHash (StringHash $publicSecurity)} 'REGISTERED_TASK_DEFINITION_OR_SECURITY_DRIFT'
}
Check 'raw XML formatting may differ but all canonical fields remain exact' {
 $raw=$publicAfter.Replace('  <','    <')
 Equal ($raw -eq $publicAfter) $false
 Equal (VerifiedRegisteredTaskXml (NewTaskStub $raw $publicSecurity) $publicAfterHash (StringHash $publicSecurity)) $raw
}
Check 'resume proof accepts only exact same security metadata inserted in full old definition' {
 $resumeIntent=@{definition_xmltext_sha256=$publicAfterHash;security_sha256=StringHash $publicSecurity;before_registration_definition_sha256=$publicBeforeHash}
 function GetTask($role){return (NewTaskStub $publicAfter $publicSecurity)}
 $null=AssertResumeTaskProof
}
Check 'resume task proof refuses another field even if approved raw check is stubbed' {
 $resumeIntent=@{definition_xmltext_sha256=$publicAfterHash;security_sha256=StringHash $publicSecurity;before_registration_definition_sha256=$publicBeforeHash}
 function GetTask($role){return (NewTaskStub ($publicAfter.Replace('PUBLIC-NEW-TARGET','PUBLIC-OTHER')) $publicSecurity)}
 MustThrow {AssertResumeTaskProof} 'RESUME_TASK_DEFINITION_OR_SECURITY_DRIFT'
}
Check 'resume task proof refuses mismatched saved pre-registration digest' {
 $resumeIntent=@{definition_xmltext_sha256=$publicAfterHash;security_sha256=StringHash $publicSecurity;before_registration_definition_sha256=('0'*64)}
 function GetTask($role){return (NewTaskStub $publicAfter $publicSecurity)}
 MustThrow {AssertResumeTaskProof} 'RESUME_FULL_PRIOR_DEFINITION_PROOF_FAILED'
}
Check 'start-only operation contains no stop registration install migrate or adoption path' {
 $node=@($asts['Resume-GatewayRelease.ps1'].FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'InvokeResumeStart'},$false))[0]
 $names=@($node.Body.FindAll({param($n)$n -is [Management.Automation.Language.CommandAst]},$true)|ForEach-Object {$_.GetCommandName()})
 foreach($forbidden in @('StopExact','UpdateTask','InstallRelease','AdoptFencedBaseline','InvokeFencedMigration','AcquireKnowledgeFence')){Equal ($forbidden -in $names) $false}
 Equal @($names|Where-Object {$_ -eq 'StartExact'}).Count 1
}
Check 'producer busy refuses before single Run' {
 function AssertMaintenance{};function AssertAssets{};function AssertProtected{};function AssertKnownFiles{};function AssertBase($r){}
 function AssertResumeTaskProof{};function AssertReadyForStart($r,$role){};function AssertResumeInstalled{};function AssertDbState($r){}
 function AcquireLibrarianBarrier($r){throw 'PUBLIC_PRODUCER_BUSY'}
 function StartExact {throw 'UNEXPECTED_RUN'}
 MustThrow {InvokeResumeStart (NewRecord)} 'PUBLIC_PRODUCER_BUSY'
}
Check 'existing DB drift refuses before any new producer marker or Run' {
 function AssertMaintenance{};function AssertAssets{};function AssertProtected{};function AssertKnownFiles{};function AssertBase($r){}
 function AssertResumeTaskProof{};function AssertReadyForStart($r,$role){};function AssertResumeInstalled{}
 function AssertDbState($r){throw 'PUBLIC_DB_DRIFT'}
 function AcquireLibrarianBarrier {throw 'UNEXPECTED_MARKER'}
 function StartExact {throw 'UNEXPECTED_RUN'}
 MustThrow {InvokeResumeStart (NewRecord)} 'PUBLIC_DB_DRIFT'
}
Check 'single Run failure releases only the owned producer barrier with no retry' {
 function AssertMaintenance{};function AssertAssets{};function AssertProtected{};function AssertKnownFiles{};function AssertBase($r){}
 function AssertResumeTaskProof{};function AssertReadyForStart($r,$role){};function AssertResumeInstalled{};function AssertDbState($r){}
 function AcquireLibrarianBarrier($r){$r.LibrarianBarrier=@{released=$false}}
 function AssertLibrarianBarrier($r){}
 function StartExact($r,$role){$script:runCount++;throw 'PUBLIC_RUN_FAILURE'}
 function ReleaseLibrarianBarrier($r){$r.LibrarianBarrier.released=$true}
 $script:runCount=0;$r=NewRecord;MustThrow {InvokeResumeStart $r} 'PUBLIC_RUN_FAILURE'
 Equal $script:runCount 1;Equal $r.LibrarianBarrier.released $true
}
Check 'single successful Run remains producer-fenced through final identity health and code checks' {
 function AssertMaintenance{};function AssertAssets{};function AssertProtected{};function AssertKnownFiles{};function AssertBase($r){}
 function AssertResumeTaskProof{};function AssertReadyForStart($r,$role){};function AssertResumeInstalled{};function AssertDbState($r){}
 function AcquireLibrarianBarrier($r){$r.LibrarianBarrier=@{released=$false}}
 function AssertLibrarianBarrier($r){Equal $r.LibrarianBarrier.released $false}
 function StartExact($r,$role){Equal $r.LibrarianBarrier.released $false;$script:runCount++}
 function AssertIdle($r){Equal $r.LibrarianBarrier.released $false}
 function AssertOwned($r,$role){return @{state=4;instances=@(@{pid=123;process_birth_filetime='PUBLIC'})}}
 function AssertAdminImage($snap){return @{image='PUBLIC'}}
 function ReleaseLibrarianBarrier($r){$r.LibrarianBarrier.released=$true}
 $script:runCount=0;$r=NewRecord;InvokeResumeStart $r
 Equal $script:runCount 1;Equal $r.LibrarianBarrier.released $true;Equal $r.FinalGateway.state 4
}
Check 'resume GO required and expired GO rejected with UTC DateTime type' {
 $Mode='Resume';$recoveryIntentPath='PUBLIC';$ApprovedRecoveryIntentSha256='public-intent';$intentPath='PUBLIC-OLD';$intentHash='public-intent'
 function FileHash($p){return 'public-intent'}
 function ReadResumeJson($p,$sha){return @{phase='ClientMaintenanceConfirmedResumeGO';intent_sha256='public-intent';script_sha256='public-script';client_active_requests=0;client_queued_requests=0;no_new_requests_until_finished=$true;parent_message_reference='PUBLIC';created_utc=[DateTime]::UtcNow.AddMinutes(-16)}}
 function ResolveResumePath($p,$base){return $p}
 $resumePacket=$PSScriptRoot;$resumeIntent=@{script_sha256='public-script'};$ParentGoSha256='PUBLIC'
 MustThrow {AssertResumeMaintenance} 'RESUME_PARENT_GO_EXPIRED'
}
Check 'all prior committed artifacts are fixed and no second adoption or migration is accepted' {
 $intentHash='PUBLIC-INTENT';$packet=$PSScriptRoot
 $resumeIntent=@{original_script_sha256='PUBLIC-SCRIPT';arguments_sha256='PUBLIC-ARGS';before_registration_definition_sha256='PUBLIC-DEF';security_sha256='PUBLIC-SD';existing_artifacts=@{}}
 $prior=@{Phase='HOLD_PRESERVE_CURRENT_STATE_NO_AUTOMATIC_RESTORE';FailureCode='TASK_DEFINITION_DRIFT';FailedPhase='task-action-update-requested';IntentSha256=$intentHash;ScriptSha256='PUBLIC-SCRIPT';StoppedTasks=@(@{role='gateway';operation_mode='Apply'});OwnedStarts=@();KnowledgeFence=@{released=$true};LibrarianBarrier=@{released=$true};Migration=@{status='ADDITIVE_MIGRATION_VERIFIED';existing_rows_unchanged=$true};TaskUpdate=@{new_arguments_sha256='PUBLIC-ARGS';new_definition_sha256='PUBLIC-DEF';security_sha256='PUBLIC-SD'}}
 foreach($name in @('EffectiveBaselineArtifact','MigrationArtifact','FencedAdoptionArtifact')){$prior[$name]=@{path='PUBLIC';sha256='PUBLIC-SHA'};$resumeIntent.existing_artifacts[$name]=@{path='PUBLIC';sha256='PUBLIC-SHA'}}
 function SafePath($p,$base){return $p};function FileHash($p){return 'PUBLIC-SHA'}
 AssertResumePrior $prior
 $prior.OwnedStarts=@(@{role='gateway'});MustThrow {AssertResumePrior $prior} 'PRIOR_HOLD_NOT_EXACTLY_QUALIFIED'
 $prior.OwnedStarts=@();$prior.EffectiveBaselineArtifact.sha256='UNKNOWN'
 MustThrow {AssertResumePrior $prior} 'EXISTING_COMMITTED_ARTIFACT_DRIFT'
}
Check 'initial resume receipt creation never replaces unknown existing evidence' {
 $packet=Join-Path ([IO.Path]::GetTempPath()) ('localbrain-resume-receipt-public-'+[guid]::NewGuid().ToString('N'))
 $null=New-Item -ItemType Directory -Path $packet;$receiptPath=Join-Path $packet 'execution.json'
 [IO.File]::WriteAllText($receiptPath,'PUBLIC-UNKNOWN-RECEIPT')
 try{
  MustThrow {SaveInitialResumeReceipt (NewRecord)} 'RESUME_INITIAL_RECEIPT_CREATE_NEW_REFUSED'
  Equal ([IO.File]::ReadAllText($receiptPath)) 'PUBLIC-UNKNOWN-RECEIPT'
 }finally{Remove-Item -LiteralPath $receiptPath;Remove-Item -LiteralPath $packet}
}
Check 'new resume receipt is created once and repeated initial creation preserves first bytes' {
 $packet=Join-Path ([IO.Path]::GetTempPath()) ('localbrain-resume-receipt-public-'+[guid]::NewGuid().ToString('N'))
 $null=New-Item -ItemType Directory -Path $packet;$receiptPath=Join-Path $packet 'execution.json'
 try{
  SaveInitialResumeReceipt (NewRecord);$first=[IO.File]::ReadAllText($receiptPath)
  Equal (($first|ConvertFrom-Json).Phase) 'PUBLIC'
  MustThrow {SaveInitialResumeReceipt (NewRecord)} 'RESUME_INITIAL_RECEIPT_CREATE_NEW_REFUSED'
  Equal ([IO.File]::ReadAllText($receiptPath)) $first
 }finally{Remove-Item -LiteralPath $receiptPath;Remove-Item -LiteralPath $packet}
}
Check 'Int32 zero counts never erase invalid GO phase hashes or maintenance promise' {
 $Mode='Resume';$recoveryIntentPath='PUBLIC';$ApprovedRecoveryIntentSha256='public-intent';$intentPath='PUBLIC-OLD';$intentHash='public-intent'
 function FileHash($p){return 'public-intent'}
 function ResolveResumePath($p,$base){return $p}
 function ReadResumeJson($p,$sha){return $script:publicGo}
 $resumePacket=$PSScriptRoot;$resumeIntent=@{script_sha256='public-script'};$ParentGoSha256='PUBLIC'
 $changes=@(@{key='phase';value='PUBLIC-WRONG'},@{key='intent_sha256';value='wrong'},@{key='script_sha256';value='wrong'},@{key='no_new_requests_until_finished';value=$false},@{key='parent_message_reference';value=' '},@{key='client_active_requests';value=$true},@{key='client_queued_requests';value='0'})
 foreach($change in $changes){
  $script:publicGo=@{phase='ClientMaintenanceConfirmedResumeGO';intent_sha256='public-intent';script_sha256='public-script';client_active_requests=[int]0;client_queued_requests=[int]0;no_new_requests_until_finished=$true;parent_message_reference='PUBLIC';created_utc=[DateTime]::UtcNow}
  $script:publicGo[$change.key]=$change.value
  MustThrow {AssertResumeMaintenance} 'RESUME_PARENT_GO_INVALID'
 }
}
Check 'valid fresh resume GO accepts only explicit Int32 or Int64 zero counts' {
 $Mode='Resume';$recoveryIntentPath='PUBLIC';$ApprovedRecoveryIntentSha256='public-intent';$intentPath='PUBLIC-OLD';$intentHash='public-intent'
 function FileHash($p){return 'public-intent'}
 function ResolveResumePath($p,$base){return $p}
 function ReadResumeJson($p,$sha){return $script:publicGo}
 $resumePacket=$PSScriptRoot;$resumeIntent=@{script_sha256='public-script'};$ParentGoSha256='PUBLIC'
 foreach($zero in @([int]0,[long]0)){
  $script:publicGo=@{phase='ClientMaintenanceConfirmedResumeGO';intent_sha256='public-intent';script_sha256='public-script';client_active_requests=$zero;client_queued_requests=$zero;no_new_requests_until_finished=$true;parent_message_reference='PUBLIC';created_utc=[DateTime]::UtcNow}
  AssertResumeMaintenance
 }
}
Check 'bootstrap and mutable staging file cannot substitute each other against fixed manifest' {
 $packet=Join-Path $PSScriptRoot 'PUBLIC-PACKET'
 $program=Join-Path $PSScriptRoot 'PUBLIC-PROGRAM'
 $root='PUBLIC-DATA';$intent=@{program_root=$program;source_commit='PUBLIC-COMMIT';manifest_sha256='PUBLIC-MANIFEST'};$resumeIntent=@{pointer_sha256='PUBLIC-POINTER'}
 function SafePath($p,$base){return $p}
 function ReadResumeJson($p,$sha){return @{source_commit='PUBLIC-COMMIT';release_root=(Join-Path $program 'releases\PUBLIC-COMMIT');data_root=$root;manifest_sha256='PUBLIC-MANIFEST'}}
 function GetFixedManifest {return @{files=@(@{path='deployment/gateway_entry.py';sha256='PUBLIC-PINNED-ENTRY'})}}
 function FileHash($p){
  if($p.EndsWith('release-manifest.json')){return 'PUBLIC-MANIFEST'}
  if($p.Contains('releases')){return 'PUBLIC-PINNED-ENTRY'}
  return 'PUBLIC-SUBSTITUTED-SAME-BYTES'
 }
 MustThrow {AssertResumeInstalled} 'INSTALLED_BOOTSTRAP_DRIFT'
}
@{status='PASS';checks=$script:checks.Count;cases=$script:checks;registered_task_operations=0;process_starts=0;production_reads=0;database_operations=0;network_calls=0;model_requests=0}|ConvertTo-Json -Depth 5
