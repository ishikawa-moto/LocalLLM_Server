#Requires -Version 7.2
param([Parameter(Mandatory)][string]$DataRoot,[Parameter(Mandatory)][string]$ReferencePacket,
 [Parameter(Mandatory)][string]$ReferenceResult,[Parameter(Mandatory)][string]$ModelBaseline,
 [Parameter(Mandatory)][string]$EmbeddingBinding,[Parameter(Mandatory)][string]$Python,
 [Parameter(Mandatory)][string]$Output)
# Read-only preparation, using public preserved artifacts for protected bindings.
# The elevated Apply rechecks actual protected bytes and actual SYSTEM commands.
$ErrorActionPreference='Stop'
function HashText([string]$text){[Convert]::ToHexString([Security.Cryptography.SHA256]::HashData([Text.Encoding]::UTF8.GetBytes($text))).ToLowerInvariant()}
function SafeExisting([string]$path){
 $full=[IO.Path]::GetFullPath($path)
 for($part=$full;$part;$part=[IO.Path]::GetDirectoryName($part)){
  if((Get-Item -LiteralPath $part -Force).Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'REPARSE_REFUSED'}
 }
 return $full
}
function Binding([string]$path,[string]$knownHash=''){
 $p=SafeExisting $path;$item=Get-Item -LiteralPath $p -Force
 @{path=$p;sha256=$(if($knownHash){$knownHash}else{(Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash.ToLowerInvariant()});
   length=$item.Length;acl_sha256=HashText (Get-Acl -LiteralPath $p).Sddl}
}
$root=SafeExisting $DataRoot
$specRef=SafeExisting (Join-Path $ReferencePacket 'startup-spec.json')
$spec=Get-Content -LiteralPath $specRef -Raw|ConvertFrom-Json -AsHashtable
$specHash=(Get-FileHash -LiteralPath $specRef -Algorithm SHA256).Hash.ToLowerInvariant()
if($spec.source_commit -cne '55a206943b43cf2041742049b7265333dff91ae8' -or
 $specHash -cne 'd5fd6df8f64b67b11d3ec49a6ff7c41b902d94499d5a75c099d292a14826cd90' -or
 $spec.data_root -cne $root){throw 'OLD_REFERENCE_BINDING_DRIFT'}
$goRef=SafeExisting (Join-Path $ReferenceResult 'activation-go.json')
$receiptRef=SafeExisting (Join-Path $ReferenceResult 'apply-execution.json')
$prior=Get-Content -LiteralPath $receiptRef -Raw|ConvertFrom-Json -AsHashtable
if($prior.activation_committed -cne $true){throw 'PRIOR_ACTIVATION_UNKNOWN'}
$service=New-Object -ComObject Schedule.Service;$service.Connect();$folder=$service.GetFolder('\')
$site=Get-Content -LiteralPath (Join-Path $root 'config/private-site.json') -Raw|ConvertFrom-Json -AsHashtable
function Port([string]$role){
 $file=Join-Path $root ('config/'+$role+'.json');$config=if(Test-Path -LiteralPath $file){Get-Content -LiteralPath $file -Raw|ConvertFrom-Json -AsHashtable}else{@{}}
 $port=if($config.ContainsKey('port')){$config.port}else{$site[$role+'_port']}
 if($port -isnot [long] -and $port -isnot [int]){throw 'PORT_UNKNOWN'}
 return $port
}
$listeners=@(Get-NetTCPConnection -State Listen)
function Processes([string]$role){
 @(foreach($processId in @($listeners|Where-Object LocalPort -EQ (Port $role)|Select-Object -ExpandProperty OwningProcess -Unique)){
  $p=Get-CimInstance -Query "SELECT CreationDate FROM Win32_Process WHERE ProcessId=$processId"
  if($p.CreationDate -isnot [DateTime]){throw 'PROCESS_BIRTH_UNKNOWN'}
  @{pid=[int]$processId;birth_filetime=$p.CreationDate.ToFileTimeUtc().ToString()}
 })
}
$oldSpecPath=Join-Path (Split-Path $spec.entry_path -Parent) 'startup-spec.json'
$tasks=@(foreach($role in @('gateway','chat')){
 $name=if($role -eq 'gateway'){'LocalBrain Control Gateway'}else{'LocalBrain Chat Web'}
 $task=$folder.GetTask($name);$d=$task.Definition;$a=@($d.Actions);$instances=@($task.GetInstances(0));$p=@(Processes $role)
 $arguments='-I -B "'+$spec.entry_path+'" --role '+$role+' --spec "'+$oldSpecPath+'" --spec-sha256 '+$specHash
 if(-not $task.Enabled -or [int]$task.State -ne 4 -or $a.Count -ne 1 -or $a[0].Path -cne $Python -or
  $a[0].Arguments -cne $arguments -or $a[0].WorkingDirectory -cne $root -or
  $instances.Count -ne 1 -or $p.Count -ne 1 -or [int]$instances[0].EnginePID -ne $p[0].pid){
  $engine=if($instances.Count -eq 1){[int]$instances[0].EnginePID}else{0}
  $observed=if($engine){Get-CimInstance -Query "SELECT ExecutablePath,CommandLine FROM Win32_Process WHERE ProcessId=$engine"}else{$null}
  $diagnostic=@{role=$role;computer=$env:COMPUTERNAME;administrator=([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator);
   enabled=$task.Enabled;state=[int]$task.State;action_count=$a.Count;path_matches=($a[0].Path -ceq $Python);arguments_match=($a[0].Arguments -ceq $arguments);working_directory_matches=($a[0].WorkingDirectory -ceq $root);
   instance_count=$instances.Count;listener_count=$p.Count;engine_pid=$engine;listener_pids=@($p.pid);process_path_readable=[bool]$observed.ExecutablePath;process_command_readable=[bool]$observed.CommandLine;
   capture_source=$PSCommandPath;production_operations=0}
  $raw=[Text.UTF8Encoding]::new($false).GetBytes(($diagnostic|ConvertTo-Json -Depth 8)+[char]10)
  $f=[IO.FileStream]::new($Output+'.binding-failure.json',[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
  try{$f.Write($raw,0,$raw.Length);$f.Flush($true)}finally{$f.Dispose()}
  throw 'CURRENT_TASK_BINDING_UNKNOWN'
 }
 $copy=$service.NewTask(0);$copy.XmlText=$task.Xml
 @{role=$role;name=$name;xml_sha256=HashText $copy.XmlText;definition_xml=$copy.XmlText;
   sddl_sha256=HashText $task.GetSecurityDescriptor(7);pid=$p[0].pid;birth_filetime=$p[0].birth_filetime;
   instance_guid=([guid]$instances[0].InstanceGuid).ToString();old_arguments=$arguments;
   command_sha256=HashText ('"'+$Python+'" '+$arguments);
   command_observation='expected from live definition; elevated Apply must verify actual SYSTEM path/command/parent'}
})
$config=@(Get-ChildItem -LiteralPath (Join-Path $root 'config') -File -Recurse -Force|Sort-Object FullName)
$protected=@(foreach($file in $config){Binding $file.FullName})
$oldModels=Get-Content -LiteralPath (SafeExisting $ModelBaseline) -Raw|ConvertFrom-Json -AsHashtable
$embedding=Get-Content -LiteralPath (SafeExisting $EmbeddingBinding) -Raw|ConvertFrom-Json -AsHashtable
$models=@(foreach($m in @($oldModels.models)+@($embedding)){
 $b=Binding $m.path $m.sha256
 if($b.length -ne $m.length -or $b.sha256 -cnotmatch '^[a-f0-9]{64}$'){throw 'MODEL_REFERENCE_DRIFT'}
 $b
})
$base=@{};foreach($role in @('llm','brain','proxy','embedding')){$base[$role]=@(Processes $role)}
$value=@{schema='localbrain.gateway-forward-baseline.v1';observed_utc=[DateTime]::UtcNow.ToString('o');data_root=$root;
 tasks=$tasks;protected_files=$protected;config_inventory=@($config.FullName);models=$models;base_processes=$base;
 model_hash_scope='Preserved expected hashes; elevated Apply hashes actual models under read locks before stopping anything';
 old_spec_reference=$specRef;old_spec_path=$oldSpecPath;old_spec_sha256=$specHash;
 old_manifest_sha256=$spec.manifest_sha256;old_activation_go_sha256=(Get-FileHash -LiteralPath $goRef -Algorithm SHA256).Hash.ToLowerInvariant();
 old_receipt_sha256=(Get-FileHash -LiteralPath $receiptRef -Algorithm SHA256).Hash.ToLowerInvariant();old_receipt_intent=$prior.intent_sha256;
 old_receipt_path=(Join-Path 'C:\ProgramData\LocalBrainServer' ('chat-ocr-attempt-'+(Split-Path (Split-Path $oldSpecPath -Parent) -Leaf)+'/execution.json'));
 runtimes=@((Binding $Python),(Binding (Get-Process -Id $PID).Path))}
$bytes=[Text.UTF8Encoding]::new($false).GetBytes(($value|ConvertTo-Json -Depth 25)+[char]10)
$stream=[IO.FileStream]::new($Output,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
try{$stream.Write($bytes,0,$bytes.Length);$stream.Flush($true)}finally{$stream.Dispose()}
@{status='READ_ONLY_BASELINE_PREPARED';baseline_sha256=(Get-FileHash -LiteralPath $Output -Algorithm SHA256).Hash.ToLowerInvariant();task_count=2;production_writes=0}|ConvertTo-Json
