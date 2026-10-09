param([string]$ClientAddress,[string]$ServerAddress)
$sitePath=Join-Path $PSScriptRoot 'config\private-site.json'
. (Join-Path $PSScriptRoot 'Site-Settings.ps1')
if(Test-Path -LiteralPath $sitePath){
  $site=Get-Content -LiteralPath $sitePath -Raw | ConvertFrom-Json
  if(-not $ClientAddress){$ClientAddress=$site.client_address}
  if(-not $ServerAddress){$ServerAddress=$site.server_address}
}
if(-not $ClientAddress-or-not $ServerAddress){throw 'Specify ClientAddress and ServerAddress, or configure private site settings.'}
$ErrorActionPreference = 'Stop'

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this script from an elevated PowerShell.'
}

$shareName = 'LocalBrainTransfer'
$sharePath = 'D:\LocalBrain\transfer'
$firewallName = if($site.temporary_transfer_firewall_name){$site.temporary_transfer_firewall_name}else{'LocalBrain temporary client transfer from ClientPC'}
$authenticatedUsers = ([Security.Principal.SecurityIdentifier]'S-1-5-11').Translate([Security.Principal.NTAccount]).Value

if (-not (Test-Path -LiteralPath $sharePath)) {
    throw "Transfer directory does not exist: $sharePath"
}

$existing = Get-SmbShare -Name $shareName -ErrorAction SilentlyContinue
if ($existing) {
    if ($existing.Path -ne $sharePath) {
        throw "An SMB share named $shareName already points to a different path."
    }
} else {
    New-SmbShare -Name $shareName -Path $sharePath -ReadAccess $authenticatedUsers -Description 'Temporary encrypted LocalBrain client package transfer to ClientPC' | Out-Null
}

Get-NetFirewallRule -DisplayName $firewallName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName $firewallName -Direction Inbound -Protocol TCP -LocalPort (Get-LocalBrainPort smb) -RemoteAddress $ClientAddress -Action Allow -Profile Private | Out-Null

Write-Host "Temporary read-only share ready: \\$ServerAddress\$shareName"
