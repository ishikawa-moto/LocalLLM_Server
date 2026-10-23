#Requires -Version 7.2
param([ValidateSet('Describe','Prepare')][string]$Mode='Describe',[string]$CandidateRoot='',[string]$DataRoot='',
 [string]$Packet='',[string]$ExpectedCommit='',[string]$ObservedProductionManifest='')
$ErrorActionPreference='Stop'
if($Mode -eq 'Describe'){@{mode='Describe';production_writes=0;task_operations=0;database_writes=0;staging_only=$true}|ConvertTo-Json;exit 0}
function FileHash($p){(Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash.ToLowerInvariant()}
function TextHash([string]$v){[Convert]::ToHexString([Security.Cryptography.SHA256]::HashData([Text.Encoding]::UTF8.GetBytes($v))).ToLowerInvariant()}
function GuardPath([string]$p,[string]$base){
 $p=[IO.Path]::GetFullPath($p);$base=[IO.Path]::GetFullPath($base).TrimEnd('\')
 if(-not $p.StartsWith($base+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'PATH_SCOPE'}
 $part=$p;while($part){if(Test-Path -LiteralPath $part){if((Get-Item -LiteralPath $part -Force).Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'REPARSE_PATH'}};$part=[IO.Path]::GetDirectoryName($part)}
 return $p
}

function GitBytes([string[]]$gitArguments){
 $psi=[Diagnostics.ProcessStartInfo]::new()
 $psi.FileName=(Get-Command git -CommandType Application | Select-Object -First 1).Source
 $psi.UseShellExecute=$false;$psi.RedirectStandardOutput=$true;$psi.RedirectStandardError=$true
 foreach($a in @($g)+$gitArguments){$psi.ArgumentList.Add($a)}
 $process=[Diagnostics.Process]::new();$process.StartInfo=$psi;$stream=[IO.MemoryStream]::new()
 try{
  $null=$process.Start()
  $errorTask=$process.StandardError.ReadToEndAsync()
  $copy=$process.StandardOutput.BaseStream.CopyToAsync($stream)
  $process.WaitForExit();$null=$copy.GetAwaiter().GetResult();$null=$errorTask.GetAwaiter().GetResult()
  if($process.ExitCode){throw 'FIXED_COMMIT_BLOB_READ_FAILED'}
  return ,$stream.ToArray()
 }finally{$stream.Dispose();$process.Dispose()}
}

function NewJson([string]$p,$value){
 $bytes=[Text.UTF8Encoding]::new($false).GetBytes(($value|ConvertTo-Json -Depth 24)+[char]10)
 $f=[IO.FileStream]::new($p,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
 try{$f.Write($bytes);$f.Flush($true)}finally{$f.Dispose()}
}
$CandidateRoot=[IO.Path]::GetFullPath($CandidateRoot);$DataRoot=[IO.Path]::GetFullPath($DataRoot);$Packet=[IO.Path]::GetFullPath($Packet)
if($CandidateRoot -eq $DataRoot -or $Packet.StartsWith($DataRoot+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'STAGE_AND_DATA_MUST_BE_SEPARATE'}
if($ExpectedCommit -notmatch '^[a-f0-9]{40}$'){throw 'EXACT_SOURCE_COMMIT_REQUIRED'}
if((Test-Path -LiteralPath (Join-Path $Packet 'intent.json')) -or (Test-Path -LiteralPath (Join-Path $Packet 'payload'))){throw 'EXISTING_PREPARED_ASSETS_HOLD'}
if(Test-Path -LiteralPath 'C:\ProgramData\LocalBrainServer'){throw 'EXISTING_PROGRAM_ROOT_RECONCILE'}
$g=@('-c',('safe.directory='+$CandidateRoot.Replace('\','/')),'-C',$CandidateRoot)
$head=@(& git @g rev-parse HEAD);if($LASTEXITCODE -or $head.Count -ne 1 -or $head[0] -ne $ExpectedCommit){throw 'CANDIDATE_HEAD_DRIFT'}
$dirty=@(& git @g status --porcelain --untracked-files=all);if($LASTEXITCODE -or $dirty.Count){throw 'CANDIDATE_NOT_CLEAN'}
$old=Get-Content -LiteralPath $ObservedProductionManifest -Raw|ConvertFrom-Json
$protected=@()
foreach($b in @($old.tracked_files)+@($old.config_hashes_only)){
 $p=GuardPath (Join-Path $DataRoot $b.path) $DataRoot
 if((FileHash $p) -ne $b.sha256){throw ('OBSERVED_SOURCE_OR_CONFIG_DRIFT:'+ $b.path)}
 $protected+=@(@{path=$b.path;sha256=$b.sha256;acl_sha256=TextHash (Get-Acl -LiteralPath $p).Sddl})
}
$python=[string](Get-Content -LiteralPath (Join-Path $DataRoot 'config\python-runtime.json') -Raw|ConvertFrom-Json).executable
$pyDir=Split-Path $python -Parent
$dependencies=@()
foreach($b in $old.configured_dependency_files){
 $p=if($b.component -eq 'configured-python'){Join-Path $pyDir $b.file}elseif($b.component -eq 'llama-b10809-cuda12.4'){Join-Path (Join-Path $DataRoot 'bin\llama-b10809-cuda12.4') $b.file}else{throw 'UNKNOWN_DEPENDENCY_COMPONENT'}
 if((FileHash $p) -ne $b.sha256){throw 'DEPENDENCY_DRIFT'}
 $dependencies+=@(@{path=$p;sha256=$b.sha256})
}
$before=Get-Content -LiteralPath (Join-Path $Packet 'database-before.json') -Raw|ConvertFrom-Json
# Execute the exact immutable commit bytes, never reopen a mutable helper path.
$helperBytes=GitBytes @('show',($ExpectedCommit+':deployment/release_db.py'))
$raw=& $python -I -B -c ([Text.Encoding]::UTF8.GetString($helperBytes)) --mode Snapshot --root $DataRoot
if($LASTEXITCODE){throw 'SNAPSHOT_FAILED'}
$live=($raw -join [char]10)|ConvertFrom-Json
if($live.aggregate_sha256 -ne $before.aggregate_sha256){throw 'DB_CHANGED_SINCE_CLASSIFIED_BASELINE_HOLD'}
$observed=Get-Content -LiteralPath (Join-Path $Packet 'runtime-before.json') -Raw|ConvertFrom-Json
$binding=@($observed.tasks|Where-Object name -eq 'LocalBrain Control Gateway')
if($binding.Count -ne 1 -or @($binding[0].instances).Count -ne 1){throw 'OBSERVED_TASK_UNKNOWN'}
$service=New-Object -ComObject Schedule.Service;$service.Connect();$task=$service.GetFolder('\').GetTask('LocalBrain Control Gateway')
$instances=@($task.GetInstances(0));$actions=@($task.Definition.Actions)
if($task.State -ne 4 -or $instances.Count -ne 1 -or $actions.Count -ne 1 -or $actions[0].Path -ne $python -or
 (TextHash $task.Xml) -ne $binding[0].definition_sha256 -or $task.Definition.Principal.UserId -notin @('SYSTEM','S-1-5-18')){throw 'CURRENT_TASK_DRIFT'}
$processId=[int]$instances[0].EnginePID
$birth=(Get-CimInstance -Query "SELECT CreationDate FROM Win32_Process WHERE ProcessId=$processId").CreationDate.ToFileTimeUtc().ToString()
$identity=@{instance_guid=([guid]$instances[0].InstanceGuid).ToString();pid=$processId;process_birth_filetime=$birth}
if($identity.instance_guid -ne $binding[0].instances[0].instance_guid -or $processId -ne $binding[0].instances[0].pid -or $birth -ne $binding[0].instances[0].birth_filetime){throw 'CURRENT_INSTANCE_DRIFT'}
$site=Get-Content -LiteralPath (Join-Path $DataRoot 'config\private-site.json') -Raw|ConvertFrom-Json
$base=@($observed.listeners|Where-Object role -ne 'gateway')
foreach($b in $base){
 $ids=@(Get-NetTCPConnection -State Listen -LocalPort $site.($b.role+'_port') -ErrorAction SilentlyContinue|Select-Object -ExpandProperty OwningProcess -Unique)
 if($ids.Count -ne @($b.processes).Count){throw 'BASE_LISTENER_DRIFT'}
 foreach($p in $b.processes){if($p.pid -notin $ids -or (Get-CimInstance -Query ('SELECT CreationDate FROM Win32_Process WHERE ProcessId='+$p.pid)).CreationDate.ToFileTimeUtc().ToString() -ne $p.birth_filetime){throw 'BASE_BIRTH_DRIFT'}}
}
$databaseFiles=@()
foreach($relative in @('runtime/slack-restricted/state.sqlite3','runtime/gateway-jobs.sqlite3','runtime/chat.sqlite3','runtime/index.sqlite3','runtime/semantic.sqlite3')){
 $p=GuardPath (Join-Path $DataRoot $relative) $DataRoot;$item=Get-Item -LiteralPath $p
 $databaseFiles+=@(@{path=$relative;acl_sha256=TextHash (Get-Acl -LiteralPath $p).Sddl;creation_filetime=$item.CreationTimeUtc.ToFileTimeUtc().ToString()})
}
$payload=GuardPath (Join-Path $Packet 'payload') $Packet
$null=New-Item -ItemType Directory -Path $payload
$fileBytes=GitBytes @('ls-tree','-r','--name-only','-z',$ExpectedCommit)
$files=@([Text.Encoding]::UTF8.GetString($fileBytes).Split([char]0,[StringSplitOptions]::RemoveEmptyEntries))
$manifestFiles=@()
foreach($name in $files){
 if($name -match '^(runtime|logs|models|SecondBrain|bin|output|codex-home)/' -or $name -match '^config/(private-site.json|api-keys.json|llm.key|tls/)'){throw 'PRIVATE_OR_MUTABLE_PAYLOAD_REFUSED'}
 $dest=GuardPath (Join-Path $payload $name) $payload
 $null=New-Item -ItemType Directory -Path (Split-Path $dest -Parent) -Force
 $bytes=GitBytes @('show',($ExpectedCommit+':'+$name))
 $sha=[Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($bytes)).ToLowerInvariant()
 $out=[IO.FileStream]::new($dest,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
 try{$out.Write($bytes);$out.Flush($true)}finally{$out.Dispose()}
 if((FileHash $dest) -ne $sha){throw 'STAGE_COMMIT_BLOB_MISMATCH'}
 $manifestFiles+=@(@{path=$name;sha256=$sha})
}
$finalHead=@(& git @g rev-parse HEAD)
$finalDirty=@(& git @g status --porcelain --untracked-files=all)
if($LASTEXITCODE -or $finalHead[0] -ne $ExpectedCommit -or $finalDirty.Count){throw 'CANDIDATE_CHANGED_DURING_PREPARE_HOLD'}
$tree=@(& git @g rev-parse ($ExpectedCommit+'^{tree}'));if($LASTEXITCODE){throw 'SOURCE_TREE_FAILED'}
NewJson (Join-Path $Packet 'release-manifest.json') @{schema='localbrain.code-release-manifest.v1';source_commit=$ExpectedCommit;source_tree=$tree[0];files=$manifestFiles}
$prodArgs=@('--no-optional-locks','-c',('safe.directory='+$DataRoot.Replace('\','/')),'-C',$DataRoot)
$productionHead=@(& git @prodArgs rev-parse HEAD);if($LASTEXITCODE){throw 'PRODUCTION_HEAD_FAILED'}
$knownStatus=@(& git @prodArgs status --porcelain --untracked-files=all);if($LASTEXITCODE){throw 'PRODUCTION_STATUS_FAILED'}
$intent=@{schema='localbrain.gateway-release-intent.v1';packet=$Packet;data_root=$DataRoot;program_root='C:\ProgramData\LocalBrainServer';
 source_commit=$ExpectedCommit;manifest_sha256=FileHash (Join-Path $Packet 'release-manifest.json');script_sha256=FileHash (Join-Path $payload 'deployment\Apply-GatewayRelease.ps1');
 python=$python;python_sha256=FileHash $python;dependency_files=$dependencies;automatic_restore=$false;admin_image_inspection_required=$true;
 production_head=$productionHead[0];production_index_sha256=FileHash (Join-Path $DataRoot '.git\index');known_git_status=$knownStatus;
 protected_files=$protected;database_files=$databaseFiles;database_before_sha256=FileHash (Join-Path $Packet 'database-before.json');base_listeners=$base;
 tasks=@(@{role='gateway';task='LocalBrain Control Gateway';arguments_sha256=TextHash $actions[0].Arguments;definition_xml_sha256=TextHash $task.Xml;before=$identity});
 state='PREPARED_WAITING_PARENT_CLIENT_MAINTENANCE_GO';native_uac_invocations=0;production_task_operations=0;model_requests=0}
Copy-Item -LiteralPath (Join-Path $payload 'deployment\Apply-GatewayRelease.ps1') -Destination (Join-Path $Packet 'Apply-GatewayRelease.ps1')
NewJson (Join-Path $Packet 'intent.json') $intent
@{status=$intent.state;source_commit=$ExpectedCommit;intent_sha256=FileHash (Join-Path $Packet 'intent.json');manifest_sha256=$intent.manifest_sha256;script_sha256=$intent.script_sha256;files=$manifestFiles.Count;production_writes=0}|ConvertTo-Json
