param([string]$ClientAddress)
# Run once in an elevated PowerShell on the LocalBrain server.
$ErrorActionPreference='Stop'
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
$principal=[Security.Principal.WindowsPrincipal]::new($identity)
if(-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){ throw 'Run Install-Server.ps1 from an elevated PowerShell.' }
$root=$PSScriptRoot
. (Join-Path $PSScriptRoot 'Site-Settings.ps1')
$sitePath=Join-Path $root 'config\private-site.json'
$site=if(Test-Path -LiteralPath $sitePath){Get-Content -LiteralPath $sitePath -Raw | ConvertFrom-Json}else{$null}
$firewallName=if($site.gateway_firewall_name){$site.gateway_firewall_name}else{'LocalBrain Gateway from ClientPC'}
if(-not $ClientAddress){
  $sitePath=Join-Path $root 'config\private-site.json'
  if(Test-Path -LiteralPath $sitePath){$ClientAddress=(Get-Content -LiteralPath $sitePath -Raw | ConvertFrom-Json).client_address}
}
if(-not $ClientAddress){throw 'Specify ClientAddress or configure private site settings before installation.'}
$pythonRuntime=Get-Content -LiteralPath (Join-Path $root 'config\python-runtime.json') -Raw | ConvertFrom-Json
$python=$pythonRuntime.executable
if(-not (Test-Path -LiteralPath $python)){ throw 'LocalBrain Python runtime is missing.' }
New-Item -ItemType Directory -Path (Join-Path $root 'logs') -Force | Out-Null
$protected=@('config\api-keys.json','config\tls\server-key.pem','config\tls\client-key.pem',
  'runtime\legacy-client-v1\client-config.json','runtime\legacy-client-v1\certs\client-key.pem')
foreach($relative in $protected){
  $path=Join-Path $root $relative
  if(Test-Path -LiteralPath $path){ & icacls.exe $path '/inheritance:r' '/grant:r' 'SYSTEM:(F)' ($env:USERNAME+':(F)') | Out-Null }
}
$manager=Join-Path $root 'Manage-LocalBrain.ps1'
if(Test-Path -LiteralPath $manager){ & $manager -Action StopGateway | Out-Host }
foreach($attempt in 1..20){
  if(-not (Get-NetTCPConnection -LocalPort (Get-LocalBrainPort gateway) -State Listen -ErrorAction SilentlyContinue)){ break }
  Start-Sleep -Milliseconds 250
}
if(Get-NetTCPConnection -LocalPort (Get-LocalBrainPort gateway) -State Listen -ErrorAction SilentlyContinue){ throw 'Gateway TCP port is still occupied; inspect the existing listener before installing.' }
$action=New-ScheduledTaskAction -Execute $python -Argument ('"'+(Join-Path $root 'src\control_gateway.py')+'"') -WorkingDirectory $root
$trigger=New-ScheduledTaskTrigger -AtStartup
$settings=New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable
$taskPrincipal=New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
Register-ScheduledTask -TaskName 'LocalBrain Control Gateway' -Action $action -Trigger $trigger -Settings $settings -Principal $taskPrincipal -Description 'mTLS gateway and on-demand model control' -Force | Out-Null
$startup=Join-Path ([Environment]::GetFolderPath('Startup')) 'LocalBrain-Gateway.cmd'
if(Test-Path -LiteralPath $startup){ Remove-Item -LiteralPath $startup -Force }
Get-NetFirewallRule -DisplayName $firewallName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName $firewallName -Direction Inbound -Protocol TCP -LocalPort (Get-LocalBrainPort gateway) -RemoteAddress $ClientAddress -Action Allow -Profile Private | Out-Null
Start-ScheduledTask -TaskName 'LocalBrain Control Gateway'
Start-Sleep -Seconds 2
if(-not (Get-NetTCPConnection -LocalPort (Get-LocalBrainPort gateway) -State Listen -ErrorAction SilentlyContinue)){ throw 'Gateway task was registered but did not start; inspect Task Scheduler history and logs.' }
Write-Host "LocalBrain Gateway is registered for machine startup and restricted to $ClientAddress."
