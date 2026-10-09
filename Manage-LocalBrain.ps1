param([ValidateSet('Status','StartChat','StopChat','RestartChat','StatusChat','HealthChat','StartLLM','StartEmbedding','StartBrain','StartProxy','StartGateway','StartLongRun','RunLibrarian','StopLLM','StopEmbedding','StopBrain','StopProxy','StopGateway','StopLongRun','IndexBrain')][string]$Action='Status')
$ErrorActionPreference='Stop'
$taskRoot=$PSScriptRoot
. (Join-Path $PSScriptRoot 'Site-Settings.ps1')
$taskState=Join-Path $taskRoot 'runtime'
$taskLogs=Join-Path $taskRoot 'logs'
function Get-OwnedProcess([string]$name) {
    $recordPath=Join-Path $taskState ($name+'.process.json')
    if (-not (Test-Path -LiteralPath $recordPath)) { return $null }
    $record=Get-Content -LiteralPath $recordPath -Raw | ConvertFrom-Json
    $process=Get-Process -Id $record.pid -ErrorAction SilentlyContinue
    if ($null -eq $process) { return $null }
    if ($process.Path -ne $record.executable -or $process.StartTime.ToUniversalTime().Ticks.ToString() -ne $record.startedTicks) {
        Write-Warning "Stale process record for $name; PID $($record.pid) belongs to another process. Leaving that process untouched."
        return $null
    }
    return $process
}
function Start-Owned([string]$name,[string]$exe,[string[]]$arguments) {
    $existing=Get-OwnedProcess $name
    if ($null -ne $existing) { Write-Output "$name already running, PID $($existing.Id)"; return }
    foreach ($suffix in @('out','err')) {
        $log=Join-Path $taskLogs "$name.$suffix.log"
        if (Test-Path -LiteralPath $log) {
            Move-Item -LiteralPath $log -Destination ($log+'.'+(Get-Date -Format 'yyyyMMdd-HHmmss'))
        }
    }
    $quoted=@($arguments | ForEach-Object { '"'+($_ -replace '"','\"')+'"' })
    $resolvedExe=[IO.Path]::GetFullPath($exe)
    $process=Start-Process -FilePath $resolvedExe -ArgumentList $quoted -WorkingDirectory $taskRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $taskLogs "$name.out.log") -RedirectStandardError (Join-Path $taskLogs "$name.err.log")
    if ($process.HasExited) { throw "$name exited immediately; inspect logs." }
    @{pid=$process.Id;executable=$resolvedExe;startedTicks=$process.StartTime.ToUniversalTime().Ticks.ToString()} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $taskState "$name.process.json") -Encoding utf8
    Write-Output "$name started, PID $($process.Id). Check Status and logs for readiness."
}
function Test-TcpPort([int]$port) {
    $client=[Net.Sockets.TcpClient]::new()
    try {
        $task=$client.ConnectAsync($LocalBrainLoopback,$port)
        return $task.Wait(1000) -and $client.Connected
    } catch { return $false } finally { $client.Dispose() }
}
switch ($Action) {
    'StartChat' {
        # Fail closed if the SYSTEM task is hidden from a non-admin token.
        # Never fall back to starting a duplicate process on the same port.
        Start-ScheduledTask -TaskName 'LocalBrain Chat Web' -ErrorAction Stop
    }
    'StopChat' {
        Stop-ScheduledTask -TaskName 'LocalBrain Chat Web' -ErrorAction Stop
    }
    'RestartChat' {
        & $MyInvocation.MyCommand.Path -Action StopChat
        Start-Sleep -Seconds 2
        & $MyInvocation.MyCommand.Path -Action StartChat
    }
    {$_ -in 'StatusChat','HealthChat'} {
        $chatSettings=Get-Content -LiteralPath (Join-Path $taskRoot 'config\chat.json') -Raw | ConvertFrom-Json
        Invoke-RestMethod -Uri (('http://'+$LocalBrainLoopback+':')+[string]$chatSettings.port+'/api/health') -TimeoutSec 10
    }
    'StartLLM' {
        $settings=Get-Content -LiteralPath (Join-Path $taskRoot 'config\llama-settings.json') -Raw | ConvertFrom-Json
        $model=if($settings.model_path){[Environment]::ExpandEnvironmentVariables($settings.model_path)}else{Join-Path $taskRoot 'models\Qwen3.8-27B-DT-IQ3_XXS.gguf'}
        if (-not (Test-Path -LiteralPath $model)) { throw "Verified model file is not ready: $model" }
        $key=Get-Content -LiteralPath (Join-Path $taskRoot 'config\api-keys.json') -Raw | ConvertFrom-Json
        $keyfile=Join-Path $taskRoot 'config\llm.key'
        [IO.File]::WriteAllText($keyfile,$key.llm,[Text.UTF8Encoding]::new($false))
        $arguments=@('-m',$model,'--host',$LocalBrainLoopback,'--port',([string](Get-LocalBrainPort llm)),'--alias','local-qwen38','--api-key-file',$keyfile,'--ctx-size',[string]$settings.context,'--parallel','1','--n-gpu-layers',[string]$settings.gpu_layers,'--fit','off','--flash-attn','on','--cache-type-k',$settings.cache_k,'--cache-type-v',$settings.cache_v,'--batch-size','256','--ubatch-size','128','--threads','4','--threads-batch','8','--no-mmproj','--spec-type','none','--reasoning-effort','low','--reasoning-budget','128','--no-reasoning-preserve','--n-predict','4096','--no-webui','--timeout','300')
        Start-Owned 'llm' (Join-Path $taskRoot 'bin\llama-b10809-cuda12.4\llama-server.exe') $arguments
    }
    'StartBrain' {
        $runtime=Get-Content -LiteralPath (Join-Path $taskRoot 'config\python-runtime.json') -Raw | ConvertFrom-Json
        Start-Owned 'brain' $runtime.executable @((Join-Path $taskRoot 'src\localbrain.py'),'serve')
    }
    'StartEmbedding' {
        $model='D:\LocalBrain\embedding-models\Qwen3-Embedding-0.6B-Q8_0.gguf'
        if(-not (Test-Path -LiteralPath $model)){ throw "Embedding model is missing: $model" }
        $arguments=@('-m',$model,'--host',$LocalBrainLoopback,'--port',([string](Get-LocalBrainPort embedding)),'--alias','qwen3-embedding-0.6b-q8','--embedding','--pooling','last','--embd-normalize','2','--ctx-size','8192','--parallel','1','--n-gpu-layers','0','--batch-size','2048','--ubatch-size','512','--no-webui')
        Start-Owned 'embedding' (Join-Path $taskRoot 'bin\llama-b10809-cuda12.4\llama-server.exe') $arguments
    }
    'StartProxy' {
        $runtime=Get-Content -LiteralPath (Join-Path $taskRoot 'config\python-runtime.json') -Raw | ConvertFrom-Json
        Start-Owned 'proxy' $runtime.executable @((Join-Path $taskRoot 'src\codex_proxy.py'))
    }
    'StartGateway' {
        $runtime=Get-Content -LiteralPath (Join-Path $taskRoot 'config\python-runtime.json') -Raw | ConvertFrom-Json
        Start-Owned 'gateway' $runtime.executable @((Join-Path $taskRoot 'src\control_gateway.py'))
    }
    'StartLongRun' {
        $runtime=Get-Content -LiteralPath (Join-Path $taskRoot 'config\python-runtime.json') -Raw | ConvertFrom-Json
        Start-Owned 'stability' $runtime.executable @((Join-Path $taskRoot 'tests\stability_test.py'),'--minutes','120','--interval','60')
    }
    'RunLibrarian' {
        $runtime=Get-Content -LiteralPath (Join-Path $taskRoot 'config\python-runtime.json') -Raw | ConvertFrom-Json
        $settings=Get-Content -LiteralPath (Join-Path $taskRoot 'config\secondbrain.json') -Raw | ConvertFrom-Json
        $limit=if($settings.librarian_batch_limit){[int]$settings.librarian_batch_limit}else{1}
        & $runtime.executable (Join-Path $taskRoot 'src\localbrain.py') librarian --limit $limit --retry-failed
        if($LASTEXITCODE -ne 0){ throw 'SecondBrain librarian failed.' }
    }
    'IndexBrain' {
        $runtime=Get-Content -LiteralPath (Join-Path $taskRoot 'config\python-runtime.json') -Raw | ConvertFrom-Json
        & $MyInvocation.MyCommand.Path -Action StartEmbedding
        $ready=$false
        foreach($attempt in 1..180){ try {if((Invoke-RestMethod -Uri ("http://${LocalBrainLoopback}:$(Get-LocalBrainPort embedding)/health") -TimeoutSec 2).status -eq 'ok'){$ready=$true;break}}catch{}; Start-Sleep -Seconds 1 }
        if(-not $ready){throw 'Embedding service did not become ready.'}
        & $runtime.executable (Join-Path $taskRoot 'src\localbrain.py') index
        if($LASTEXITCODE -ne 0){ throw 'SecondBrain indexing failed.' }
    }
    {$_ -in 'StopLLM','StopEmbedding','StopBrain','StopProxy','StopGateway','StopLongRun'} {
        $name=if ($Action -eq 'StopLLM') {'llm'} elseif($Action -eq 'StopEmbedding') {'embedding'} elseif ($Action -eq 'StopBrain') {'brain'} elseif ($Action -eq 'StopProxy') {'proxy'} elseif($Action -eq 'StopLongRun'){'stability'} else {'gateway'}
        $process=Get-OwnedProcess $name
        if ($null -ne $process) {
            & taskkill.exe /PID $process.Id /T /F | Out-Null
            if($LASTEXITCODE -ne 0){ throw "Failed to stop the verified $name process tree." }
            Write-Output "$name stopped."
        } else { Write-Output "$name is not running." }
    }
    default {
        foreach ($name in @('llm','embedding','brain','proxy','gateway','stability')) {
            $process=Get-OwnedProcess $name
            if($name -eq 'stability'){
                [pscustomobject]@{Service=$name;PID=if($process){$process.Id}else{$null};Health=if($process){'running'}else{'not-running'};Address=(Join-Path $taskLogs 'stability-current.json')}
                continue
            }
            $port=if ($name -eq 'llm') {(Get-LocalBrainPort llm)} elseif($name -eq 'embedding'){(Get-LocalBrainPort embedding)} elseif ($name -eq 'brain') {(Get-LocalBrainPort brain)} elseif ($name -eq 'proxy') {(Get-LocalBrainPort proxy)} else {(Get-LocalBrainPort gateway)}
            $health='unavailable'
            if($name -eq 'gateway') { if(Test-TcpPort $port){$health='listening'} }
            else { try { $health=(Invoke-RestMethod -Uri "http://${LocalBrainLoopback}:$port/health" -TimeoutSec 2).status } catch {} }
            [pscustomobject]@{Service=$name;PID=if($process){$process.Id}else{$null};Health=$health;Address="${LocalBrainLoopback}:$port"}
        }
    }
}
