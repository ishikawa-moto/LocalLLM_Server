# Gateway-only adaptation of the previously deployed Forward UAC worker.
# Existing runtime ACL/lock and exact-byte protected execution are retained.
#Requires -Version 7.2
param([ValidateSet('Describe','Run')][string]$Mode='Describe',
 [string]$Packet='', [ValidatePattern('^[a-f0-9]{64}$')][string]$ApprovedIntentSha256='',
 [ValidateSet('Apply','Restore')][string]$Operation='Apply',
 [string]$ApplyReceiptSha256='',
 [ValidatePattern('^[a-f0-9]{64}$')][string]$ParentGoSha256='',
 [ValidatePattern('^[a-f0-9]{64}$')][string]$WorkerSourceSha256='',
 [ValidatePattern('^[a-f0-9-]{36}$')][string]$LaunchId='')
$ErrorActionPreference='Stop'
if($Mode -eq 'Describe'){@{purpose='One native-UAC execution of the approved fixed Apply';self_elevates=$false;automatic_retry=$false;production_operations=0}|ConvertTo-Json;exit 0}
$packet=[IO.Path]::GetFullPath($Packet)
$program='C:\ProgramData\LocalBrainServer'
$launchRoot=Join-Path $program ('gateway-forward-uac-gateway-priority-f5df6bd-20261103-'+$Operation.ToLowerInvariant())
$intentHash=$ApprovedIntentSha256
$applyHash=$null;$checksHash=$null;$python=$null;$pythonHash=$null
function Hash([byte[]]$bytes){return [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($bytes)).ToLowerInvariant()}
function Safe([string]$path){
 if(-not [IO.Path]::IsPathFullyQualified($path)){throw 'ABSOLUTE_PATH_REQUIRED'}
 $part=[IO.Path]::GetFullPath($path)
 while($part){
  if(Test-Path -LiteralPath $part){if((Get-Item -LiteralPath $part -Force).Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'REPARSE_REFUSED'}}
  $next=[IO.Path]::GetDirectoryName($part);if($next -eq $part){break};$part=$next
 }
}
function FixedBytes([string]$path,[string]$expected){Safe $path;$bytes=[IO.File]::ReadAllBytes($path);if((Hash $bytes) -cne $expected){throw 'FIXED_BYTES_DRIFT'};return ,$bytes}
function SaveNew([string]$name,$value){
 if($name -notin @('worker-started.json','worker-completion.json','runtime-qualified.json','activation-go.json','apply-execution.json','apply-stdout.txt')){throw 'UNAPPROVED_RECEIPT_NAME'}
 $path=Join-Path $launchRoot $name;Safe $path
 [byte[]]$bytes=@()
 if($value -is [byte[]]){$bytes=$value}else{$bytes=[Text.UTF8Encoding]::new($false).GetBytes(($value|ConvertTo-Json -Depth 40)+[char]10)}
 $stream=[IO.FileStream]::new($path,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
 try{$stream.Write($bytes,0,$bytes.Length);$stream.Flush($true)}finally{$stream.Dispose()}
}
function VerifyParentGo($go,$intent,[string]$intentHash,[string]$applyHash){
 if($go.phase -cne 'GatewayOnlyMaintenanceGO' -or $go.operation -cne $Operation -or
    $go.continue_gui_verified -cne $true -or $go.continue_gui_evidence_reference -notmatch '\S' -or $go.intent_sha256 -cne $intentHash -or
    $go.source_commit -cne $intent.source_commit -or $go.script_sha256 -cne $applyHash -or
    $go.manifest_sha256 -cne $intent.manifest_sha256 -or $go.client_source -cne $intent.client_source -or
    $go.deployment_id -cne $intent.deployment_id -or
    ($go.client_active_requests -isnot [int] -and $go.client_active_requests -isnot [long]) -or
    ($go.client_queued_requests -isnot [int] -and $go.client_queued_requests -isnot [long]) -or
    $go.client_active_requests -ne 0 -or $go.client_queued_requests -ne 0 -or
    $go.all_chat_slack_continue_idle -cne $true -or $go.no_new_requests_until_finished -cne $true -or
    $go.human_approval_reference -notmatch '\S'){throw 'PARENT_GO_BINDING_INVALID'}
 if($go.created_utc -is [DateTime]){
  if($go.created_utc.Kind -eq [DateTimeKind]::Unspecified){throw 'PARENT_GO_TIMEZONE_REQUIRED'}
  $date=$go.created_utc.ToUniversalTime()
 }else{
  if([string]$go.created_utc -notmatch '(Z|[+-]\d\d:\d\d)$'){throw 'PARENT_GO_TIMEZONE_REQUIRED'}
  $date=[DateTimeOffset]::Parse([string]$go.created_utc).UtcDateTime
 }
 if($date -gt [DateTime]::UtcNow -or ([DateTime]::UtcNow-$date).TotalSeconds -gt 900){throw 'PARENT_GO_EXPIRED'}
 return $date
}
$owned=$false;$applied=$false;$failure=$null;$intent=$null
$runtimeLocks=[Collections.Generic.List[IO.FileStream]]::new()
$runtimeTrust=$null
$phase='preflight';$failureType=$null;$failureHResult=$null
try{
 $identity=[Security.Principal.WindowsIdentity]::GetCurrent()
 if(-not ([Security.Principal.WindowsPrincipal]$identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){throw 'NATIVE_UAC_ADMIN_TOKEN_REQUIRED'}
 if($ParentGoSha256 -cnotmatch '^[a-f0-9]{64}$' -or $WorkerSourceSha256 -cnotmatch '^[a-f0-9]{64}$' -or $LaunchId -cnotmatch '^[a-f0-9-]{36}$'){throw 'FIXED_LAUNCH_BINDING_REQUIRED'}
 $intentBytes=FixedBytes (Join-Path $packet 'intent.json') $intentHash
 $intent=[Text.Encoding]::UTF8.GetString($intentBytes)|ConvertFrom-Json -AsHashtable
 if($intent.schema -cne 'localbrain.gateway-forward-intent.v1' -or $intent.packet -cne $packet -or
    $intent.program_root -cne $program -or $intent.source_commit -cne 'f5df6bd12e9a87333f06948966296f80b1167d35' -or
    $intent.deployment_id -cne 'gateway-priority-f5df6bd-20261103'){throw 'FIXED_SCOPE_DRIFT'}
 $applyHash=$intent.script_sha256;$checksHash=$intent.checks_sha256;$python=$intent.python;$pythonHash=$intent.python_sha256
 if((Get-Process -Id $PID).Path -cne $intent.powershell){throw 'POWERSHELL_RUNTIME_DRIFT'}
 $null=FixedBytes $intent.powershell $intent.powershell_sha256
 $applyBytes=FixedBytes (Join-Path $packet 'Apply-GatewayForward.ps1') $applyHash
 $checksBytes=FixedBytes (Join-Path $packet 'gateway_forward_checks.py') $checksHash
 $null=FixedBytes $python $pythonHash
 $goBytes=FixedBytes (Join-Path $packet ($Operation.ToLowerInvariant()+'-go.json')) $ParentGoSha256
 $go=[Text.Encoding]::UTF8.GetString($goBytes)|ConvertFrom-Json -AsHashtable
 $goDate=VerifyParentGo $go $intent $intentHash $applyHash
 if($Operation -ceq 'Restore' -and ($ApplyReceiptSha256 -cnotmatch '^[a-f0-9]{64}$' -or $go.apply_receipt_sha256 -cne $ApplyReceiptSha256)){throw 'RESTORE_RECEIPT_BINDING_REQUIRED'}
 Safe $program;Safe $launchRoot
 if(Test-Path -LiteralPath $launchRoot){throw 'PRIOR_LAUNCH_NO_RETRY'}
 # The existing parent already denies unprivileged writes/renames. Require that
 # exact effective access boundary before creating the protected child.
 $acl=Get-Acl -LiteralPath $program
 if(-not $acl.AreAccessRulesProtected -or $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -notin @('S-1-5-32-544','S-1-5-18')){throw 'PROGRAM_PARENT_ACL_UNSAFE'}
 $rules=@($acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]))
 if($rules.Count -ne 3){throw 'PROGRAM_PARENT_RULES_DRIFT'}
 foreach($rule in $rules){
  $sid=$rule.IdentityReference.Value
  if($rule.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow -or $sid -notin @('S-1-5-32-544','S-1-5-18','S-1-5-32-545')){throw 'PROGRAM_PARENT_RULES_DRIFT'}
  if($sid -eq 'S-1-5-32-545' -and [int64]$rule.FileSystemRights -ne 1179817){throw 'PROGRAM_PARENT_UNPRIVILEGED_WRITE'}
 }
 $null=New-Item -ItemType Directory -Path $launchRoot
 $newAcl=[Security.AccessControl.DirectorySecurity]::new()
 $admin=[Security.Principal.SecurityIdentifier]::new('S-1-5-32-544');$system=[Security.Principal.SecurityIdentifier]::new('S-1-5-18')
 $newAcl.SetOwner($admin);$newAcl.SetAccessRuleProtection($true,$false)
 $inherit=[Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit'
 foreach($sid in @($admin,$system)){$newAcl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($sid,'FullControl',$inherit,'None','Allow'))}
 # Read-only result visibility for the approving user; no user write/delete.
 $newAcl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($identity.User,'ReadAndExecute',$inherit,'None','Allow'))
 Set-Acl -LiteralPath $launchRoot -AclObject $newAcl
 $owned=$true
 SaveNew 'worker-started.json' @{schema='localbrain.chat-ocr-uac-worker.v1';launch_id=$LaunchId;pid=$PID;birth_filetime=[Diagnostics.Process]::GetCurrentProcess().StartTime.ToFileTimeUtc().ToString();started_utc=[DateTime]::UtcNow.ToString('o');administrator=$true;worker_source_sha256=$WorkerSourceSha256;intent_sha256=$intentHash;apply_sha256=$applyHash;parent_go_sha256=$ParentGoSha256;runtime_trust=$runtimeTrust;automatic_retry=$false}
 $phase='runtime_qualification'
 # The approving operator/Administrators/SYSTEM are existing runtime trustees.
 # Other actors must have no owner/write control; no existing ACL is changed.
 $trusted=@($identity.User.Value,'S-1-5-32-544','S-1-5-18')
 $readMask=[int64]([Security.AccessControl.FileSystemRights]'ReadAndExecute,Synchronize');$runtimeItems=0
 foreach($runtimeRoot in @((Split-Path $python -Parent),(Split-Path $intent.powershell -Parent))){
  $items=@((Get-Item -LiteralPath $runtimeRoot -Force))+@(Get-ChildItem -LiteralPath $runtimeRoot -Recurse -Force -ErrorAction Stop)
  foreach($item in $items){
   Safe $item.FullName;$a=Get-Acl -LiteralPath $item.FullName
   if($a.GetOwner([Security.Principal.SecurityIdentifier]).Value -notin $trusted){throw 'RUNTIME_UNTRUSTED_OWNER'}
   foreach($r in $a.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])){
    if($r.AccessControlType -eq [Security.AccessControl.AccessControlType]::Allow -and $r.IdentityReference.Value -notin $trusted -and
       ([int64]$r.FileSystemRights -band (-bnot $readMask)) -ne 0){throw 'RUNTIME_UNTRUSTED_WRITE_GRANT'}
   }
   if(-not $item.PSIsContainer){$runtimeLocks.Add([IO.FileStream]::new($item.FullName,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read))}
   $runtimeItems++
  }
 }
 $null=FixedBytes $python $pythonHash
 $runtimeTrust=@{checked_items=$runtimeItems;held_file_handles=$runtimeLocks.Count;untrusted_write_grants=0;existing_acl_changes=0;trusted_approving_operator=$identity.User.Value}
 SaveNew 'runtime-qualified.json' $runtimeTrust
 $goCheck=& $python -I -B -c ([Text.Encoding]::UTF8.GetString($checksBytes)) --mode Maintenance --packet $packet --intent-sha256 $intentHash --go-sha256 $ParentGoSha256 --operation $Operation --apply-receipt-sha256 $(if($Operation -ceq 'Restore'){$ApplyReceiptSha256}else{'not-applicable'})
 if($LASTEXITCODE){throw 'FRESH_MAINTENANCE_GO_INVALID'}
 if(([DateTime]::UtcNow-$goDate).TotalSeconds -gt 600){throw 'INSUFFICIENT_GO_TIME_BEFORE_APPLY'}
 $phase='protected_apply_copy'
 $protectedApply=Join-Path $launchRoot 'Apply-GatewayForward.ps1'
 $stream=[IO.FileStream]::new($protectedApply,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
 try{$stream.Write($applyBytes,0,$applyBytes.Length);$stream.Flush($true)}finally{$stream.Dispose()}
 $null=FixedBytes $protectedApply $applyHash
 $applied=$true
 $phase='apply_invoked'
 $result=& $protectedApply -Mode $Operation -Packet $packet -ApprovedIntentSha256 $intentHash -ParentGoSha256 $ParentGoSha256 -ApplyReceiptSha256 $ApplyReceiptSha256
 if($result){SaveNew 'apply-stdout.txt' ([Text.UTF8Encoding]::new($false).GetBytes(($result -join [char]10)))}
}catch{
 $failure=if($_.Exception.Message -cmatch '^[A-Z][A-Z_]{2,100}$'){$_.Exception.Message}else{'PRIVATE_ERROR_DETAILS_SUPPRESSED'}
 $failureType=$_.Exception.GetType().FullName;$failureHResult=$_.Exception.HResult
}finally{
 if($owned){
  $execution=$null;$executionObservation='unavailable';$activation=$null;$activationObservation='unavailable';$observationErrors=@()
  try{
   $operationAudit=if($Operation -ceq 'Restore'){$intent.audit_root+'-restore'}else{$intent.audit_root};$path=Join-Path $operationAudit 'execution.json';Safe $path
   if(Test-Path -LiteralPath $path -ErrorAction Stop){
    $bytes=[IO.File]::ReadAllBytes($path);$execution=[Text.Encoding]::UTF8.GetString($bytes)|ConvertFrom-Json -AsHashtable
    if($execution -isnot [System.Collections.IDictionary] -or $execution.intent_sha256 -cne $intentHash){throw 'RECEIPT_BINDING_UNKNOWN'}
    $executionObservation='observed'
    try{SaveNew 'apply-execution.json' $bytes}catch{$observationErrors+= 'RECEIPT_COPY_FAILED'}
   }
  }catch{$observationErrors+='RECEIPT_OBSERVATION_FAILED'}
  try{
   Safe $intent.activation_go
   if(Test-Path -LiteralPath $intent.activation_go -ErrorAction Stop){
    [byte[]]$activationBytes=[IO.File]::ReadAllBytes($intent.activation_go)
    $g=[Text.Encoding]::UTF8.GetString($activationBytes)|ConvertFrom-Json -AsHashtable
    if($g.schema -cne 'localbrain.chat-ocr-activation.v1' -or $g.intent_sha256 -cne $intentHash -or
       $g.source -cne $intent.source_commit -or $g.spec_sha256 -cne $intent.spec_sha256 -or
       $g.manifest_sha256 -cne $intent.manifest_sha256){throw 'ACTIVATION_BINDING_UNKNOWN'}
    $activation=$true;$activationObservation='verified_go_present'
    try{SaveNew 'activation-go.json' $activationBytes}catch{$observationErrors+='ACTIVATION_COPY_FAILED'}
   }else{
    $activation=$false;$activationObservation='go_absent_at_observation'
    if($executionObservation -eq 'observed' -and $execution.activation_committed -ceq $true){$activation=$null;$activationObservation='conflicting_observations'}
   }
  }catch{$observationErrors+='ACTIVATION_OBSERVATION_FAILED'}
  try{
   SaveNew 'worker-completion.json' @{schema='localbrain.chat-ocr-uac-completion.v1';launch_id=$LaunchId;pid=$PID;completed_utc=[DateTime]::UtcNow.ToString('o');worker_source_sha256=$WorkerSourceSha256;intent_sha256=$intentHash;apply_invocations=[int]$applied;error_code=$failure;failure_type=$failureType;failure_hresult=$failureHResult;worker_phase=$phase;execution_observation=$executionObservation;activation_observation=$activationObservation;activation_committed=$activation;phase=$execution.phase;requests=$execution.requests;observation_errors=$observationErrors;automatic_retry=$false;old_version_restore=($Operation -ceq 'Restore');operation=$Operation;old_db_restore=$false}
  }catch{if(-not $failure){$failure='COMPLETION_RECEIPT_UNAVAILABLE'}}
 }
 foreach($held in $runtimeLocks){$held.Dispose()}
}
if($failure){[Console]::Error.WriteLine($failure);exit 1}
