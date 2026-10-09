param(
  [Parameter(Mandatory=$true)][string]$CertificatePfx,
  [Security.SecureString]$CertificatePassword,
  [string]$InitialWorkspace,
  [string]$ServerHost,
  [int]$GatewayPort,
  [int]$BridgePort,
  [switch]$SkipExtensionInstall,
  [switch]$RemovePfxAfterImport
)
$ErrorActionPreference='Stop'
$packageRoot=$PSScriptRoot
$installRoot=Join-Path $env:LOCALAPPDATA 'LocalBrain'
$appRoot=Join-Path $installRoot 'app'
$settings=(Get-Content -LiteralPath (Join-Path $packageRoot 'clientsettings.template.json') -Raw)
if($settings.Contains('__SERVER_HOST__')){
  if(-not $ServerHost){ $ServerHost=Read-Host 'LocalBrain server hostname or IPv4 address' }
  if([Uri]::CheckHostName($ServerHost)-eq[UriHostNameType]::Unknown){ throw 'A valid server hostname or IPv4 address is required.' }
  $settings=$settings.Replace('__SERVER_HOST__',$ServerHost)
}
foreach($entry in @(@('__GATEWAY_PORT__','GatewayPort'),@('__BRIDGE_PORT__','BridgePort'))){
  if($settings.Contains($entry[0])){
    $value=Get-Variable -Name $entry[1] -ValueOnly
    if(-not $value){$value=Read-Host ($entry[1]+' for this deployment')}
    $numeric=0
    if(-not [int]::TryParse([string]$value,[ref]$numeric) -or $numeric -le 0 -or $numeric -gt [UInt16]::MaxValue){throw 'A valid numeric TCP port is required.'}
    $settings=$settings.Replace($entry[0],[string]$numeric)
  }
}
$parsedSettings=$settings | ConvertFrom-Json
$parsedSettings.bridgePort=[int]$parsedSettings.bridgePort
if($parsedSettings.bridgePort -le 0 -or $parsedSettings.bridgePort -gt [UInt16]::MaxValue){throw 'Invalid BridgePort.'}
$gatewayUri=$null
if(-not [Uri]::TryCreate($parsedSettings.gatewayBaseUrl,[UriKind]::Absolute,[ref]$gatewayUri) -or $gatewayUri.Scheme -ne 'https' -or $gatewayUri.Port -le 0){throw 'A valid HTTPS Gateway endpoint is required.'}
$settings=$parsedSettings | ConvertTo-Json -Depth 10
$dotnet=(Get-Command dotnet -ErrorAction Stop)
if(-not $InitialWorkspace){ $InitialWorkspace=Read-Host 'Initial Git workspace path (leave empty to register later)' }
if(([version](& $dotnet.Source --version)).Major -lt 10){ throw '.NET 10 or newer is required.' }
if($InitialWorkspace){
  $git=(Get-Command git -ErrorAction Stop)
  $gitRoot=& $git.Source -C $InitialWorkspace rev-parse --show-toplevel 2>$null
  if($LASTEXITCODE -ne 0){
    Write-Warning "$InitialWorkspace is not a Git repository. Installation will continue; register each real Git repository later."
    $InitialWorkspace=''
  }
}
if(-not $SkipExtensionInstall){
  $code=(Get-Command code -ErrorAction Stop)
  & $code.Source --install-extension Continue.continue
  if($LASTEXITCODE -ne 0){ throw 'Continue installation failed.' }
  & $code.Source --install-extension openai.chatgpt
  if($LASTEXITCODE -ne 0){ throw 'Codex extension installation failed.' }
}
New-Item -ItemType Directory -Path $appRoot -Force | Out-Null
& $dotnet.Source publish (Join-Path $packageRoot 'dotnet\LocalBrain.ClientHost\LocalBrain.ClientHost.csproj') -c Release -r win-x64 --self-contained false -o $appRoot
if($LASTEXITCODE -ne 0){ throw 'LocalBrain client publish failed.' }
if($null -eq $CertificatePassword){ $CertificatePassword=Read-Host 'Client certificate password' -AsSecureString }
$resolvedPfx=[IO.Path]::GetFullPath($CertificatePfx)
$certificate=Import-PfxCertificate -FilePath $resolvedPfx -CertStoreLocation 'Cert:\CurrentUser\My' -Password $CertificatePassword -Exportable:$false
if($RemovePfxAfterImport){ Remove-Item -LiteralPath $resolvedPfx -Force }
$ca=Join-Path $packageRoot 'certs\ca.pem'
if(-not (Test-Path -LiteralPath $ca)){ throw 'certs\ca.pem is missing from the transfer package.' }
Import-Certificate -FilePath $ca -CertStoreLocation 'Cert:\CurrentUser\Root' | Out-Null
$settings=$settings.Replace('__CLIENT_CERT_THUMBPRINT__',$certificate.Thumbprint)
[IO.File]::WriteAllText((Join-Path $appRoot 'clientsettings.json'),$settings,[Text.UTF8Encoding]::new($false))
$continueDir=Join-Path $env:USERPROFILE '.continue'; New-Item -ItemType Directory -Path $continueDir -Force | Out-Null
$target=Join-Path $continueDir 'config.yaml'; if(Test-Path $target){ Copy-Item $target ($target+'.backup-'+(Get-Date -Format yyyyMMdd-HHmmss)) }
$exe=(Join-Path $appRoot 'localbrain.exe') -replace '\\','/'
$yaml=(Get-Content -LiteralPath (Join-Path $packageRoot 'continue-template.yaml') -Raw).Replace('__LOCALBRAIN_EXE__',$exe).Replace('__LOOPBACK_HOST__',[Net.IPAddress]::Loopback.IPAddressToString).Replace('__BRIDGE_PORT__',[string]$parsedSettings.bridgePort)
[IO.File]::WriteAllText($target,$yaml,[Text.UTF8Encoding]::new($false))
try {
  $action=New-ScheduledTaskAction -Execute (Join-Path $appRoot 'localbrain.exe') -Argument 'serve' -WorkingDirectory $appRoot
  $trigger=New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
  $taskSettings=New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
  Register-ScheduledTask -TaskName 'LocalBrain Client Host' -Action $action -Trigger $trigger -Settings $taskSettings -Description 'VS Code bridge, heartbeat, SecondBrain MCP, and Codex review runner' -Force | Out-Null
  Start-ScheduledTask -TaskName 'LocalBrain Client Host'
} catch {
  $startup=Join-Path ([Environment]::GetFolderPath('Startup')) 'LocalBrain-Client.cmd'
  ('@echo off'+[Environment]::NewLine+'start "" /min "'+(Join-Path $appRoot 'localbrain.exe')+'" serve') | Set-Content -LiteralPath $startup -Encoding ascii
  Start-Process -FilePath (Join-Path $appRoot 'localbrain.exe') -ArgumentList 'serve' -WorkingDirectory $appRoot -WindowStyle Hidden
  Write-Warning 'Task Scheduler was unavailable; installed an equivalent per-user Startup entry.'
}
if($InitialWorkspace){
  & (Join-Path $appRoot 'localbrain.exe') register-project $InitialWorkspace
  if($LASTEXITCODE -ne 0){ throw 'Initial Git workspace registration failed.' }
}
Write-Host 'LocalBrain client is installed. Restart VS Code, open Continue, and select Agent mode.'
