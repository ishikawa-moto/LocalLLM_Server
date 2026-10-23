#Requires -Version 7.2
$ErrorActionPreference='Stop'
$p=Join-Path (Split-Path $PSScriptRoot -Parent) 'Manage-LocalBrain.ps1'
$t=$null;$e=$null;$ast=[Management.Automation.Language.Parser]::ParseFile($p,[ref]$t,[ref]$e)
if($e.Count){throw 'SYNTAX_ERROR'}
$switch=@($ast.FindAll({param($n)$n -is [Management.Automation.Language.SwitchStatementAst]},$false))
if($switch.Count -ne 1){throw 'MANAGEMENT_ROUTING_SCOPE_CHANGED'}
$taskRoot='C:\public-fixture-data';$taskCodeRoot='C:\public-fixture-code'
function Get-Content {param($LiteralPath,[switch]$Raw) return '{"executable":"public-python"}'}
function Start-Owned($name,$executable,[string[]]$arguments){$script:observed=@{role=$name;executable=$executable;arguments=$arguments}}
$checks=0
foreach($Action in @('StartBrain','StartProxy','StartGateway')){
 $script:observed=$null
 . ([ScriptBlock]::Create($switch[0].Extent.Text))
 if(-not $script:observed -or $script:observed.executable -ne 'public-python'){throw 'UNEXPECTED_START_ROUTING'}
 $expected=if($Action -eq 'StartBrain'){'src\localbrain.py'}elseif($Action -eq 'StartProxy'){'src\codex_proxy.py'}else{'src\control_gateway.py'}
 if($script:observed.arguments[0] -ne (Join-Path $taskCodeRoot $expected)){throw 'CHILD_SOURCE_NOT_FROZEN'}
 if($Action -eq 'StartBrain' -and ($script:observed.arguments.Count -ne 4 -or $script:observed.arguments[1] -ne '--root' -or $script:observed.arguments[2] -ne $taskRoot -or $script:observed.arguments[3] -ne 'serve')){throw 'BRAIN_DATA_ROOT_NOT_STABLE'}
 $checks++
}
@{status='PASS';checks=$checks;management_sha256=(Microsoft.PowerShell.Utility\Get-FileHash -LiteralPath $p).Hash.ToLowerInvariant();production_operations=0;process_starts=0;network_calls=0;model_requests=0}|ConvertTo-Json
