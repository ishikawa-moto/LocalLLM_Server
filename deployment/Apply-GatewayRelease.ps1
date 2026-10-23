#Requires -Version 7.2
param([ValidateSet('Describe','Inspect','Apply')][string]$Mode='Describe',[string]$Packet='',[string]$ApprovedIntentSha256='',[string]$ParentGoSha256='')
$ErrorActionPreference='Stop'
if($Mode -eq 'Describe'){@{mode='Describe';task='LocalBrain Control Gateway';stop_limit=1;start_limit=1;parent_apply_go_required=$true;automatic_restore=$false;model_requests=0}|ConvertTo-Json;exit 0}
$packet=[IO.Path]::GetFullPath($Packet)
$intentPath=Join-Path $packet 'intent.json'
$receiptPath=Join-Path $packet 'execution.json'
function FileHash([string]$path){if(Test-Path -LiteralPath $path -PathType Leaf){return (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()};return $null}
function StringHash([string]$value){return [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData([Text.Encoding]::UTF8.GetBytes($value))).ToLowerInvariant()}
function SafePath([string]$path,[string]$base){
 $full=[IO.Path]::GetFullPath($path);$base=[IO.Path]::GetFullPath($base).TrimEnd('\')
 if(-not $full.StartsWith($base+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'PATH_OUTSIDE_FIXED_ROOT'}
 $part=$full
 while($part){
  if(Test-Path -LiteralPath $part){if((Get-Item -LiteralPath $part -Force).Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'REPARSE_REFUSED'}}
  $next=[IO.Path]::GetDirectoryName($part);if($part -eq $next){break};$part=$next
 }
 return $full
}
function GetPort([string]$role){
 $site=Get-Content -LiteralPath (Join-Path $root 'config\private-site.json') -Raw|ConvertFrom-Json
 return [int]$site.($role+'_port')
}
function Listeners([string]$role){return @(Get-NetTCPConnection -State Listen -LocalPort (GetPort $role) -ErrorAction SilentlyContinue|Select-Object -ExpandProperty OwningProcess -Unique)}
function ProcessBirth([int]$processId){
 $value=@(Get-CimInstance -Query "SELECT CreationDate FROM Win32_Process WHERE ProcessId=$processId" -ErrorAction Stop)
 if($value.Count -ne 1 -or $value[0].CreationDate -isnot [DateTime]){throw 'PROCESS_BIRTH_UNAVAILABLE'}
 return $value[0].CreationDate.ToFileTimeUtc().ToString([Globalization.CultureInfo]::InvariantCulture)
}
function GetTask([string]$role){
 $binding=@($intent.tasks|Where-Object role -eq $role)
 if($binding.Count -ne 1){throw 'TASK_OUTSIDE_APPROVED_PAIR'}
 $service=New-Object -ComObject Schedule.Service;$service.Connect()
 $task=$service.GetFolder('\').GetTask($binding[0].task)
 $actions=@($task.Definition.Actions)
 if(-not $task.Enabled -or $actions.Count -ne 1 -or $actions[0].Path -ne $intent.python -or
  (StringHash $actions[0].Arguments) -ne $script:taskArgsHash -or
  (StringHash $task.Xml) -ne $script:taskXmlHash -or
  $task.Definition.Principal.UserId -notin @('SYSTEM','S-1-5-18')){throw 'TASK_DEFINITION_DRIFT'}
 return $task
}
function Snapshot([string]$role){
 $task=GetTask $role
 $instances=@($task.GetInstances(0)|ForEach-Object {
  $engine=[int]$_.EnginePID
  @{instance_guid=([guid]$_.InstanceGuid).ToString();pid=$engine;process_birth_filetime=if($engine -gt 0){ProcessBirth $engine}else{$null}}
 })
 return @{role=$role;state=[int]$task.State;instances=$instances;listener_pids=@(Listeners $role);observed_utc=[DateTime]::UtcNow.ToString('o')}
}
function SameIdentity($a,$b){
 return ($null -ne $a -and $null -ne $b -and $a.instance_guid -and $a.pid -gt 0 -and $a.process_birth_filetime -and
  $a.instance_guid -eq $b.instance_guid -and $a.pid -eq $b.pid -and $a.process_birth_filetime -eq $b.process_birth_filetime)
}

$null=SafePath $intentPath $packet
$intentBytes=[IO.File]::ReadAllBytes($intentPath)
$intentHash=[Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($intentBytes)).ToLowerInvariant()
if($ApprovedIntentSha256 -notmatch '^[a-f0-9]{64}$' -or $intentHash -ne $ApprovedIntentSha256){throw 'INTENT_MISMATCH'}
$intent=[Text.Encoding]::UTF8.GetString($intentBytes)|ConvertFrom-Json
$root=$intent.data_root
if($intent.schema -ne 'localbrain.gateway-release-intent.v1' -or $intent.packet -ne $packet -or (FileHash $PSCommandPath) -ne $intent.script_sha256 -or
 $intent.program_root -ne 'C:\ProgramData\LocalBrainServer' -or $intent.automatic_restore -cne $false -or $intent.database_policy -ne 'index-search-counters-v1' -or $intent.maintenance_policy -ne 'fenced-operational-metrics-v1' -or
 $intent.fenced_adoption_reference -cne ('fenced-operational-metrics-v1:'+$intent.source_commit) -or
 @($intent.tasks).Count -ne 1 -or $intent.tasks[0].task -ne 'LocalBrain Control Gateway'){throw 'FIXED_SCOPE_DRIFT'}
$script:taskXmlHash=$intent.tasks[0].definition_xml_sha256
$script:taskArgsHash=$intent.tasks[0].arguments_sha256
function AssertMaintenance {
 if((FileHash $intentPath) -ne $intentHash){throw 'INTENT_DRIFT'}
 if($Mode -ne 'Apply'){return}
 $path=SafePath (Join-Path $packet 'apply-go.json') $packet
 if($ParentGoSha256 -notmatch '^[a-f0-9]{64}$' -or (FileHash $path) -ne $ParentGoSha256){throw 'PARENT_APPLY_GO_REQUIRED'}
 $g=ReadFixedJson $path $ParentGoSha256 'PARENT_APPLY_GO_BYTES_DRIFT'
 if($g.phase -ne 'ClientMaintenanceConfirmedApplyGO' -or $g.intent_sha256 -ne $intentHash -or $g.script_sha256 -ne $intent.script_sha256 -or
 ($g.client_active_requests -isnot [int] -and $g.client_active_requests -isnot [long]) -or ($g.client_queued_requests -isnot [int] -and $g.client_queued_requests -isnot [long]) -or $g.client_active_requests -ne 0 -or $g.client_queued_requests -ne 0 -or $g.no_new_requests_until_finished -cne $true -or $g.parent_message_reference -notmatch '\S'){throw 'PARENT_MAINTENANCE_GATE_INVALID'}
 $date=if($g.created_utc -is [DateTime]){$g.created_utc.ToUniversalTime()}else{[DateTimeOffset]::Parse([string]$g.created_utc).UtcDateTime}
 if($date -gt [DateTime]::UtcNow -or ([DateTime]::UtcNow-$date).TotalMinutes -gt 15){throw 'PARENT_MAINTENANCE_GATE_EXPIRED'}
}
function ReadFixedJson([string]$path,[string]$expected,[string]$failureCode){
 $bytes=[IO.File]::ReadAllBytes($path)
 if([Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($bytes)).ToLowerInvariant() -ne $expected){throw $failureCode}
 return ([Text.Encoding]::UTF8.GetString($bytes)|ConvertFrom-Json -AsHashtable)
}
function GetFixedManifest {
 $path=SafePath (Join-Path $packet 'release-manifest.json') $packet
 $value=ReadFixedJson $path $intent.manifest_sha256 'MANIFEST_BYTES_NOT_VERIFIED'
 if($value.source_commit -cne $intent.source_commit){throw 'RELEASE_VERSION_DRIFT'}
 return $value
}
function AssertAssets {
 if((FileHash $intent.python) -ne $intent.python_sha256 -or (FileHash (Join-Path $packet 'release-manifest.json')) -ne $intent.manifest_sha256){throw 'RUNTIME_OR_MANIFEST_DRIFT'}
 foreach($b in $intent.dependency_files){
  $p=SafePath $b.path (Split-Path $b.path -Parent)
  if((FileHash $p) -ne $b.sha256){throw 'CONFIGURED_DEPENDENCY_DRIFT'}
 }
 $m=GetFixedManifest
 if($m.source_commit -ne $intent.source_commit){throw 'RELEASE_VERSION_DRIFT'}
 foreach($b in $m.files){if((FileHash (SafePath (Join-Path $packet ('payload\'+$b.path)) $packet)) -ne $b.sha256){throw 'STAGED_PAYLOAD_DRIFT'}}
 if((FileHash (Join-Path $packet 'database-before.json')) -ne $intent.database_before_sha256){throw 'DB_BASELINE_FILE_DRIFT'}
}
function AssertProtected {
 foreach($b in $intent.protected_files){
  $p=SafePath (Join-Path $root $b.path) $root
  if((FileHash $p) -ne $b.sha256){throw 'PROTECTED_CONFIG_OR_SOURCE_DRIFT'}
  if($b.acl_sha256 -and (StringHash (Get-Acl -LiteralPath $p).Sddl) -ne $b.acl_sha256){throw 'PROTECTED_ACL_DRIFT'}
 }
 foreach($b in $intent.database_files){
  $p=SafePath (Join-Path $root $b.path) $root
  $item=Get-Item -LiteralPath $p
  if((StringHash (Get-Acl -LiteralPath $p).Sddl) -ne $b.acl_sha256 -or $item.CreationTimeUtc.ToFileTimeUtc().ToString() -ne $b.creation_filetime){throw 'DATABASE_FILE_OWNERSHIP_OR_IDENTITY_DRIFT'}
 }
}
function AssertKnownFiles {
 $head=@(& git --no-optional-locks -c ('safe.directory='+$root.Replace('\','/')) -C $root rev-parse HEAD)
 if($LASTEXITCODE -or $head.Count -ne 1 -or $head[0] -ne $intent.production_head -or (FileHash (Join-Path $root '.git\index')) -ne $intent.production_index_sha256){throw 'HEAD_OR_INDEX_DRIFT'}
 $status=@(& git --no-optional-locks -c ('safe.directory='+$root.Replace('\','/')) -C $root status --porcelain --untracked-files=all)
 if($LASTEXITCODE -or (@($status|Sort-Object)-join [char]10) -cne (@($intent.known_git_status|Sort-Object)-join [char]10)){throw 'UNKNOWN_GIT_DIFF'}
}
function AssertBase($record){
 foreach($b in $record.BaseListeners){
  $pids=@(Listeners $b.role)
  if($pids.Count -ne @($b.processes).Count){throw 'PROTECTED_LISTENER_COUNT_DRIFT'}
  foreach($p in @($b.processes)){if($p.pid -notin $pids -or (ProcessBirth $p.pid) -ne $p.birth_filetime){throw 'PROTECTED_PROCESS_IDENTITY_DRIFT'}}
 }
}
function InvokePinned([string]$relative,[string[]]$arguments){
 AssertAssets
 $p=SafePath (Join-Path $packet ('payload\'+$relative)) $packet
 $bytes=[IO.File]::ReadAllBytes($p)
 $m=GetFixedManifest
 $b=@($m.files|Where-Object path -eq $relative)
 if($b.Count -ne 1 -or [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($bytes)).ToLowerInvariant() -ne $b[0].sha256){throw 'HELPER_BYTES_NOT_VERIFIED'}
 $raw=& $intent.python -I -B -c ([Text.Encoding]::UTF8.GetString($bytes)) @arguments
 if($LASTEXITCODE){throw 'PINNED_HELPER_CHECK_FAILED'}
 return (($raw -join [char]10)|ConvertFrom-Json -AsHashtable)
}
function AssertDbState($record){
 if($record.EffectiveBaselineArtifact){
  $anchor=$record.EffectiveBaselineArtifact
  $arguments=@('--mode','Check','--root',$root,'--before',$anchor.path,'--before-sha256',$anchor.sha256)
 }else{
  if($record.MigrationArtifact){throw 'MIGRATION_WITHOUT_FENCED_BASELINE'}
  # Preview retains old/new full evidence and never changes the original baseline.
  $arguments=@('--mode','CheckBusiness','--root',$root,'--before',(Join-Path $packet 'database-before.json'),'--before-sha256',$intent.database_before_sha256)
 }
 if($record.MigrationArtifact){$arguments+=@('--migration',$record.MigrationArtifact.path,'--migration-sha256',$record.MigrationArtifact.sha256)}
 $observed=InvokePinned 'deployment/release_db.py' $arguments
 $record.LastDatabaseCheck=$observed.current_snapshot
 $record.LastDatabaseComparison=$observed.comparison
 $record.DatabaseChecks=@($record.DatabaseChecks)+@($observed)
 if($observed.comparison.compatible -cne $true){throw 'DATABASE_POLICY_COMPARISON_HELD_NO_RESTORE'}
}
function SaveNewArtifact([string]$name,$value){
 $p=SafePath (Join-Path $packet $name) $packet
 $bytes=[Text.UTF8Encoding]::new($false).GetBytes(($value|ConvertTo-Json -Depth 24)+[char]10)
 $f=[IO.FileStream]::new($p,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
 try{$f.Write($bytes);$f.Flush($true)}finally{$f.Dispose()}
 $expected=[Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($bytes)).ToLowerInvariant()
 if((FileHash $p) -ne $expected){throw 'SAVED_ARTIFACT_BYTES_DRIFT'}
 return @{path=$p;sha256=$expected}
}
function AdoptFencedBaseline($record){
 AssertKnowledgeFence $record
 if($record.FencedAdoptionAttempted -or $record.EffectiveBaselineArtifact){throw 'FENCED_ADOPTION_ALREADY_ATTEMPTED'}
 $record.FencedAdoptionAttempted=$true;$record.Phase='fenced-baseline-adoption-requested';SaveReceipt $record
 $adoption=InvokePinned 'deployment/release_db.py' @('--mode','AdoptFencedMetrics','--root',$root,
  '--before',(Join-Path $packet 'database-before.json'),'--before-sha256',$intent.database_before_sha256,
  '--adoption-reference',$intent.fenced_adoption_reference,'--fence-token',$record.KnowledgeFence.token)
 AssertKnowledgeFence $record
 if($adoption.status -ne 'FENCED_OPERATIONAL_METRICS_ADOPTED' -or $adoption.fence_token -cne $record.KnowledgeFence.token -or
  $adoption.reference -cne $intent.fenced_adoption_reference -or $adoption.comparison.compatible -cne $true){throw 'FENCED_ADOPTION_NOT_VERIFIED'}
 $record.FencedAdoptionArtifact=SaveNewArtifact 'fenced-adoption.json' $adoption
 $record.EffectiveBaselineArtifact=SaveNewArtifact 'database-fenced-baseline.json' $adoption.adopted_baseline
 $record.Phase='single-fenced-baseline-adopted';SaveReceipt $record
 AssertKnowledgeFence $record
}
function InvokeFencedMigration($record){
 AcquireKnowledgeFence $record
 try{
  AssertKnowledgeFence $record
  AdoptFencedBaseline $record
  AssertKnowledgeFence $record
  $record.Phase='additive-migration-requested';SaveReceipt $record
  $anchor=$record.EffectiveBaselineArtifact
  $migration=InvokePinned 'deployment/release_db.py' @('--mode','Migrate','--root',$root,'--before',$anchor.path,'--before-sha256',$anchor.sha256)
  if($migration.status -ne 'ADDITIVE_MIGRATION_VERIFIED' -or $migration.existing_rows_unchanged -cne $true){throw 'MIGRATION_NOT_VERIFIED'}
  $record.Migration=$migration;$record.MigrationArtifact=SaveMigrationArtifact $migration
  AssertKnowledgeFence $record
  $record.Phase='post-migration-classification';AssertDbState $record
  $record.DatabaseAfterMigration=$record.LastDatabaseCheck
  AssertKnowledgeFence $record
  $record.Phase='migration-committed-existing-rows-preserved';SaveReceipt $record
 }finally{ReleaseKnowledgeFence $record}
}
function InvokeReleaseTransition($record){
 AcquireLibrarianBarrier $record
 try{
  # Existing producer barrier covers already dispatched Librarian children as well.
  # Stop/Ready and all Scheduler COM operations stay outside the short byte-lock section.
  AssertLibrarianBarrier $record
  StopExact $record 'gateway'
  AssertReadyForStart $record 'gateway'
  InvokeFencedMigration $record
  AssertLibrarianBarrier $record
  if(-not $record.KnowledgeFence.released){throw 'KNOWLEDGE_FENCE_NOT_RELEASED_BEFORE_SCHEDULER'}
  SaveReceipt $record
  UpdateTask $record
  StartExact $record 'gateway'
  AssertMaintenance;AssertAssets;AssertProtected;AssertKnownFiles;AssertBase $record;AssertIdle $record
  $record.FinalGateway=AssertOwned $record 'gateway';$record.AdminAfterImage=AssertAdminImage $record.FinalGateway
  AssertLibrarianBarrier $record
 }finally{ReleaseLibrarianBarrier $record}
 if(-not $record.LibrarianBarrier.released){throw 'LIBRARIAN_BARRIER_RELEASE_UNCONFIRMED'}
}
function SaveMigrationArtifact($migration){return (SaveNewArtifact 'migration-result.json' $migration)}
function PublicFailureCode($failure){
 $message=$failure.Exception.Message
 if($message -cmatch '^[A-Z][A-Z_]{2,100}$'){return $message}
 return 'PRIVATE_ERROR_MESSAGE_SUPPRESSED'
}
function AssertIdle($record){
 AssertMaintenance
 $status=InvokePinned 'deployment/probe_status.py' @('--root',$root)
 if($status.safe_idle -cne $true -or $status.brain_healthy -cne $true -or $status.proxy_listening -cne $true){throw 'GATEWAY_BUSY_OR_UNKNOWN'}
 if($record.TaskUpdated -and $status.release_commit -ne $intent.source_commit){throw 'LOADED_RELEASE_VERSION_UNKNOWN'}
 AssertDbState $record
 $record.LastFreshIdle=$status
}
function AssertBaseServices {
 $b=InvokePinned 'deployment/probe_status.py' @('--root',$root,'--base-only')
 if($b.brain_healthy -cne $true -or $b.proxy_listening -cne $true){throw 'BASE_SERVICES_UNHEALTHY_NO_AUTOSTART'}
}
function AssertAdminImage($snapshot){
 $i=$snapshot.instances[0];$task=GetTask 'gateway';$a=@($task.Definition.Actions)[0]
 $p=Get-CimInstance -Query ('SELECT ExecutablePath,CommandLine,ParentProcessId FROM Win32_Process WHERE ProcessId='+$i.pid)
 if($p.ExecutablePath -ne $intent.python -or $p.CommandLine -cne ('"'+$a.Path+'" '+$a.Arguments)){throw 'ADMIN_PROCESS_IMAGE_OR_COMMAND_MISMATCH'}
 $parent=Get-CimInstance -Query ('SELECT Name,CommandLine FROM Win32_Process WHERE ProcessId='+$p.ParentProcessId)
 if($parent.Name -ne 'svchost.exe' -or $parent.CommandLine -notmatch '-s Schedule'){throw 'ADMIN_PARENT_NOT_SCHEDULER'}
 return @{pid=$i.pid;birth_filetime=$i.process_birth_filetime;image_sha256=FileHash $p.ExecutablePath;command_sha256=StringHash $p.CommandLine;parent_pid=$p.ParentProcessId}
}
function InstallRelease($record){
 AssertMaintenance;AssertAssets;AssertProtected;AssertKnownFiles;AssertBase $record
 $program=$intent.program_root
 if(Test-Path -LiteralPath $program){throw 'EXISTING_PROGRAM_ROOT_HOLD'}
 $null=SafePath (Join-Path $program 'gateway-current.json') 'C:\ProgramData'
 $record.Phase='create-program-root-requested';SaveReceipt $record
 $null=New-Item -ItemType Directory -Path $program
 $release=Join-Path $program ('releases\'+$intent.source_commit)
 $null=New-Item -ItemType Directory -Path $release -Force
 $m=GetFixedManifest
 foreach($b in $m.files){
  $dest=SafePath (Join-Path $release $b.path) $release
  $null=New-Item -ItemType Directory -Path (Split-Path $dest -Parent) -Force
  Copy-Item -LiteralPath (Join-Path $packet ('payload\'+$b.path)) -Destination $dest
  if((FileHash $dest) -ne $b.sha256){throw 'INSTALLED_RELEASE_BYTES_MISMATCH'}
 }
 Copy-Item -LiteralPath (Join-Path $packet 'release-manifest.json') -Destination (Join-Path $release 'release-manifest.json')
 Copy-Item -LiteralPath (Join-Path $release 'deployment\gateway_entry.py') -Destination (Join-Path $program 'gateway_entry.py')
 $pointer=@{schema='localbrain.gateway-release-pointer.v1';source_commit=$intent.source_commit;release_root=$release;data_root=$root;manifest_sha256=$intent.manifest_sha256}
 [IO.File]::WriteAllText((Join-Path $program 'gateway-current.json'),($pointer|ConvertTo-Json),[Text.UTF8Encoding]::new($false))
 $admin=[Security.Principal.SecurityIdentifier]::new('S-1-5-32-544')
 $system=[Security.Principal.SecurityIdentifier]::new('S-1-5-18')
 $users=[Security.Principal.SecurityIdentifier]::new('S-1-5-32-545')
 foreach($item in @((Get-Item -LiteralPath $program))+@(Get-ChildItem -LiteralPath $program -Recurse -Force)){
  $null=SafePath $item.FullName 'C:\ProgramData'
  if($item.PSIsContainer){$acl=[Security.AccessControl.DirectorySecurity]::new();$inherit=[Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit'}
  else{$acl=[Security.AccessControl.FileSecurity]::new();$inherit=[Security.AccessControl.InheritanceFlags]::None}
  $acl.SetOwner($admin);$acl.SetAccessRuleProtection($true,$false)
  foreach($sid in @($admin,$system)){$acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($sid,'FullControl',$inherit,'None','Allow'))}
  $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($users,'ReadAndExecute',$inherit,'None','Allow'))
  Set-Acl -LiteralPath $item.FullName -AclObject $acl
 }
 $record.ReleaseRoot=$release;$record.Phase='release-installed-not-active';SaveReceipt $record
 $entry=Join-Path $program 'gateway_entry.py'
 if((FileHash $entry) -ne (FileHash (Join-Path $packet 'payload\deployment\gateway_entry.py'))){throw 'BOOTSTRAP_DRIFT'}
 $raw=& $intent.python -I -B $entry --pointer (Join-Path $program 'gateway-current.json') --verify-only
 if($LASTEXITCODE){throw 'RELEASE_ENTRY_VERIFY_FAILED'}
}
function UpdateTask($record){
 AssertMaintenance;AssertAssets;AssertProtected;AssertKnownFiles;AssertBase $record;AssertReadyForStart $record 'gateway';AssertDbState $record
 $task=GetTask 'gateway';$d=$task.Definition;$security=$task.GetSecurityDescriptor(7)
 $arguments='-I -B "'+(Join-Path $intent.program_root 'gateway_entry.py')+'" --pointer "'+(Join-Path $intent.program_root 'gateway-current.json')+'"'
 $d.Actions.Item(1).Arguments=$arguments
 $planned=StringHash $d.XmlText
 $record.TaskUpdate=@{old_definition_sha256=$script:taskXmlHash;new_definition_sha256=$planned;new_arguments_sha256=StringHash $arguments;security_sha256=StringHash $security}
 $record.Phase='task-action-update-requested';SaveReceipt $record
 $service=New-Object -ComObject Schedule.Service;$service.Connect()
 $updated=$service.GetFolder('\').RegisterTaskDefinition('LocalBrain Control Gateway',$d,4,'SYSTEM',$null,5,$security)
 $script:taskXmlHash=$planned;$script:taskArgsHash=StringHash $arguments
 $verified=GetTask 'gateway'
 if((StringHash $verified.GetSecurityDescriptor(7)) -ne (StringHash $security)){throw 'TASK_SECURITY_CHANGED_HOLD'}
 $record.TaskUpdated=$true;$record.Phase='task-action-updated';SaveReceipt $record
}
function SaveReceipt($record){
 $record.UpdatedUtc=[DateTime]::UtcNow.ToString('o')
 $bytes=[Text.UTF8Encoding]::new($false).GetBytes(($record|ConvertTo-Json -Depth 20)+[char]10)
 $pending=SafePath ($receiptPath+'.pending') $packet
 $stream=[IO.FileStream]::new($pending,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
 try{$stream.Write($bytes,0,$bytes.Length);$stream.Flush($true)}finally{$stream.Dispose()}
 if(Test-Path -LiteralPath $receiptPath){[IO.File]::Replace($pending,$receiptPath,[NullString]::Value)}else{[IO.File]::Move($pending,$receiptPath)}
}
function AssertOwned($record,[string]$role){
 $snapshot=Snapshot $role
 if($snapshot.state -eq 3 -and $snapshot.instances.Count -eq 0 -and $snapshot.listener_pids.Count -eq 0){
  if(-not @($record.StoppedTasks|Where-Object role -eq $role).Count){throw 'UNEXPLAINED_STOPPED_TASK'}
  return $snapshot
 }
 if($snapshot.state -ne 4 -or $snapshot.instances.Count -ne 1 -or $snapshot.listener_pids.Count -gt 1){throw 'TASK_STATE_UNKNOWN'}
 $known=@($record.BeforeTasks|Where-Object role -eq $role|ForEach-Object {$_.instances[0]})+@($record.OwnedStarts|Where-Object role -eq $role)
 if(-not @($known|Where-Object {SameIdentity $_ $snapshot.instances[0]}).Count){throw 'TASK_INSTANCE_PID_BIRTH_DRIFT'}
 if($snapshot.listener_pids.Count -eq 1 -and $snapshot.listener_pids[0] -ne $snapshot.instances[0].pid){throw 'FOREIGN_LISTENER_HOLD'}
 return $snapshot
}
function StopExact($record,[string]$role){
 AssertMaintenance;AssertAssets;AssertProtected;AssertKnownFiles;AssertBase $record
 $before=AssertOwned $record $role
 if($before.state -eq 3){return}
 # Parent confirms all Client requesters fenced; fresh Server idle is also required.
 AssertIdle $record
 $record.Phase=$role+'-stop-requested';SaveReceipt $record
 $again=AssertOwned $record $role
 if($again.state -ne 4 -or -not (SameIdentity $before.instances[0] $again.instances[0])){throw 'TASK_CHANGED_BEFORE_STOP'}
 $task=GetTask $role;$live=@($task.GetInstances(0))
 if($live.Count -ne 1 -or ([guid]$live[0].InstanceGuid).ToString() -ne $again.instances[0].instance_guid -or
  [int]$live[0].EnginePID -ne $again.instances[0].pid -or (ProcessBirth ([int]$live[0].EnginePID)) -ne $again.instances[0].process_birth_filetime){throw 'TASK_CHANGED_AT_STOP'}
 $live[0].Stop()
 $record.StoppedTasks=@($record.StoppedTasks)+@(@{role=$role;instance=$again.instances[0];operation_mode=$Mode;requested_utc=[DateTime]::UtcNow.ToString('o')})
 SaveReceipt $record
 $deadline=[DateTime]::UtcNow.AddSeconds(30)
 do{
  Start-Sleep -Milliseconds 200
  $after=Snapshot $role
  if($after.state -eq 3 -and $after.instances.Count -eq 0 -and $after.listener_pids.Count -eq 0){$record.Phase=$role+'-ready-after-owned-stop';SaveReceipt $record;return}
  if($after.instances.Count -gt 1 -or ($after.instances.Count -eq 1 -and -not (SameIdentity $again.instances[0] $after.instances[0]))){throw 'REPLACEMENT_APPEARED_DURING_STOP'}
 }while([DateTime]::UtcNow -lt $deadline)
 throw 'TASK_STOP_UNCONFIRMED_NO_KILL'
}
function AssertReadyForStart($record,[string]$role){
 $snapshot=AssertOwned $record $role
 if($snapshot.state -ne 3 -or $snapshot.instances.Count -ne 0 -or $snapshot.listener_pids.Count -ne 0){throw 'TASK_NOT_READY_TO_START'}
}
function StartExact($record,[string]$role){
 AssertMaintenance;AssertAssets;AssertProtected;AssertKnownFiles;AssertBase $record
 AssertReadyForStart $record $role
 AssertDbState $record
 AssertBaseServices
 if(@($record.OwnedStarts|Where-Object {$_.role -eq $role -and $_.operation_mode -eq $Mode}).Count){throw 'ONE_START_PER_TASK_PER_MODE_NO_RETRY'}
 $entry=@{role=$role;operation_mode=$Mode;attempt_id=[guid]::NewGuid().ToString();requested_utc=[DateTime]::UtcNow.ToString('o');instance_guid=$null;pid=$null;process_birth_filetime=$null;health='pending';last_snapshot=$null}
 $record.OwnedStarts=@($record.OwnedStarts)+@($entry)
 $record.Phase=$role+'-start-requested';SaveReceipt $record
 $task=GetTask $role;$run=$task.Run($null)
 $entry.instance_guid=([guid]$run.InstanceGuid).ToString()
 # Scheduler identity is durably recorded before observing PID/birth or doing health requests.
 SaveReceipt $record
 $deadline=[DateTime]::UtcNow.AddSeconds(30);$previous=''
 do{
  $snapshot=Snapshot $role;$entry.last_snapshot=$snapshot
  $match=@($snapshot.instances|Where-Object instance_guid -eq $entry.instance_guid)
  if($snapshot.instances.Count -gt 1 -or ($snapshot.instances.Count -eq 1 -and $match.Count -ne 1)){throw 'UNKNOWN_START_INSTANCE'}
  if($match.Count -eq 1){
   if(($entry.pid -gt 0 -and $entry.pid -ne $match[0].pid) -or ($entry.process_birth_filetime -and $entry.process_birth_filetime -ne $match[0].process_birth_filetime)){throw 'START_IDENTITY_CHANGED_NO_REBIND'}
   $entry.pid=$match[0].pid;$entry.process_birth_filetime=$match[0].process_birth_filetime
  }
  if($snapshot.listener_pids.Count -gt 1 -or ($snapshot.listener_pids.Count -eq 1 -and ($match.Count -ne 1 -or $snapshot.listener_pids[0] -ne $entry.pid))){throw 'START_LISTENER_FOREIGN'}
  $record.Phase=$role+'-start-observed'
  $key=$snapshot.state.ToString()+'|'+($snapshot.instances|ConvertTo-Json -Compress)+'|'+($snapshot.listener_pids -join ',')
  if($key -ne $previous){SaveReceipt $record;$previous=$key}
  if($snapshot.state -eq 4 -and $match.Count -eq 1 -and $entry.pid -gt 0 -and $entry.process_birth_filetime -and $snapshot.listener_pids.Count -eq 1){
   $record.Phase=$role+'-health-check';SaveReceipt $record
   AssertIdle $record
   $confirmed=AssertOwned $record $role
   if($confirmed.state -ne 4 -or $confirmed.listener_pids.Count -ne 1 -or -not (SameIdentity $entry $confirmed.instances[0])){throw 'TASK_CHANGED_DURING_HEALTH'}
   $entry.health='passed';SaveReceipt $record;return
  }
  Start-Sleep -Milliseconds 250
 }while([DateTime]::UtcNow -lt $deadline)
 throw 'START_UNCONFIRMED_NO_RETRY'
}

AssertAssets;AssertProtected;AssertKnownFiles
$initial=Snapshot 'gateway'
if($initial.state -ne 4 -or $initial.instances.Count -ne 1 -or $initial.listener_pids.Count -ne 1 -or $initial.listener_pids[0] -ne $initial.instances[0].pid -or -not (SameIdentity $initial.instances[0] $intent.tasks[0].before)){throw 'BEFORE_GATEWAY_IDENTITY_DRIFT'}
if($Mode -eq 'Inspect'){@{status='INSPECTED_READONLY';gateway=$initial;admin_image_required_at_apply=$true;production_writes=0}|ConvertTo-Json -Depth 6;exit 0}
AssertMaintenance
$who=[Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if(-not $who.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){throw 'NATIVE_WINDOWS_UAC_REQUIRED'}
# Load exactly the manifested module bytes. Importing definitions acquires no locks.
$fencePath=SafePath (Join-Path $packet 'payload\deployment\release_fence.ps1') $packet
$fenceBytes=[IO.File]::ReadAllBytes($fencePath)
$fenceManifest=GetFixedManifest
$fenceBinding=@($fenceManifest.files|Where-Object path -eq 'deployment/release_fence.ps1')
if($fenceBinding.Count -ne 1 -or [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($fenceBytes)).ToLowerInvariant() -ne $fenceBinding[0].sha256){throw 'FENCE_MODULE_BYTES_NOT_VERIFIED'}
. ([scriptblock]::Create([Text.Encoding]::UTF8.GetString($fenceBytes)))
$mutex=[Threading.Mutex]::new($false,'Global\LocalBrain-GatewayRelease-20261023')
if(-not $mutex.WaitOne(0)){$mutex.Dispose();throw 'ANOTHER_RELEASE_WORKER_ACTIVE'}
$record=$null
try{
 if((Test-Path -LiteralPath $receiptPath) -or (Test-Path -LiteralPath ($receiptPath+'.pending'))){throw 'EXISTING_EXECUTION_HOLD_NO_RETRY'}
 $baseline=Get-Content -LiteralPath (Join-Path $packet 'database-before.json') -Raw|ConvertFrom-Json -AsHashtable
 $record=@{Schema='localbrain.gateway-release-execution.v1';Phase='preflight';IntentSha256=$intentHash;ScriptSha256=$intent.script_sha256;
 BeforeTasks=@($initial);BaseListeners=@($intent.base_listeners);DatabaseBefore=$baseline;DatabaseAfterMigration=$null;
 OwnedStarts=@();StoppedTasks=@();TaskUpdated=$false;DatabaseChecks=@();MigrationArtifact=$null;EffectiveBaselineArtifact=$null;FencedAdoptionAttempted=$false;LibrarianBarrier=$null;KnowledgeFence=$null;ExplicitModelRequests=0;AutomaticRestore=$false;ParentGoSha256=$ParentGoSha256}
 $record.Phase='preflight-admin-image';$record.AdminBeforeImage=AssertAdminImage $initial
 $record.Phase='preflight-protected-listeners';AssertBase $record
 $record.Phase='preflight-idle-and-database';AssertIdle $record;SaveReceipt $record
 InstallRelease $record
 InvokeReleaseTransition $record
 $record.Phase='SERVER_DEPLOYED_AWAITING_CLIENT_HOST_QUALIFICATION';SaveReceipt $record
 @{status=$record.Phase;receipt_sha256=FileHash $receiptPath;source_commit=$intent.source_commit;automatic_restore=$false;model_requests=0}|ConvertTo-Json
}catch{
 if($record){$record.FailureCode=PublicFailureCode $_;$record.FailedPhase=$record.Phase;$record.Phase='HOLD_PRESERVE_CURRENT_STATE_NO_AUTOMATIC_RESTORE';try{SaveReceipt $record}catch{}}
 throw 'RELEASE_APPLY_HELD_INSPECT_OWNED_RECEIPT_NO_RETRY'
}finally{$mutex.ReleaseMutex();$mutex.Dispose()}
