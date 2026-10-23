#Requires -Version 7.2
$ErrorActionPreference='Stop'
$scriptPath=Join-Path $PSScriptRoot 'Apply-GatewayRelease.ps1'
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($scriptPath,[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'SYNTAX_ERRORS'}
# Import only function definitions, never the activation script body.
$functions=@($ast.FindAll({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst]},$false))
foreach($definition in $functions){. ([ScriptBlock]::Create($definition.Extent.Text))}
$fenceAst=[Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot 'release_fence.ps1'),[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'FENCE_SYNTAX_ERRORS'}
foreach($f in @($fenceAst.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$false))){. ([scriptblock]::Create($f.Extent.Text))}
$script:passed=0;$script:results=@()
function Check([string]$name,[scriptblock]$body){& $body;$script:passed++;$script:results+=@($name)}
function MustThrow([scriptblock]$body,[string]$expected){$caught=$false;try{& $body}catch{if($_.Exception.Message -notlike ('*'+$expected+'*')){throw};$caught=$true};if(-not $caught){throw "Expected rejection: $expected"}}
function Equal($actual,$expected){if($actual -cne $expected){throw "Fixture assertion failed"}}
$id=@{instance_guid='00000000-0000-0000-0000-000000000001';pid=101;process_birth_filetime='100'}
$other=@{instance_guid=$id.instance_guid;pid=101;process_birth_filetime='200'}
$running=@{state=4;instances=@($id);listener_pids=@(101)}
$ready=@{state=3;instances=@();listener_pids=@()}
function NewRecord {return @{BeforeTasks=@(@{role='gateway';instances=@($id)});StoppedTasks=@();OwnedStarts=@();Phase='fixture';BaseListeners=@();DatabaseBefore=@{aggregate_sha256='a'};DatabaseChecks=@();MigrationArtifact=$null}}
# This parent-scope function always overrides disk-writing SaveReceipt in every case.
function SaveReceipt($record){$script:saved+=@(($record|ConvertTo-Json -Depth 15 -Compress))}
$Mode='Deploy'
$packet=$PSScriptRoot;$root='PUBLIC';$intent=@{database_before_sha256='original-fixed-hash'}
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

