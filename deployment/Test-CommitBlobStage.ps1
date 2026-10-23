#Requires -Version 7.2
$ErrorActionPreference='Stop'
$path=Join-Path $PSScriptRoot 'Prepare-GatewayRelease.ps1'
$t=$null;$e=$null;$ast=[Management.Automation.Language.Parser]::ParseFile($path,[ref]$t,[ref]$e)
if($e.Count){throw 'SYNTAX_ERROR'}
$definition=@($ast.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'GitBytes'},$false))
if($definition.Count -ne 1){throw 'FIXED_FUNCTION_UNAVAILABLE'}
. ([ScriptBlock]::Create($definition[0].Extent.Text))
$parent=[IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')
$fixture=Join-Path $parent ('localbrain-blob-public-'+[guid]::NewGuid().ToString('N'))
$null=New-Item -ItemType Directory -Path $fixture
$pass=$false
try{
 & git init --quiet $fixture;if($LASTEXITCODE){throw 'FIXTURE_INIT_FAILED'}
 $g=@('--no-optional-locks','-c',('safe.directory='+$fixture.Replace('\','/')),'-c','core.autocrlf=false','-c','core.hooksPath=NUL','-c','user.name=Public Fixture','-c','user.email=fixture@example.invalid','-C',$fixture)
 $text=[Text.Encoding]::UTF8.GetBytes(('PUBLIC fixed'+[char]10))
 $binary=[byte[]]@(0,255,13,10,128,65)
 [IO.File]::WriteAllBytes((Join-Path $fixture 'fixed.txt'),$text)
 [IO.File]::WriteAllBytes((Join-Path $fixture 'binary.bin'),$binary)
 & git @g add -- fixed.txt binary.bin;if($LASTEXITCODE){throw 'FIXTURE_ADD_FAILED'}
 & git @g commit --quiet -m fixture;if($LASTEXITCODE){throw 'FIXTURE_COMMIT_FAILED'}
 $commit=(@(& git @g rev-parse HEAD))[0]
 function HashBytes([byte[]]$b){[Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($b))}
 if((HashBytes (GitBytes @('show',($commit+':fixed.txt')))) -ne (HashBytes $text)){throw 'TEXT_BLOB_CHANGED'}
 if((HashBytes (GitBytes @('show',($commit+':binary.bin')))) -ne (HashBytes $binary)){throw 'BINARY_BLOB_TRANSCODED'}
 [IO.File]::WriteAllText((Join-Path $fixture 'fixed.txt'),('PUBLIC modified checkout'+[char]13+[char]10))
 if((HashBytes (GitBytes @('show',($commit+':fixed.txt')))) -ne (HashBytes $text)){throw 'WORKTREE_RACE_ACCEPTED'}
 & git @g add -- fixed.txt;& git @g commit --quiet -m next;if($LASTEXITCODE){throw 'SECOND_FIXTURE_COMMIT_FAILED'}
 if((HashBytes (GitBytes @('show',($commit+':fixed.txt')))) -ne (HashBytes $text)){throw 'MUTABLE_HEAD_USED'}
 $pass=$true
 @{status='PASS';checks=4;stage_source='Exact immutable commit blobs, including binary';prepare_script_sha256=(Get-FileHash -LiteralPath $path).Hash.ToLowerInvariant();production_writes=0;network_calls=0;model_requests=0}|ConvertTo-Json
}finally{
 if($pass){
  $resolved=[IO.Path]::GetFullPath($fixture)
  if(-not $resolved.StartsWith($parent+'\localbrain-blob-public-',[StringComparison]::OrdinalIgnoreCase) -or $resolved -eq $parent){throw 'FIXTURE_CLEANUP_SCOPE'}
  Remove-Item -LiteralPath $resolved -Recurse -Force
 }
}
