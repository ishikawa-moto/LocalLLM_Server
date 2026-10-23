#Requires -Version 7.2
$ErrorActionPreference='Stop'
$scriptPath=Join-Path $PSScriptRoot 'Apply-GatewayRelease.ps1'
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($scriptPath,[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'SYNTAX_ERRORS'}
# Import only function definitions, never the activation script body.
$functions=@($ast.FindAll({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst]},$false))
foreach($definition in $functions){. ([ScriptBlock]::Create($definition.Extent.Text))}
$script:passed=0;$script:results=@()
function Check([string]$name,[scriptblock]$body){& $body;$script:passed++;$script:results+=@($name)}
function MustThrow([scriptblock]$body,[string]$expected){$caught=$false;try{& $body}catch{if($_.Exception.Message -notlike ('*'+$expected+'*')){throw};$caught=$true};if(-not $caught){throw "Expected rejection: $expected"}}
function Equal($actual,$expected){if($actual -cne $expected){throw "Fixture assertion failed"}}
$id=@{instance_guid='00000000-0000-0000-0000-000000000001';pid=101;process_birth_filetime='100'}
$other=@{instance_guid=$id.instance_guid;pid=101;process_birth_filetime='200'}
$running=@{state=4;instances=@($id);listener_pids=@(101)}
$ready=@{state=3;instances=@();listener_pids=@()}
function NewRecord {return @{BeforeTasks=@(@{role='gateway';instances=@($id)});StoppedTasks=@();OwnedStarts=@();Phase='fixture';BaseListeners=@();DatabaseBefore=@{aggregate_sha256='a'}}}
# This parent-scope function always overrides disk-writing SaveReceipt in every case.
function SaveReceipt($record){$script:saved+=@(($record|ConvertTo-Json -Depth 15 -Compress))}
$Mode='Deploy'
function AssertBaseServices {}
Check 'same identity' {Equal (SameIdentity $id $id) $true}
Check 'birth reuse refused' {Equal (SameIdentity $id $other) $false}
Check 'PID reuse refused' {Equal (SameIdentity $id @{instance_guid=$id.instance_guid;pid=102;process_birth_filetime='100'}) $false}
Check 'protected PID birth checked' {
 function Listeners($role){return @(101)}
 function ProcessBirth($processId){return '200'}
 MustThrow {AssertBase @{BaseListeners=@(@{role='brain';processes=@($id)})}} 'PROTECTED_PROCESS_IDENTITY_DRIFT'
}
Check 'optional absent model accepted' {
 function Listeners($role){return @()}
 AssertBase @{BaseListeners=@(@{role='llm';processes=@()})}
}
Check 'unexpected model startup refused' {
 function Listeners($role){return @(202)}
 MustThrow {AssertBase @{BaseListeners=@(@{role='llm';processes=@()})}} 'PROTECTED_LISTENER_COUNT_DRIFT'
}
Check 'foreign listener refused' {
 function Snapshot($role){return @{state=4;instances=@($id);listener_pids=@(202)}}
 MustThrow {AssertOwned (NewRecord) 'gateway'} 'FOREIGN_LISTENER'
}
Check 'replacement birth refused' {
 function Snapshot($role){return @{state=4;instances=@($other);listener_pids=@(101)}}
 MustThrow {AssertOwned (NewRecord) 'gateway'} 'TASK_INSTANCE_PID_BIRTH_DRIFT'
}
Check 'unexplained Ready refused' {
 function Snapshot($role){return $ready}
 MustThrow {AssertOwned (NewRecord) 'gateway'} 'UNEXPLAINED_STOPPED_TASK'
}
Check 'busy prevents stop' {
 function AssertMaintenance{};function AssertAssets{};function AssertProtected{};function AssertKnownFiles{};function AssertBase($record){}
 function Snapshot($role){return $running}
 function AssertIdle($record){throw 'FIXTURE_BUSY'}
 function GetTask($role){throw 'UNEXPECTED_TASK_ACCESS'}
 MustThrow {StopExact (NewRecord) 'gateway'} 'FIXTURE_BUSY'
}
Check 'database guard precedes Run' {
 function AssertMaintenance{};function AssertAssets{};function AssertProtected{};function AssertKnownFiles{};function AssertBase($record){}
 function AssertReadyForStart($record,$role){}
 function AssertDbState($record){throw 'FIXTURE_DB_CHANGED'}
 function GetTask($role){throw 'UNEXPECTED_RUN'}
 MustThrow {StartExact (NewRecord) 'gateway'} 'FIXTURE_DB_CHANGED'
}
Check 'repeat start refused' {
 function AssertMaintenance{};function AssertAssets{};function AssertProtected{};function AssertKnownFiles{};function AssertBase($record){}
 function AssertReadyForStart($record,$role){};function AssertDbState($record){}
 function GetTask($role){throw 'UNEXPECTED_RUN'}
 $record=NewRecord;$record.OwnedStarts=@(@{role='gateway';operation_mode='Deploy'})
 MustThrow {StartExact $record 'gateway'} 'ONE_START_PER_TASK_PER_MODE_NO_RETRY'
}
Check 'Run GUID saved before observation failure' {
 function AssertMaintenance{};function AssertAssets{};function AssertProtected{};function AssertKnownFiles{};function AssertBase($record){}
 function AssertReadyForStart($record,$role){};function AssertDbState($record){}
 function GetTask($role){
  $task=[pscustomobject]@{}
  $task|Add-Member ScriptMethod Run {$script:runCount++;return [pscustomobject]@{InstanceGuid='00000000-0000-0000-0000-000000000002'}}
  return $task
 }
 function Snapshot($role){throw 'FIXTURE_OBSERVATION_FAILED'}
 $script:saved=@();$script:runCount=0;$record=NewRecord
 MustThrow {StartExact $record 'gateway'} 'FIXTURE_OBSERVATION_FAILED'
 Equal $script:runCount 1;Equal $record.OwnedStarts.Count 1
 $last=$script:saved[-1]|ConvertFrom-Json
 Equal $last.OwnedStarts[0].instance_guid '00000000-0000-0000-0000-000000000002'
 Equal $last.Phase 'gateway-start-requested'
}
Check 'normal stop reaches Ready once' {
 function AssertMaintenance{};function AssertAssets{};function AssertProtected{};function AssertKnownFiles{};function AssertBase($record){};function AssertIdle($record){}
 function ProcessBirth($processId){return '100'}
 function Snapshot($role){if($script:stopCount){return $ready};return $running}
 function GetTask($role){
  $live=[pscustomobject]@{InstanceGuid=$id.instance_guid;EnginePID=101}
  $live|Add-Member ScriptMethod Stop {$script:stopCount++}
  $task=[pscustomobject]@{Live=$live}
  $task|Add-Member ScriptMethod GetInstances {param($flags) return @($this.Live)}
  return $task
 }
 $script:saved=@();$script:stopCount=0;$record=NewRecord
 StopExact $record 'gateway'
 Equal $script:stopCount 1;Equal $record.StoppedTasks.Count 1;Equal $record.Phase 'gateway-ready-after-owned-stop'
}
Check 'normal start checks health once' {
 function AssertMaintenance{};function AssertAssets{};function AssertProtected{};function AssertKnownFiles{};function AssertBase($record){}
 function AssertDbState($record){}
 function Snapshot($role){if($script:runCount){return @{state=4;instances=@($script:newId);listener_pids=@(303)}};return $ready}
 function AssertIdle($record){$script:healthCount++}
 function GetTask($role){
  $task=[pscustomobject]@{}
  $task|Add-Member ScriptMethod Run {$script:runCount++;return [pscustomobject]@{InstanceGuid=$script:newId.instance_guid}}
  return $task
 }
 $script:newId=@{instance_guid='00000000-0000-0000-0000-000000000003';pid=303;process_birth_filetime='300'}
 $script:runCount=0;$script:healthCount=0;$script:saved=@();$record=NewRecord;$record.StoppedTasks=@(@{role='gateway'})
 StartExact $record 'gateway'
 Equal $script:runCount 1;Equal $script:healthCount 1;Equal $record.OwnedStarts[0].health 'passed'
}

