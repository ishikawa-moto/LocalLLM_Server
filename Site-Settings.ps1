# Deployment ports remain in ignored local settings.
function Get-LocalBrainPort([string]$Service, [string]$Root=$PSScriptRoot) {
    $path=Join-Path $Root 'config\private-site.json'
    $settings=Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
    $value=$settings.PSObject.Properties[$Service+'_port'].Value
    if($value -isnot [int] -and $value -isnot [long]){throw "Configure numeric ${Service}_port in config/private-site.json"}
    if($value -le 0 -or $value -gt [UInt16]::MaxValue){throw "Invalid ${Service}_port in config/private-site.json"}
    return [int]$value
}
$LocalBrainLoopback=[Net.IPAddress]::Loopback.IPAddressToString
