#Requires -Version 7.2
param([Parameter(Mandatory)][string]$Python)
$ErrorActionPreference='Stop'
$apply=Join-Path $PSScriptRoot 'Apply-GatewayRelease.ps1';$module=Join-Path $PSScriptRoot 'release_fence.ps1'
foreach($path in @($apply,$module)){
 $t=$null;$e=$null;$a=[Management.Automation.Language.Parser]::ParseFile($path,[ref]$t,[ref]$e)
 if($e.Count){throw 'PARSE_ERROR'}
 foreach($f in @($a.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$false))){
  if($path -eq $module -or $f.Name -in @('SafePath','FileHash','StringHash')){. ([scriptblock]::Create($f.Extent.Text))}
 }
}
$parent=[IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')
$root=Join-Path $parent ('localbrain-fence-public-'+[guid]::NewGuid().ToString('N'))
$null=New-Item -ItemType Directory -Path (Join-Path $root 'runtime')
$lockPath=Join-Path $root 'runtime\knowledge-transaction.lock';[IO.File]::WriteAllText($lockPath,'0')
$intent=@{knowledge_lock_binding=@{path='runtime/knowledge-transaction.lock';sha256=FileHash $lockPath;acl_sha256=StringHash (Get-Acl -LiteralPath $lockPath).Sddl;creation_filetime=(Get-Item -LiteralPath $lockPath).CreationTimeUtc.ToFileTimeUtc().ToString()}}
$record=@{};$children=@();$passed=0;$pass=$false
$guardPath=Join-Path (Split-Path $PSScriptRoot -Parent) 'src\knowledge_guard.py'
$worker=@'
import importlib.util,pathlib,sys,types
if sys.argv[3]=='librarian':
 sys.path.insert(0,str(pathlib.Path(sys.argv[1]).parent))
 from knowledge_pipeline import KnowledgePipeline
 brain=types.SimpleNamespace(runtime=pathlib.Path(sys.argv[2])/'runtime',docs=pathlib.Path(sys.argv[2])/'public-docs',settings={})
 pipeline=KnowledgePipeline(brain)
 def forbidden(*a,**k):raise AssertionError('Unexpected discovery or extraction')
 pipeline._run_queue_locked=forbidden
 result=pipeline.run_queue(extractor=object())
 assert result.get('locked') is True,result
 print('LOCKED_WITHOUT_DISCOVERY_OR_EXTRACT',flush=True)
 raise SystemExit(0)
spec=importlib.util.spec_from_file_location('public_guard',sys.argv[1]);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
brain=types.SimpleNamespace(runtime=pathlib.Path(sys.argv[2])/'runtime')
print('STARTED',flush=True)
with m.guard(brain):
 print('ENTERED',flush=True)
 if sys.argv[3]=='hold':sys.stdin.readline()
print('RELEASED',flush=True)
'@
function StartWorker([string]$mode){
 $psi=[Diagnostics.ProcessStartInfo]::new($Python);$psi.UseShellExecute=$false;$psi.CreateNoWindow=$true
 $psi.RedirectStandardInput=$true;$psi.RedirectStandardOutput=$true;$psi.RedirectStandardError=$true
 foreach($arg in @('-I','-B','-c',$worker,$guardPath,$root,$mode)){$psi.ArgumentList.Add($arg)}
 $p=[Diagnostics.Process]::new();$p.StartInfo=$psi;$null=$p.Start();$script:children+=@($p);return $p
}
function Line($process,[string]$expected){
 $read=$process.StandardOutput.ReadLineAsync()
 if(-not $read.Wait(3000) -or $read.Result -ne $expected){throw 'PUBLIC_WORKER_PROTOCOL'}
}
function Finish($process){
 if(-not $process.WaitForExit(3000) -or $process.ExitCode -ne 0){throw 'PUBLIC_WORKER_FAILED'}
}
try{
 AcquireLibrarianBarrier $record
 # The existing run_queue must recognize the native file sharing/access protocol.
 $p=StartWorker 'librarian';Line $p 'LOCKED_WITHOUT_DISCOVERY_OR_EXTRACT';Finish $p;$passed++
 $barrier=Join-Path $root 'runtime\librarian.lock';$renamed=Join-Path $root 'runtime\public-unexpected-marker'
 $replaceBlocked=$false
 try{[IO.File]::Move($barrier,$renamed)}catch [IO.IOException]{$replaceBlocked=$true}
 if(-not $replaceBlocked -or -not (Test-Path -LiteralPath $barrier) -or (Test-Path -LiteralPath $renamed)){throw 'BARRIER_REPLACEMENT_NOT_BLOCKED'}
 $passed++
 AcquireKnowledgeFence $record
 $p=StartWorker 'enter';Line $p 'STARTED'
 $waiting=$p.StandardOutput.ReadLineAsync()
 if($waiting.Wait(200)){throw 'KNOWLEDGE_PRODUCER_ENTERED_WHILE_FENCED'}
 $passed++
 ReleaseKnowledgeFence $record
 if(-not $waiting.Wait(3000) -or $waiting.Result -ne 'ENTERED'){throw 'PRODUCER_NOT_RELEASED'}
 Line $p 'RELEASED';Finish $p;$passed++
 $record.KnowledgeFence=$null
 $p=StartWorker 'hold';Line $p 'STARTED';Line $p 'ENTERED'
 $busy=$false;$clock=[Diagnostics.Stopwatch]::StartNew()
 try{AcquireKnowledgeFence $record}catch{if($_.Exception.Message -ne 'KNOWLEDGE_WRITER_BUSY'){throw};$busy=$true}
 if(-not $busy -or $clock.Elapsed.TotalSeconds -gt 4){throw 'BUSY_BOUND_FAILED'}
 $passed++;$p.StandardInput.WriteLine('release');Line $p 'RELEASED';Finish $p
 AcquireKnowledgeFence $record
 try{throw 'PUBLIC_CRITICAL_FAILURE'}catch{if($_.Exception.Message -ne 'PUBLIC_CRITICAL_FAILURE'){throw}}finally{ReleaseKnowledgeFence $record}
 $p=StartWorker 'enter';Line $p 'STARTED';Line $p 'ENTERED';Line $p 'RELEASED';Finish $p;$passed++
 ReleaseLibrarianBarrier $record
 if(Test-Path -LiteralPath (Join-Path $root 'runtime\librarian.lock')){throw 'OWNED_BARRIER_REMAINED'}
 $passed++
 [IO.File]::WriteAllText((Join-Path $root 'runtime\librarian.lock'),'PUBLIC existing producer')
 $record=@{};$refused=$false
 try{AcquireLibrarianBarrier $record}catch{if($_.Exception.Message -ne 'LIBRARIAN_ACTIVE_OR_UNKNOWN_NO_STALE_REMOVAL'){throw};$refused=$true}
 if(-not $refused -or [IO.File]::ReadAllText((Join-Path $root 'runtime\librarian.lock')) -ne 'PUBLIC existing producer'){throw 'EXISTING_PRODUCER_LOCK_MODIFIED'}
 $passed++
 # Cleanup must never resolve the pathname for deletion after dropping the owned handle.
 $releaseText=($a.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'ReleaseLibrarianBarrier'},$false))[0].Extent.Text
 if($releaseText -match 'Remove-Item|\[IO\.File\]::Delete\(' -or $releaseText -notmatch 'DeleteOwned\(\$script:LibrarianBarrierStream\)'){throw 'PATH_BASED_CLEANUP_FORBIDDEN'}
 $passed++;$pass=$true
 @{status='PASS';checks=$passed;module_sha256=FileHash $module;knowledge_guard_sha256=FileHash $guardPath;production_locks=0;native_uac=0;models=0;network_calls=0}|ConvertTo-Json
}finally{
 try{ReleaseKnowledgeFence $record}finally{if($script:LibrarianBarrierStream){ReleaseLibrarianBarrier $record}}
 foreach($p in $children){if(-not $p.HasExited){$p.Kill();$p.WaitForExit(3000)|Out-Null};$p.Dispose()}
 if($pass){$resolved=[IO.Path]::GetFullPath($root);if(-not $resolved.StartsWith($parent+'\localbrain-fence-public-',[StringComparison]::OrdinalIgnoreCase)){throw 'PUBLIC_CLEANUP_SCOPE'};Remove-Item -LiteralPath $resolved -Recurse -Force}
}
