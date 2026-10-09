param([string]$OutputPath=(Join-Path 'D:\LocalBrain\transfer' ('LocalBrain-Client-'+(Get-Date -Format yyyyMMdd-HHmmss)+'.zip')))
$ErrorActionPreference='Stop'; $root=$PSScriptRoot
$dotnet=(Get-Command dotnet -ErrorAction Stop); $python=Join-Path $root '.venv\Scripts\python.exe'
& $dotnet.Source build (Join-Path $root 'client-package\dotnet\LocalBrain.ClientHost\LocalBrain.ClientHost.csproj') -c Release
if($LASTEXITCODE -ne 0){ throw 'Client build failed.' }
& $python (Join-Path $root 'src\export_client_package.py') $OutputPath
if($LASTEXITCODE -ne 0){ throw 'Encrypted client package creation failed.' }
$outputDirectory=Split-Path -Parent ([IO.Path]::GetFullPath($OutputPath))
Copy-Item -LiteralPath (Join-Path $root 'Install-LocalBrain-On-ClientPC.cmd') -Destination $outputDirectory -Force
Copy-Item -LiteralPath (Join-Path $root 'Install-LocalBrain-On-ClientPC.ps1') -Destination $outputDirectory -Force
Write-Host 'Copy the ZIP and ClientPC launcher to ClientPC over a trusted local medium or authenticated Windows share. Do not send the PFX password with the ZIP.'
