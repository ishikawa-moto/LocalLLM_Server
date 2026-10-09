$ErrorActionPreference = 'Stop'

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this script from an elevated PowerShell.'
}

Get-SmbShare -Name 'LocalBrainTransfer' -ErrorAction SilentlyContinue | Remove-SmbShare -Force
$sitePath=Join-Path $PSScriptRoot 'config\private-site.json'
$site=if(Test-Path -LiteralPath $sitePath){Get-Content -LiteralPath $sitePath -Raw | ConvertFrom-Json}else{$null}
$firewallName=if($site.temporary_transfer_firewall_name){$site.temporary_transfer_firewall_name}else{'LocalBrain temporary client transfer from ClientPC'}
Get-NetFirewallRule -DisplayName $firewallName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
Write-Host 'Temporary LocalBrain transfer share and firewall rule were removed.'
