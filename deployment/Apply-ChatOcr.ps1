#Requires -Version 7.2
param([ValidateSet('Describe','Validate','Apply')][string]$Mode='Describe',
 [string]$Packet='', [string]$ApprovedIntentSha256='', [string]$ParentGoSha256='')
$ErrorActionPreference='Stop'
if($Mode -eq 'Describe'){
 @{mode='Describe';tasks=@('LocalBrain Chat Web','LocalBrain Control Gateway');stop_limit_each=1;start_limit_each=1;
   production_approval_required=$true;fresh_human_idle_required=$true;automatic_restore=$false;models_started_by_apply=0}|ConvertTo-Json;exit 0
}
function HashBytes([byte[]]$bytes){return [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($bytes)).ToLowerInvariant()}
function StringHash([string]$text){return HashBytes ([Text.Encoding]::UTF8.GetBytes($text))}
function SafePath([string]$path,[string]$base=''){
 $full=[IO.Path]::GetFullPath($path)
 if($base){$base=[IO.Path]::GetFullPath($base).TrimEnd('\');if($full -ne $base -and -not $full.StartsWith($base+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'PATH_OUTSIDE_FIXED_ROOT'}}
 $part=$full
 while($part){
  if(Test-Path -LiteralPath $part){if((Get-Item -LiteralPath $part -Force).Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'REPARSE_REFUSED'}}
  $next=[IO.Path]::GetDirectoryName($part);if($next -eq $part){break};$part=$next
 }
 return $full
}
function FileHash([string]$path){$null=SafePath $path;if(Test-Path -LiteralPath $path -PathType Leaf){return HashBytes ([IO.File]::ReadAllBytes($path))};return $null}
function ReadFixed([string]$path,[string]$expected){
 $null=SafePath $path;$bytes=[IO.File]::ReadAllBytes($path)
 if((HashBytes $bytes) -cne $expected){throw 'FIXED_BYTES_DRIFT'}
 return ([Text.Encoding]::UTF8.GetString($bytes)|ConvertFrom-Json -AsHashtable)
}
$packet=SafePath $Packet
$intentPath=Join-Path $packet 'intent.json'
if($ApprovedIntentSha256 -cnotmatch '^[a-f0-9]{64}$'){throw 'EXACT_INTENT_APPROVAL_REQUIRED'}
$intent=ReadFixed $intentPath $ApprovedIntentSha256
$root=SafePath $intent.data_root
if($intent.schema -cne 'localbrain.chat-ocr-apply-intent.v1' -or $intent.packet -cne $packet -or
 $intent.source_commit -cnotmatch '^[a-f0-9]{40}$' -or $intent.deployment_id -cnotmatch '^[a-z0-9-]{1,80}$' -or
 $intent.client_source -cnotmatch '^[a-f0-9]{40}$' -or $intent.client_manifest_sha256 -cnotmatch '^[a-f0-9]{64}$' -or
 (FileHash $PSCommandPath) -cne $intent.script_sha256 -or $intent.automatic_restore -cne $false -or
 @($intent.tasks).Count -ne 2 -or (@($intent.tasks.role|Sort-Object)-join ',') -cne 'chat,gateway' -or
 $intent.program_root -cne 'C:\ProgramData\LocalBrainServer' -or
 $intent.release_root -cne (Join-Path $intent.program_root ('releases\'+$intent.source_commit)) -or
 $intent.activation_root -cne (Join-Path $intent.program_root ('chat-ocr-activations\'+$intent.deployment_id)) -or
 $intent.entry_path -cne (Join-Path $intent.activation_root 'chat_ocr_entry.py') -or
 $intent.audit_root -cne (Join-Path $intent.program_root ('chat-ocr-attempt-'+$intent.deployment_id)) -or
 $intent.activation_go -cne (Join-Path $intent.activation_root 'activation-go.json')){throw 'FIXED_SCOPE_DRIFT'}
foreach($binding in $intent.tasks){
 $expected=if($binding.role -eq 'chat'){'LocalBrain Chat Web'}else{'LocalBrain Control Gateway'}
 if($binding.name -cne $expected){throw 'TASK_SCOPE_DRIFT'}
}
function Check([string]$checkMode){
 if((FileHash $intentPath) -cne $ApprovedIntentSha256 -or (FileHash $intent.python) -cne $intent.python_sha256){throw 'INTENT_OR_PYTHON_DRIFT'}
 $path=Join-Path $packet 'chat_ocr_checks.py';$bytes=[IO.File]::ReadAllBytes((SafePath $path $packet))
 if((HashBytes $bytes) -cne $intent.checks_sha256){throw 'CHECKS_DRIFT'}
 $argsList=@('--mode',$checkMode,'--packet',$packet,'--intent-sha256',$ApprovedIntentSha256)
 if($checkMode -eq 'Maintenance'){$argsList+=@('--go-sha256',$ParentGoSha256)}
 $raw=& $intent.python -I -B -c ([Text.Encoding]::UTF8.GetString($bytes)) @argsList
 if($LASTEXITCODE){throw 'PINNED_CHECK_HELD'}
 return (($raw -join [char]10)|ConvertFrom-Json -AsHashtable)
}
$assetCheck=Check 'Assets'
if($Mode -eq 'Validate'){$assetCheck|ConvertTo-Json -Depth 12;exit 0}
if(-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){
 throw 'NATIVE_UAC_REQUIRED_NO_SELF_ELEVATION'
}
if($ParentGoSha256 -cnotmatch '^[a-f0-9]{64}$'){throw 'FRESH_PARENT_GO_REQUIRED'}
$null=Check 'Maintenance'
foreach($path in @($intent.release_root,$intent.activation_root,(Join-Path $root 'config\chat-ocr.json'),(Join-Path $root 'runtime\chat-ocr'))){
 $null=SafePath $path
 if(Test-Path -LiteralPath $path){throw 'TARGET_ALREADY_EXISTS_FORWARD_REVIEW_REQUIRED'}
}
$service=New-Object -ComObject Schedule.Service;$service.Connect();$folder=$service.GetFolder('\')
$record=@{schema='localbrain.chat-ocr-apply-receipt.v1';intent_sha256=$ApprovedIntentSha256;source=$intent.source_commit;
 phase='reserved';started_utc=[DateTime]::UtcNow.ToString('o');activation_committed=$false;automatic_restore=$false;
 requests=@{chat=@{stop=0;start=0};gateway=@{stop=0;start=0}};events=@()}
$receiptPath=Join-Path $intent.audit_root 'execution.json'
$attempt=$null
$auditOwned=$false
function SaveReceipt {
 $temp=SafePath (Join-Path $intent.audit_root ('execution-'+[guid]::NewGuid().ToString()+'.pending.json')) $intent.audit_root
 $null=SafePath $receiptPath $intent.audit_root
 $bytes=[Text.UTF8Encoding]::new($false).GetBytes(($record|ConvertTo-Json -Depth 32)+[char]10)
 $stream=[IO.FileStream]::new($temp,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
 try{$stream.Write($bytes);$stream.Flush($true)}finally{$stream.Dispose()}
 [IO.File]::Move($temp,$receiptPath,$true)
}
function Step([string]$phase){$record.phase=$phase;$record.events+=@{phase=$phase;utc=[DateTime]::UtcNow.ToString('o')};SaveReceipt}
function Binding([string]$role){return @($intent.tasks|Where-Object role -CEQ $role)[0]}
function Task([string]$role){return $folder.GetTask((Binding $role).name)}
function Birth([int]$processId){
 $p=Get-CimInstance -Query "SELECT CreationDate FROM Win32_Process WHERE ProcessId=$processId"
 if($null -eq $p -or $p.CreationDate -isnot [DateTime]){throw 'PROCESS_BIRTH_UNKNOWN'}
 return $p.CreationDate.ToFileTimeUtc().ToString([Globalization.CultureInfo]::InvariantCulture)
}
function AssertDefinition([string]$role,[bool]$new){
 $task=Task $role;$binding=Binding $role;$d=$task.Definition;$actions=@($d.Actions)
 $suspended=$record.disabled_tasks.ContainsKey($role)
 $expectedXml=if($new){$record.new_tasks[$role].definition_sha256}elseif($suspended){$record.disabled_tasks[$role]}else{$binding.xml_sha256}
 $copy=$service.NewTask(0);$copy.XmlText=$task.Xml
 $actualXml=if($new -or $suspended){StringHash $copy.XmlText}else{StringHash $task.Xml}
 $expectedEnabled=if($new){$record.new_tasks[$role].enabled}else{-not $suspended}
 if($task.Enabled -cne $expectedEnabled -or $actions.Count -ne 1 -or $actions[0].Path -cne $intent.python -or
  $actualXml -cne $expectedXml -or (StringHash $task.GetSecurityDescriptor(7)) -cne $binding.sddl_sha256 -or
  $d.Principal.UserId -notin @('SYSTEM','S-1-5-18') -or [int]$d.Principal.LogonType -ne 5){throw 'TASK_DEFINITION_OR_SECURITY_DRIFT'}
 if($new -and ($actions[0].Arguments -cne $record.new_tasks[$role].arguments -or $actions[0].WorkingDirectory -cne $root)){throw 'TASK_ENTRY_DRIFT'}
 return $task
}
function SuspendTask([string]$role){
 $task=AssertDefinition $role $false
 # Only boot triggers (or none) are supported. Enabling a boot task during this
 # running boot cannot admit a calendar/logon/event-triggered unowned instance.
 foreach($trigger in @($task.Definition.Triggers)){if([int]$trigger.Type -ne 8){throw 'AUTOMATIC_TRIGGER_OUTSIDE_FIXED_CONTRACT'}}
 $copy=$service.NewTask(0);$copy.XmlText=$task.Xml;$copy.Settings.Enabled=$false
 $record.disabled_tasks[$role]=StringHash $copy.XmlText
 Step ($role+'-suspend-automatic-start-requested');$task.Enabled=$false
 $null=AssertDefinition $role $false
}
function Port([string]$role){
 $site=Get-Content -LiteralPath (Join-Path $root 'config\private-site.json') -Raw|ConvertFrom-Json -AsHashtable
 $config=Get-Content -LiteralPath (Join-Path $root ('config\'+$role+'.json')) -Raw|ConvertFrom-Json -AsHashtable
 $value=if($config.ContainsKey('port')){$config.port}else{$site[$role+'_port']}
 if(($value -isnot [int] -and $value -isnot [long]) -or $value -lt 1 -or $value -gt 65535){throw 'PORT_UNKNOWN'}
 return [int]$value
}
function ListenerPids([string]$role){
 $all=@(Get-NetTCPConnection -State Listen -ErrorAction Stop)
 return @($all|Where-Object LocalPort -EQ (Port $role)|Select-Object -ExpandProperty OwningProcess -Unique)
}
function AssertRunning([string]$role,[bool]$new){
 $task=AssertDefinition $role $new;$instances=@($task.GetInstances(0));$pids=@(ListenerPids $role)
 $allowedStates=if(-not $new -and $record.disabled_tasks.ContainsKey($role)){@(1,4)}else{@(4)}
 if([int]$task.State -notin $allowedStates -or $instances.Count -ne 1 -or $pids.Count -ne 1 -or [int]$instances[0].EnginePID -ne [int]$pids[0]){throw 'RUNNING_TASK_OR_LISTENER_UNKNOWN'}
 $processId=[int]$pids[0];$p=Get-CimInstance -Query "SELECT ExecutablePath,CommandLine,ParentProcessId FROM Win32_Process WHERE ProcessId=$processId"
 $parent=Get-CimInstance -Query ('SELECT Name,CommandLine FROM Win32_Process WHERE ProcessId='+$p.ParentProcessId)
 if($p.ExecutablePath -cne $intent.python -or (FileHash $p.ExecutablePath) -cne $intent.python_sha256 -or
  $p.CommandLine -cne ('"'+$intent.python+'" '+$task.Definition.Actions.Item(1).Arguments) -or
  $parent.Name -ne 'svchost.exe' -or $parent.CommandLine -notmatch '-s Schedule'){throw 'SCHEDULER_PROCESS_BINDING_UNKNOWN'}
 $observed=@{pid=$processId;birth_filetime=Birth $processId;instance_guid=([guid]$instances[0].InstanceGuid).ToString();command_sha256=StringHash $p.CommandLine}
 if(-not $new){
  $b=Binding $role
  if($observed.pid -ne $b.pid -or $observed.birth_filetime -cne $b.birth_filetime -or $observed.command_sha256 -cne $b.command_sha256 -or $observed.instance_guid -cne $b.instance_guid){throw 'PREVIOUS_INSTANCE_CHANGED'}
 }
 return $observed
}
function AssertProtected {
 $actual=@(Get-ChildItem -LiteralPath (Join-Path $root 'config') -Recurse -File -Force|ForEach-Object {
  $null=SafePath $_.FullName $root
  $relative=[IO.Path]::GetRelativePath($root,$_.FullName).Replace('\','/')
  if($relative -ne 'config/chat-ocr.json' -or -not $record.feature_config_created){$relative}
 }|Sort-Object)
 if(($actual -join [char]10) -cne (@($intent.config_inventory|Sort-Object)-join [char]10)){throw 'CONFIG_INVENTORY_DRIFT'}
 foreach($b in $intent.protected_files){
  $path=SafePath (Join-Path $root $b.path) $root
  if((FileHash $path) -cne $b.sha256 -or (StringHash (Get-Acl -LiteralPath $path).Sddl) -cne $b.acl_sha256){throw 'PROTECTED_FILE_DRIFT'}
 }
 foreach($b in $intent.program_files){if((FileHash (SafePath $b.path $intent.program_root)) -cne $b.sha256){throw 'EXISTING_GATEWAY_BOOTSTRAP_OR_POINTER_DRIFT'}}
}
function CaptureBase {
 $site=Get-Content -LiteralPath (Join-Path $root 'config\private-site.json') -Raw|ConvertFrom-Json -AsHashtable
 $result=@{}
 foreach($role in @('llm','brain','proxy','embedding')){
  $portNumber=$site[$role+'_port'];if($portNumber -isnot [long] -and $portNumber -isnot [int]){throw 'BASE_PORT_UNKNOWN'}
  $all=@(Get-NetTCPConnection -State Listen -ErrorAction Stop)
  $pids=@($all|Where-Object LocalPort -EQ $portNumber|Select-Object -ExpandProperty OwningProcess -Unique)
  if($role -in @('brain','proxy') -and $pids.Count -ne 1){throw 'BASE_LISTENER_UNKNOWN'}
  $result[$role]=@($pids|ForEach-Object{@{pid=[int]$_;birth_filetime=Birth ([int]$_)}})
 }
 return $result
}
function AssertBase {
 $current=CaptureBase
 foreach($role in @('llm','brain','proxy','embedding')){
  $a=@($current[$role]|ForEach-Object{[string]$_.pid+':'+$_.birth_filetime}|Sort-Object)
  $b=@($record.base_processes[$role]|ForEach-Object{[string]$_.pid+':'+$_.birth_filetime}|Sort-Object)
  if(($a -join ',') -cne ($b -join ',')){throw 'BASE_PROCESS_CHANGED_NO_AUTOSTART'}
 }
}
function StopOnce([string]$role){
 $null=Check 'Maintenance';AssertProtected;AssertBase
 $record.old_processes[$role]=AssertRunning $role $false
 if($record.requests[$role].stop -ne 0){throw 'STOP_LIMIT'}
 $record.requests[$role].stop=1;Step ($role+'-stop-requested');(Task $role).Stop(0)
 $clock=[Diagnostics.Stopwatch]::StartNew()
 do{
  $task=Task $role;$instances=@($task.GetInstances(0));$pids=@(ListenerPids $role)
  $old=Get-Process -Id $record.old_processes[$role].pid -ErrorAction SilentlyContinue
  if([int]$task.State -eq 1 -and $instances.Count -eq 0 -and $pids.Count -eq 0 -and $null -eq $old){return}
  Start-Sleep -Milliseconds 200
 }while($clock.Elapsed.TotalSeconds -lt 30)
 throw 'STOP_DID_NOT_SETTLE_NO_FORCE_OR_RETRY'
}
function ProtectNewTree([string]$path){
 $admin=[Security.Principal.SecurityIdentifier]::new('S-1-5-32-544');$system=[Security.Principal.SecurityIdentifier]::new('S-1-5-18')
 foreach($item in @((Get-Item -LiteralPath $path -Force))+@(Get-ChildItem -LiteralPath $path -Recurse -Force)){
  $null=SafePath $item.FullName $path
  if($item.PSIsContainer){$acl=[Security.AccessControl.DirectorySecurity]::new();$inherit=[Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit'}
  else{$acl=[Security.AccessControl.FileSecurity]::new();$inherit=[Security.AccessControl.InheritanceFlags]::None}
  $acl.SetOwner($admin);$acl.SetAccessRuleProtection($true,$false)
  foreach($sid in @($admin,$system)){$acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($sid,'FullControl',$inherit,'None','Allow'))}
  Set-Acl -LiteralPath $item.FullName -AclObject $acl
 }
}
function InstallRelease {
 $null=Check 'Maintenance';$null=Check 'Assets';AssertProtected;AssertBase
 if(Test-Path -LiteralPath $intent.release_root){throw 'RELEASE_TARGET_EXISTS'}
 $null=New-Item -ItemType Directory -Path $intent.release_root
 ProtectNewTree $intent.release_root
 $manifest=ReadFixed (Join-Path $packet 'release-manifest.json') $intent.manifest_sha256
 foreach($b in $manifest.files){
  $dest=SafePath (Join-Path $intent.release_root $b.path) $intent.release_root
  $null=New-Item -ItemType Directory -Path (Split-Path $dest -Parent) -Force
  Copy-Item -LiteralPath (Join-Path $packet ('payload\'+$b.path)) -Destination $dest
  if((FileHash $dest) -cne $b.sha256){throw 'INSTALLED_BYTES_DRIFT'}
 }
 Copy-Item -LiteralPath (Join-Path $packet 'release-manifest.json') -Destination (Join-Path $intent.release_root 'release-manifest.json')
 if(Test-Path -LiteralPath $intent.activation_root){throw 'ACTIVATION_TARGET_EXISTS'}
 $null=New-Item -ItemType Directory -Path $intent.activation_root
 ProtectNewTree $intent.activation_root
 foreach($name in @('chat_ocr_entry.py','startup-spec.json')){Copy-Item -LiteralPath (Join-Path $packet $name) -Destination (Join-Path $intent.activation_root $name)}
 ProtectNewTree $intent.release_root;ProtectNewTree $intent.activation_root
 if((FileHash $intent.entry_path) -cne $intent.entry_sha256 -or (FileHash (Join-Path $intent.activation_root 'startup-spec.json')) -cne $intent.spec_sha256){throw 'INSTALLED_STARTUP_DRIFT'}
 foreach($role in @('gateway','chat')){
  $raw=& $intent.python -I -B $intent.entry_path --role $role --spec (Join-Path $intent.activation_root 'startup-spec.json') --spec-sha256 $intent.spec_sha256 --verify-only
  if($LASTEXITCODE){throw 'INSTALLED_ENTRY_VERIFY_FAILED'}
 }
}
function UpdateTask([string]$role){
 $task=AssertDefinition $role $false
 if([int]$task.State -ne 1 -or @($task.GetInstances(0)).Count -ne 0){throw 'TASK_NOT_SETTLED'}
 $security=$task.GetSecurityDescriptor(7);$d=$service.NewTask(0);$d.XmlText=$task.Xml
 $d.RegistrationInfo.SecurityDescriptor=$security
 $d.Actions.Item(1).Path=$intent.python
 $arguments='-I -B "'+$intent.entry_path+'" --role '+$role+' --spec "'+(Join-Path $intent.activation_root 'startup-spec.json')+'" --spec-sha256 '+$intent.spec_sha256
 $d.Actions.Item(1).Arguments=$arguments;$d.Actions.Item(1).WorkingDirectory=$root
 $record.new_tasks[$role]=@{definition_sha256=StringHash $d.XmlText;arguments=$arguments;enabled=$false}
 Step ($role+'-task-update-requested')
 # UPDATE only, preserve principal ACEs, ignore registration triggers. Never create a task.
 $null=$folder.RegisterTaskDefinition((Binding $role).name,$d,52,'SYSTEM',$null,5,$security)
 $null=AssertDefinition $role $true
}
function EnableNewTask([string]$role){
 $task=AssertDefinition $role $true
 if($task.Enabled -or [int]$task.State -notin @(1,3) -or @($task.GetInstances(0)).Count -ne 0){throw 'NEW_TASK_NOT_SUSPENDED'}
 $copy=$service.NewTask(0);$copy.XmlText=$task.Xml;$copy.Settings.Enabled=$true
 $record.new_tasks[$role].definition_sha256=StringHash $copy.XmlText;$record.new_tasks[$role].enabled=$true
 Step ($role+'-enable-verified-new-entry-requested');$task.Enabled=$true
 $null=AssertDefinition $role $true
}
function StartOnce([string]$role){
 $null=Check 'Maintenance';AssertProtected;AssertBase
 $task=AssertDefinition $role $true
 if([int]$task.State -ne 3 -or @($task.GetInstances(0)).Count -ne 0 -or @(ListenerPids $role).Count -ne 0){throw 'START_TARGET_NOT_IDLE'}
 if($record.requests[$role].start -ne 0){throw 'START_LIMIT'}
 $record.requests[$role].start=1;Step ($role+'-start-requested');$null=$task.Run($null)
 $clock=[Diagnostics.Stopwatch]::StartNew()
 do{
  $task=Task $role;$instances=@($task.GetInstances(0));$pids=@(ListenerPids $role)
  if([int]$task.State -eq 4 -and $instances.Count -eq 1 -and $pids.Count -eq 1){
   $record.new_processes[$role]=AssertRunning $role $true;return
  }
  Start-Sleep -Milliseconds 200
 }while($clock.Elapsed.TotalSeconds -lt 30)
 throw 'HELD_START_UNCONFIRMED_NO_RETRY'
}
function AssertStorageSame($before,$after){
 if(($before.existing_db_identities|ConvertTo-Json -Compress) -cne ($after.existing_db_identities|ConvertTo-Json -Compress)){throw 'EXISTING_DATABASE_IDENTITY_CHANGED'}
 foreach($name in $before.databases.Keys){
  $a=$before.databases[$name];$b=$after.databases[$name]
  if($a.file_id -cne $b.file_id -or $a.catalog_sha256 -cne $b.catalog_sha256 -or
     ($a.files|ConvertTo-Json -Compress) -cne ($b.files|ConvertTo-Json -Compress)){throw 'EXISTING_HISTORY_OR_SCHEMA_CHANGED_NO_RESTORE'}
 }
}
try{
 $null=SafePath $intent.audit_root $intent.program_root
 if(Test-Path -LiteralPath $intent.audit_root){throw 'PRIOR_ATTEMPT_NO_RETRY'}
 $null=New-Item -ItemType Directory -Path $intent.audit_root
 ProtectNewTree $intent.audit_root
 $auditOwned=$true
 $attempt=[IO.FileStream]::new((Join-Path $intent.audit_root 'apply-attempt.lock'),[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
 SaveReceipt
 $record.old_processes=@{};$record.new_processes=@{};$record.new_tasks=@{};$record.disabled_tasks=@{}
 AssertProtected
 foreach($role in @('gateway','chat')){$record.old_processes[$role]=AssertRunning $role $false}
 $record.base_processes=CaptureBase
 $fencePath=Join-Path $packet 'payload\deployment\release_fence.ps1';$fenceBytes=[IO.File]::ReadAllBytes($fencePath)
 if((HashBytes $fenceBytes) -cne $intent.fence_sha256){throw 'FENCE_SOURCE_DRIFT'}
 . ([ScriptBlock]::Create([Text.Encoding]::UTF8.GetString($fenceBytes)))
 AcquireLibrarianBarrier $record
 $record.fresh_idle_1=Check 'OldProbe';Start-Sleep -Milliseconds 250;$record.fresh_idle_2=Check 'OldProbe'
 $null=Check 'Maintenance';AssertLibrarianBarrier $record
 SuspendTask 'chat';SuspendTask 'gateway'
 StopOnce 'chat';$record.last_fresh_idle=Check 'OldProbe';StopOnce 'gateway'
 Step 'both-old-processes-settled'
 $record.existing_storage_before=Check 'Storage';Step 'existing-storage-qualified'
 InstallRelease;Step 'immutable-release-installed-held'
 UpdateTask 'gateway';UpdateTask 'chat'
 AssertProtected;AssertBase;$null=Check 'Maintenance'
 $featurePath=SafePath (Join-Path $root 'config\chat-ocr.json') $root
 $featureBytes=[IO.File]::ReadAllBytes((Join-Path $packet 'chat-ocr.json'))
 if((HashBytes $featureBytes) -cne $intent.feature_sha256){throw 'FEATURE_BYTES_DRIFT'}
 $featureStream=[IO.FileStream]::new($featurePath,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
 try{$featureStream.Write($featureBytes);$featureStream.Flush($true)}finally{$featureStream.Dispose()}
 $record.feature_config_created=$true
 EnableNewTask 'gateway';EnableNewTask 'chat'
 StartOnce 'gateway';StartOnce 'chat'
 $record.held_endpoints=Check 'HeldProbe'
 foreach($role in @('gateway','chat')){
  $fresh=AssertRunning $role $true
  if($fresh.pid -ne $record.held_endpoints.processes[$role].pid -or $fresh.birth_filetime -cne $record.new_processes[$role].birth_filetime){throw 'HELD_ENDPOINT_PROCESS_DRIFT'}
 }
 $record.existing_storage_after=Check 'Storage';AssertStorageSame $record.existing_storage_before $record.existing_storage_after
 AssertProtected;AssertBase;AssertLibrarianBarrier $record;$null=Check 'Maintenance';$null=Check 'Assets'
 if((FileHash $featurePath) -cne $intent.feature_sha256){throw 'FEATURE_CONFIG_CHANGED'}
 # No model API/inference, OCR dispatch, DB migration, baseline adoption or old-version restore.
 Step 'all-postconditions-passed-activation-pending'
 ReleaseLibrarianBarrier $record
 $go=@{schema='localbrain.chat-ocr-activation.v1';source=$intent.source_commit;manifest_sha256=$intent.manifest_sha256;
  spec_sha256=$intent.spec_sha256;data_root=$root;databases=$intent.databases;intent_sha256=$ApprovedIntentSha256;
  existing_history_preserved=$true;loopback_authenticated_endpoints_verified=$true;
  clientpc_cross_machine_verified=$false;verified_utc=[DateTime]::UtcNow.ToString('o')}
 $goTemp=Join-Path $intent.activation_root 'activation-go.pending.json'
 $goBytes=[Text.UTF8Encoding]::new($false).GetBytes(($go|ConvertTo-Json -Depth 16)+[char]10)
 $goStream=[IO.FileStream]::new($goTemp,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
 try{$goStream.Write($goBytes);$goStream.Flush($true)}finally{$goStream.Dispose()}
 if((FileHash $goTemp) -cne (HashBytes $goBytes)){throw 'ACTIVATION_GO_BYTES_DRIFT'}
 # Atomic, durable admission decision. No fallible production step follows this commit point.
 [IO.File]::Move($goTemp,$intent.activation_go,$false)
 $record.activation_committed=$true;$record.phase='ACTIVATION_COMMITTED';$record.completed_utc=[DateTime]::UtcNow.ToString('o')
 SaveReceipt
 $record|ConvertTo-Json -Depth 32
}catch{
 $committed=Test-Path -LiteralPath $intent.activation_go
 $record.activation_committed=$committed
 $record.phase=if($committed){'ACTIVATION_PRESENT_RECEIPT_OR_POST_COMMIT_ERROR'}elseif($record.new_processes.Count -eq 2){'NEW_SERVICES_HELD_FORWARD_REPAIR_REQUIRED'}else{'PARTIAL_TRANSITION_NO_ACTIVATION_GO_OLD_ADMISSION_UNCONFIRMED'}
 $record.error_code=if($_.Exception.Message -cmatch '^[A-Z][A-Z_]{2,100}$'){$_.Exception.Message}else{'PRIVATE_ERROR_DETAILS_SUPPRESSED'}
 if($auditOwned){try{SaveReceipt}catch{}}
 throw $record.error_code
}finally{
 if($script:LibrarianBarrierStream){ReleaseLibrarianBarrier $record}
 if($attempt){$attempt.Dispose()}
}
