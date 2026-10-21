[CmdletBinding()]
param(
    [string]$OutputDirectory = (Join-Path $PSScriptRoot 'private-runtime'),
    [ValidateRange(0,65535)][int]$Port = 0
)
$ErrorActionPreference = 'Stop'
# Preparation only: no Docker, services, production files, firewall, or model changes.
$destination = [IO.Path]::GetFullPath($OutputDirectory)
if (Test-Path -LiteralPath $destination) {
    throw 'Preparation directory already exists. Inspect and reuse it; do not overwrite it.'
}
$listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback,$Port)
try {
    $listener.Start()
    $selectedPort = ([Net.IPEndPoint]$listener.LocalEndpoint).Port
} finally { $listener.Stop() }
$secretBytes = New-Object byte[] 32
$rng = [Security.Cryptography.RandomNumberGenerator]::Create()
try { $rng.GetBytes($secretBytes) } finally { $rng.Dispose() }
$secret = [BitConverter]::ToString($secretBytes).Replace('-','').ToLowerInvariant()
New-Item -ItemType Directory -Path $destination -ErrorAction Stop | Out-Null
$encoding = New-Object Text.UTF8Encoding $false
[IO.File]::WriteAllText((Join-Path $destination 'private-searxng.env'),
    "SEARXNG_PORT=$selectedPort`nSEARXNG_SECRET=$secret`n",$encoding)
$config = [ordered]@{enabled=$true;provider='free_cascade';
    searxng_url=('http://'+[Net.IPAddress]::Loopback.ToString()+':'+$selectedPort+'/search')}
[IO.File]::WriteAllText((Join-Path $destination 'private-chat-web-search.json'),
    ($config | ConvertTo-Json -Compress),$encoding)
$secret = $null
[ordered]@{status='prepared_only';directory=$destination;port=$selectedPort;
    docker_started=$false;production_changed=$false} | ConvertTo-Json -Compress
