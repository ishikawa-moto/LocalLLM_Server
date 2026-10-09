param([ValidateSet('immediate','daily')][string]$Mode='daily')
$ErrorActionPreference='Stop'
$taskRoot=$PSScriptRoot
$taskPython=[string](Get-Content -LiteralPath (Join-Path $taskRoot 'config\python-runtime.json') -Raw | ConvertFrom-Json).executable
if(-not (Test-Path -LiteralPath $taskPython -PathType Leaf)){exit 1}
$taskId=[Guid]::NewGuid().ToString('N')
$taskOut=Join-Path $taskRoot ('runtime\draft-review-'+$taskId+'.out')
$taskErr=Join-Path $taskRoot ('runtime\draft-review-'+$taskId+'.err')
$taskCode=1
try {
    $taskArgs=@('-B',('"'+(Join-Path $taskRoot 'src\draft_review_queue.py')+'"'),'--root',('"'+$taskRoot+'"'))
    $taskChild=Start-Process -FilePath $taskPython -ArgumentList $taskArgs -WorkingDirectory $taskRoot -WindowStyle Hidden -PassThru -Wait -RedirectStandardOutput $taskOut -RedirectStandardError $taskErr
    $taskCode=$taskChild.ExitCode
    $taskResult=Get-Content -LiteralPath $taskOut -Raw | ConvertFrom-Json
    if($taskResult.status -notin @('quiet','disabled','coalesced','pending','drained')){throw 'Invalid reviewer status'}
    if($taskResult.status -notin @('quiet','disabled')){
        $taskSafe=[ordered]@{finished_at_utc=[DateTime]::UtcNow.ToString('o');mode=$Mode;exit_code=$taskCode;status=$taskResult.status;processed=[int]$taskResult.processed;pending=[bool]$taskResult.pending}
        if($taskResult.error_type -match '^[A-Za-z][A-Za-z0-9]{0,79}$'){$taskSafe.error_type=[string]$taskResult.error_type}
        $taskSafe | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $taskRoot 'logs\draft-review-last.json') -Encoding UTF8
    }
} catch {
    $taskCode=1
    [ordered]@{finished_at_utc=[DateTime]::UtcNow.ToString('o');mode=$Mode;exit_code=1;error_type=$_.Exception.GetType().Name} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $taskRoot 'logs\draft-review-last.json') -Encoding UTF8
} finally {
    foreach($taskTemp in @($taskOut,$taskErr)){if(Test-Path -LiteralPath $taskTemp){Remove-Item -LiteralPath $taskTemp}}
}
exit $taskCode
