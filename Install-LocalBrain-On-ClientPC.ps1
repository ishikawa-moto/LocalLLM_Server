param([string]$ExpectedClientAddress)
if(-not $ExpectedClientAddress){$ExpectedClientAddress=Read-Host 'Expected IPv4 address of this ClientPC client'}
$ErrorActionPreference = 'Stop'

$localAddresses = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop | ForEach-Object IPAddress)
if ($ExpectedClientAddress -notin $localAddresses) {
    throw 'This installer must run on the configured ClientPC client address.'
}

$sourceRoot = $PSScriptRoot
$package = Get-ChildItem -LiteralPath $sourceRoot -Filter 'LocalBrain-Client-*.zip' -File |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
if (-not $package) {
    throw "No LocalBrain-Client-*.zip was found in $sourceRoot"
}

$installerRoot = Join-Path $env:LOCALAPPDATA 'LocalBrain\installer'
$runRoot = Join-Path $installerRoot (Get-Date -Format 'yyyyMMdd-HHmmss')
$resolvedInstallerRoot = [IO.Path]::GetFullPath($installerRoot).TrimEnd('\') + '\'
$resolvedRunRoot = [IO.Path]::GetFullPath($runRoot).TrimEnd('\') + '\'
if (-not $resolvedRunRoot.StartsWith($resolvedInstallerRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Resolved installer directory escaped the LocalBrain installer root.'
}

New-Item -ItemType Directory -Path $runRoot -Force | Out-Null
$localZip = Join-Path $runRoot $package.Name
Copy-Item -LiteralPath $package.FullName -Destination $localZip
Expand-Archive -LiteralPath $localZip -DestinationPath $runRoot -Force

$clientRoot = Join-Path $runRoot 'LocalBrain-Client'
$setup = Join-Path $clientRoot 'Setup-Client.ps1'
$pfx = Join-Path $clientRoot 'certs\client.pfx'
if (-not (Test-Path -LiteralPath $setup) -or -not (Test-Path -LiteralPath $pfx)) {
    throw 'The copied client package is incomplete.'
}

Write-Host 'Enter the temporary PFX password, then the initial Git workspace path.'
try {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $setup -CertificatePfx $pfx -RemovePfxAfterImport
    if ($LASTEXITCODE -ne 0) {
        throw "Setup-Client.ps1 failed with exit code $LASTEXITCODE"
    }
}
finally {
    if (Test-Path -LiteralPath $pfx) { Remove-Item -LiteralPath $pfx -Force }
}

$task = Get-ScheduledTask -TaskName 'LocalBrain Client Host' -ErrorAction SilentlyContinue
$startup = Join-Path ([Environment]::GetFolderPath('Startup')) 'LocalBrain-Client.cmd'
$app = Join-Path $env:LOCALAPPDATA 'LocalBrain\app\localbrain.exe'
$settings = Join-Path $env:LOCALAPPDATA 'LocalBrain\app\clientsettings.json'
$continueConfig = Join-Path $env:USERPROFILE '.continue\config.yaml'
if (-not (Test-Path -LiteralPath $app) -or -not (Test-Path -LiteralPath $settings) -or -not (Test-Path -LiteralPath $continueConfig)) {
    throw 'Client files or Continue configuration were not installed.'
}
if (-not $task -and -not (Test-Path -LiteralPath $startup)) {
    throw 'Neither the LocalBrain scheduled task nor Startup fallback was installed.'
}

Start-Sleep -Seconds 3
$clientSettings=Get-Content -LiteralPath $settings -Raw | ConvertFrom-Json
$loopback=[Net.IPAddress]::Loopback.IPAddressToString
$listener = Get-NetTCPConnection -LocalAddress $loopback -LocalPort $clientSettings.bridgePort -State Listen -ErrorAction SilentlyContinue
if (-not $listener) {
    throw 'LocalBrain Client Host was installed but is not listening on the configured loopback Bridge endpoint.'
}

Remove-Item -LiteralPath $localZip -Force
Remove-Item -LiteralPath $clientRoot -Recurse -Force
Write-Host 'ClientPC installation verified: LocalBrain Client Host is listening on the configured loopback Bridge endpoint.'