Check 'database aggregate equality' {
 function InvokePinned($relative,$arguments){return @{aggregate_sha256='a'}}
 AssertDbState (NewRecord)
}
Check 'database aggregate drift refused' {
 function InvokePinned($relative,$arguments){return @{aggregate_sha256='b'}}
 MustThrow {AssertDbState (NewRecord)} 'DATABASE_LOGICAL_STATE_DRIFT'
}
Check 'missing Parent GO refused before any mutation' {
 $Mode='Apply';$ParentGoSha256='';$intentPath='fixture';$intentHash='a';$packet=$PSScriptRoot
 function FileHash($path){return 'a'}
 MustThrow {AssertMaintenance} 'PARENT_APPLY_GO_REQUIRED'
}
Check 'Parent busy gate refused' {
 $Mode='Apply';$ParentGoSha256='a'*64;$intentHash='a'*64;$intentPath='fixture';$packet=$PSScriptRoot
 $intent=@{script_sha256='b'}
 function FileHash($path){return 'a'*64}
 function Get-Content {param($LiteralPath,[switch]$Raw)
  return (@{phase='ClientMaintenanceConfirmedApplyGO';intent_sha256=('a'*64);script_sha256='b';client_active_requests=1;client_queued_requests=0;no_new_requests_until_finished=$true;parent_message_reference='PUBLIC';created_utc=[DateTime]::UtcNow.ToString('o')}|ConvertTo-Json)
 }
 MustThrow {AssertMaintenance} 'PARENT_MAINTENANCE_GATE_INVALID'
}
Check 'Parent expired gate refused' {
 $Mode='Apply';$ParentGoSha256='a'*64;$intentHash='a'*64;$intentPath='fixture';$packet=$PSScriptRoot
 $intent=@{script_sha256='b'}
 function FileHash($path){return 'a'*64}
 function Get-Content {param($LiteralPath,[switch]$Raw)
  return (@{phase='ClientMaintenanceConfirmedApplyGO';intent_sha256=('a'*64);script_sha256='b';client_active_requests=0;client_queued_requests=0;no_new_requests_until_finished=$true;parent_message_reference='PUBLIC';created_utc=[DateTime]::UtcNow.AddMinutes(-16).ToString('o')}|ConvertTo-Json)
 }
 MustThrow {AssertMaintenance} 'PARENT_MAINTENANCE_GATE_EXPIRED'
}
Check 'existing ProgramData root is preserved without writes' {
 function AssertMaintenance{};function AssertAssets{};function AssertProtected{};function AssertKnownFiles{};function AssertBase($record){}
 $intent=@{program_root=$PSScriptRoot}
 MustThrow {InstallRelease (NewRecord)} 'EXISTING_PROGRAM_ROOT_HOLD'
}


Check 'unhealthy base prevents Run' {
 function AssertMaintenance{};function AssertAssets{};function AssertProtected{};function AssertKnownFiles{};function AssertBase($record){}
 function AssertReadyForStart($record,$role){};function AssertDbState($record){}
 function AssertBaseServices {throw 'BASE_SERVICES_UNHEALTHY_NO_AUTOSTART'}
 function GetTask($role){throw 'UNEXPECTED_RUN'}
 MustThrow {StartExact (NewRecord) 'gateway'} 'BASE_SERVICES_UNHEALTHY_NO_AUTOSTART'
}

[ordered]@{status='PASS';checks=$script:passed;cases=$script:results;script_sha256=FileHash $scriptPath;production_operations=0;database_reads=0;network_calls=0;model_requests=0}|ConvertTo-Json -Depth 5