Check 'full database observation and classification retained' {
 function InvokePinned($relative,$arguments){return @{current_snapshot=@{aggregate_sha256='a'};comparison=@{compatible=$true;search_requests_delta=2}}}
 $record=NewRecord;AssertDbState $record
 Equal $record.LastDatabaseCheck.aggregate_sha256 'a';Equal $record.LastDatabaseComparison.search_requests_delta 2
 Equal $record.DatabaseChecks.Count 1
}
Check 'failed full database evidence retained without baseline refresh' {
 function InvokePinned($relative,$arguments){return @{current_snapshot=@{aggregate_sha256='b'};comparison=@{compatible=$false;reason_code='NONMETRICS_TABLE_DRIFT'}}}
 $record=NewRecord
 MustThrow {AssertDbState $record} 'DATABASE_POLICY_COMPARISON_HELD'
 Equal $record.DatabaseBefore.aggregate_sha256 'a';Equal $record.LastDatabaseCheck.aggregate_sha256 'b'
 Equal $record.DatabaseChecks.Count 1;Equal $record.LastDatabaseComparison.reason_code 'NONMETRICS_TABLE_DRIFT'
}
Check 'comparison always uses fixed effective baseline after migration' {
 $packet=$PSScriptRoot;$root='PUBLIC';$intent=@{database_before_sha256='original-fixed-hash'}
 function InvokePinned($relative,$arguments){
  Equal $arguments[1] 'Check'
  Equal $arguments[5] (Join-Path $packet 'database-before.json')
  Equal $arguments[7] 'original-fixed-hash'
  Equal $arguments[9] 'PUBLIC-MIGRATION';Equal $arguments[11] 'migration-hash'
  return @{current_snapshot=@{aggregate_sha256='new'};comparison=@{compatible=$true}}
 }
 $record=NewRecord;$record.EffectiveBaselineArtifact=@{path=(Join-Path $packet 'database-before.json');sha256='original-fixed-hash'};$record.MigrationArtifact=@{path='PUBLIC-MIGRATION';sha256='migration-hash'}
 $record.DatabaseAfterMigration=@{aggregate_sha256='never-use-as-new-anchor'}
 AssertDbState $record
}
Check 'failure codes retain fixed codes and suppress private message content' {
 try{throw 'DATABASE_POLICY_COMPARISON_HELD_NO_RESTORE'}catch{Equal (PublicFailureCode $_) 'DATABASE_POLICY_COMPARISON_HELD_NO_RESTORE'}
 try{throw 'PUBLIC fixture arbitrary message / not an allowlisted code'}catch{Equal (PublicFailureCode $_) 'PRIVATE_ERROR_MESSAGE_SUPPRESSED'}
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
 function ReadFixedJson {param($path,$expected,$failureCode)
  return (@{phase='ClientMaintenanceConfirmedApplyGO';intent_sha256=('a'*64);script_sha256='b';client_active_requests=1;client_queued_requests=0;no_new_requests_until_finished=$true;parent_message_reference='PUBLIC';created_utc=[DateTime]::UtcNow.ToString('o')})
 }
 MustThrow {AssertMaintenance} 'PARENT_MAINTENANCE_GATE_INVALID'
}
Check 'Parent expired gate refused' {
 $Mode='Apply';$ParentGoSha256='a'*64;$intentHash='a'*64;$intentPath='fixture';$packet=$PSScriptRoot
 $intent=@{script_sha256='b'}
 function FileHash($path){return 'a'*64}
 function ReadFixedJson {param($path,$expected,$failureCode)
  return (@{phase='ClientMaintenanceConfirmedApplyGO';intent_sha256=('a'*64);script_sha256='b';client_active_requests=0;client_queued_requests=0;no_new_requests_until_finished=$true;parent_message_reference='PUBLIC';created_utc=[DateTime]::UtcNow.AddMinutes(-16).ToString('o')})
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

Check 'before fence comparison previews without adoption' {
 function InvokePinned($relative,$arguments){Equal $arguments[1] 'CheckBusiness';return @{current_snapshot=@{};comparison=@{compatible=$true}}}
 $record=NewRecord;AssertDbState $record;Equal $record.EffectiveBaselineArtifact $null
}
Check 'migration cannot occur without fixed fenced anchor' {
 $record=NewRecord;$record.MigrationArtifact=@{path='PUBLIC'}
 MustThrow {AssertDbState $record} 'MIGRATION_WITHOUT_FENCED_BASELINE'
}
Check 'second adoption is refused before helper call' {
 function AssertKnowledgeFence($record){}
 function InvokePinned {throw 'UNEXPECTED_SECOND_ADOPTION'}
 $record=NewRecord;$record.FencedAdoptionAttempted=$true
 MustThrow {AdoptFencedBaseline $record} 'FENCED_ADOPTION_ALREADY_ATTEMPTED'
}
Check 'migration exception releases knowledge fence' {
 function AcquireKnowledgeFence($record){$record.KnowledgeFence=@{released=$false}}
 function AssertKnowledgeFence($record){}
 function AdoptFencedBaseline($record){throw 'PUBLIC_BUSINESS_DRIFT'}
 function ReleaseKnowledgeFence($record){$record.KnowledgeFence.released=$true}
 $record=NewRecord
 MustThrow {InvokeFencedMigration $record} 'PUBLIC_BUSINESS_DRIFT'
 Equal $record.KnowledgeFence.released $true
}
Check 'cooperative budget refusal releases knowledge fence' {
 function AcquireKnowledgeFence($record){$record.KnowledgeFence=@{released=$false}}
 function AssertKnowledgeFence($record){throw 'KNOWLEDGE_FENCE_COOPERATIVE_BUDGET_EXPIRED'}
 function AdoptFencedBaseline {throw 'UNEXPECTED_ADOPTION'}
 function ReleaseKnowledgeFence($record){$record.KnowledgeFence.released=$true}
 $record=NewRecord
 MustThrow {InvokeFencedMigration $record} 'KNOWLEDGE_FENCE_COOPERATIVE_BUDGET_EXPIRED'
 Equal $record.KnowledgeFence.released $true
}
Check 'existing Librarian refuses before Gateway stop' {
 function AcquireLibrarianBarrier($record){throw 'PUBLIC_LIBRARIAN_BUSY'}
 function StopExact {throw 'UNEXPECTED_STOP'}
 MustThrow {InvokeReleaseTransition (NewRecord)} 'PUBLIC_LIBRARIAN_BUSY'
}
Check 'stop failure releases producer barrier without acquiring byte lock' {
 function AcquireLibrarianBarrier($record){$record.LibrarianBarrier=@{released=$false}}
 function AssertLibrarianBarrier($record){}
 function StopExact {throw 'PUBLIC_STOP_HELD'}
 function InvokeFencedMigration {throw 'UNEXPECTED_BYTE_LOCK'}
 function ReleaseLibrarianBarrier($record){$record.LibrarianBarrier.released=$true}
 $record=NewRecord;MustThrow {InvokeReleaseTransition $record} 'PUBLIC_STOP_HELD'
 Equal $record.LibrarianBarrier.released $true
}
Check 'Scheduler and start occur only after knowledge release; producer barrier persists' {
 function AcquireLibrarianBarrier($record){$record.LibrarianBarrier=@{released=$false};$script:order=@('barrier')}
 function AssertLibrarianBarrier($record){Equal $record.LibrarianBarrier.released $false}
 function StopExact($record,$role){Equal $record.KnowledgeFence $null;$script:order+=@('stop')}
 function AssertReadyForStart($record,$role){}
 function InvokeFencedMigration($record){$record.KnowledgeFence=@{released=$true};$script:order+=@('migration-unlocked')}
 function UpdateTask($record){Equal $record.KnowledgeFence.released $true;Equal $record.LibrarianBarrier.released $false;$script:order+=@('update')}
 function StartExact($record,$role){Equal $record.KnowledgeFence.released $true;Equal $record.LibrarianBarrier.released $false;$script:order+=@('start')}
 function AssertMaintenance{};function AssertAssets{};function AssertProtected{};function AssertKnownFiles{};function AssertBase($record){};function AssertIdle($record){}
 function AssertOwned($record,$role){return @{}}
 function AssertAdminImage($snapshot){return @{}}
 function ReleaseLibrarianBarrier($record){$record.LibrarianBarrier.released=$true;$script:order+=@('release')}
 $record=NewRecord;InvokeReleaseTransition $record
 Equal ($script:order -join ',') 'barrier,stop,migration-unlocked,update,start,release'
}
Check 'failure after release or start never retries; producer barrier finally releases' {
 function AcquireLibrarianBarrier($record){$record.LibrarianBarrier=@{released=$false}}
 function AssertLibrarianBarrier($record){}
 function StopExact($record,$role){}
 function AssertReadyForStart($record,$role){}
 function InvokeFencedMigration($record){$record.KnowledgeFence=@{released=$true}}
 function UpdateTask($record){throw 'PUBLIC_UPDATE_FAILED'}
 function StartExact {throw 'UNEXPECTED_RUN'}
 function ReleaseLibrarianBarrier($record){$record.LibrarianBarrier.released=$true}
 $record=NewRecord;MustThrow {InvokeReleaseTransition $record} 'PUBLIC_UPDATE_FAILED'
 Equal $record.LibrarianBarrier.released $true
}
Check 'effective anchor contains exactly the adopted complete snapshot' {
 function AssertKnowledgeFence($record){}
 function InvokePinned($relative,$arguments){
  Equal $arguments[1] 'AdoptFencedMetrics'
  Equal $arguments[-1] 'public-token'
  return @{status='FENCED_OPERATIONAL_METRICS_ADOPTED';fence_token='public-token';reference='public-ref';comparison=@{compatible=$true};
   source_baseline=@{aggregate_sha256='old-complete'};observed_snapshot=@{aggregate_sha256='adopted-complete'};adopted_baseline=@{aggregate_sha256='adopted-complete'}}
 }
 function SaveNewArtifact($name,$value){$script:artifactValues[$name]=$value;return @{path=$name;sha256='public-fixed-hash'}}
 $intent=@{fenced_adoption_reference='public-ref'};$record=NewRecord;$record.KnowledgeFence=@{token='public-token'}
 $script:artifactValues=@{};AdoptFencedBaseline $record
 Equal $script:artifactValues['database-fenced-baseline.json'].aggregate_sha256 'adopted-complete'
 Equal $script:artifactValues['fenced-adoption.json'].source_baseline.aggregate_sha256 'old-complete'
 Equal $record.EffectiveBaselineArtifact.sha256 'public-fixed-hash';Equal $record.FencedAdoptionAttempted $true
}
Check 'saved anchor hash comes from validated buffer, refusing path substitution' {
 $packet=Join-Path ([IO.Path]::GetTempPath()) ('localbrain-artifact-public-'+[guid]::NewGuid().ToString('N'))
 $null=New-Item -ItemType Directory -Path $packet
 function FileHash($path){return 'public-substitution'}
 try{MustThrow {SaveNewArtifact 'public.json' @{public='validated'}} 'SAVED_ARTIFACT_BYTES_DRIFT'}
 finally{Remove-Item -LiteralPath (Join-Path $packet 'public.json');Remove-Item -LiteralPath $packet}
}
Check 'same-buffer fixed JSON refuses changed manifest before helper execution' {
 $packet=Join-Path ([IO.Path]::GetTempPath()) ('localbrain-manifest-public-'+[guid]::NewGuid().ToString('N'))
 $null=New-Item -ItemType Directory -Path (Join-Path $packet 'payload')
 $manifest=Join-Path $packet 'release-manifest.json';$helper=Join-Path $packet 'payload/public.py'
 [IO.File]::WriteAllText($manifest,'{"source_commit":"PUBLIC","files":[]}')
 [IO.File]::WriteAllText($helper,'raise AssertionError("must never execute")')
 $intent=@{manifest_sha256=('0'*64);source_commit='PUBLIC';python='PUBLIC_MUST_NEVER_START'}
 function AssertAssets {}
 try{MustThrow {InvokePinned 'public.py' @('--mode','Describe')} 'MANIFEST_BYTES_NOT_VERIFIED'}
 finally{Remove-Item -LiteralPath $helper;Remove-Item -LiteralPath (Split-Path $helper -Parent);Remove-Item -LiteralPath $manifest;Remove-Item -LiteralPath $packet}
}
Check 'same-buffer fixed JSON returns exactly hashed content' {
 $p=Join-Path ([IO.Path]::GetTempPath()) ('localbrain-json-public-'+[guid]::NewGuid().ToString('N')+'.json')
 $bytes=[Text.Encoding]::UTF8.GetBytes('{"public":"validated"}');[IO.File]::WriteAllBytes($p,$bytes)
 $expected=[Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($bytes)).ToLowerInvariant()
 try{
  Equal (ReadFixedJson $p $expected 'PUBLIC_DRIFT').public 'validated'
  [IO.File]::WriteAllText($p,'{"public":"changed"}')
  MustThrow {ReadFixedJson $p $expected 'PUBLIC_DRIFT'} 'PUBLIC_DRIFT'
 }finally{Remove-Item -LiteralPath $p}
}
[ordered]@{status='PASS';checks=$script:passed;cases=$script:results;script_sha256=FileHash $scriptPath;production_operations=0;database_reads=0;network_calls=0;model_requests=0}|ConvertTo-Json -Depth 5
