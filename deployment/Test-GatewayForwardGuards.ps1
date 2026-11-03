#Requires -Version 7.2
$ErrorActionPreference='Stop'
$path=Join-Path $PSScriptRoot 'Apply-GatewayForward.ps1'
$tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseFile($path,[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'PARSE_FAILED'}
$names=@('SuspendTask','StopOnce','UpdateTask','EnableNewTask','StartOnce','RestoreTask')
foreach($name in $names){
 $function=@($ast.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq $name},$true))
 if($function.Count -ne 1){throw 'FUNCTION_MISSING'}
 . ([ScriptBlock]::Create($function[0].Extent.Text))
 try{& $name 'chat';throw 'CHAT_MUTATION_ACCEPTED'}catch{if($_.Exception.Message -cne 'GATEWAY_ONLY_MUTATION_REQUIRED'){throw}}
}
$bad=$ast.FindAll({param($n)
 $n -is [Management.Automation.Language.CommandAst] -and $n.GetCommandName() -cin $names -and
 $n.CommandElements.Count -gt 1 -and $n.CommandElements[1].Extent.Text -ceq "'chat'"
},$true)
if(@($bad).Count){throw 'CHAT_MUTATION_CALL_FOUND'}
# Exercise the actual RestoreTask XML transformation using real COM definitions,
# but replace registration with a recording object. Never obtain a live task.
foreach($name in @('HashBytes','StringHash','HandleBirth')){
 $definition=$ast.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq $name},$true)[0]
 . ([ScriptBlock]::Create($definition.Extent.Text))
}
$self=[Diagnostics.Process]::GetCurrentProcess()
try{
 $cim=Get-CimInstance -Query "SELECT CreationDate FROM Win32_Process WHERE ProcessId=$($self.Id)"
 if((HandleBirth $self) -cne $cim.CreationDate.ToFileTimeUtc().ToString([Globalization.CultureInfo]::InvariantCulture)){throw 'PROCESS_TIME_REPRESENTATION_MISMATCH'}
}finally{$self.Dispose()}
$service=New-Object -ComObject Schedule.Service;$service.Connect()
$old=$service.NewTask(0)
$script:fixtureSecurity='O:SYG:SYD:(A;;FA;;;SY)(A;;FA;;;BA)'
$old.RegistrationInfo.SecurityDescriptor=$script:fixtureSecurity
$old.Principal.UserId='SYSTEM';$old.Principal.LogonType=5
$old.Settings.Enabled=$true
$action=$old.Actions.Create(0);$action.Path='C:\public-fixture\python.exe';$action.WorkingDirectory='C:\public-fixture';$action.Arguments='OLD_FIXED_ARGUMENTS'
$script:fixtureBinding=@{name='public-fixture-not-registered';old_arguments=$action.Arguments;xml_sha256=StringHash $old.XmlText}
$new=$service.NewTask(0);$new.XmlText=$old.XmlText;$new.Actions.Item(1).Arguments='NEW_FIXED_ARGUMENTS';$new.Settings.Enabled=$false
$script:fixtureNewXml=$new.XmlText
$script:fixtureTask=[pscustomobject]@{State=1;Xml=$script:fixtureNewXml}
$script:fixtureTask|Add-Member ScriptMethod GetInstances {param($flags) return @()}
$script:fixtureTask|Add-Member ScriptMethod GetSecurityDescriptor {param($flags) return $script:fixtureSecurity}
$record=@{new_tasks=@{gateway=@{definition_sha256=StringHash $script:fixtureNewXml}};disabled_tasks=@{}}
function Binding([string]$role){return $script:fixtureBinding}
function AssertDefinition([string]$role,[bool]$new){
 $copy=$service.NewTask(0);$copy.XmlText=$script:fixtureTask.Xml
 $expected=if($new){$record.new_tasks.gateway.definition_sha256}else{$record.disabled_tasks.gateway}
 if((StringHash $copy.XmlText) -cne $expected){throw 'FIXTURE_DEFINITION_DRIFT'}
 return $script:fixtureTask
}
function Step([string]$phase){}
$script:fixtureRegistrations=0
$folder=[pscustomobject]@{}
$folder|Add-Member ScriptMethod RegisterTaskDefinition {param($name,$definition,$flags,$user,$password,$logon,$security)
 if($flags -ne 52 -or $user -cne 'SYSTEM' -or $security -cne $script:fixtureSecurity){throw 'REGISTRATION_BOUNDARY_DRIFT'}
 $script:fixtureRegistrations++;$script:fixtureTask.Xml=$definition.XmlText
}
RestoreTask 'gateway'
$restored=$service.NewTask(0);$restored.XmlText=$script:fixtureTask.Xml;$restored.Settings.Enabled=$true
if((StringHash $restored.XmlText) -cne $script:fixtureBinding.xml_sha256 -or $script:fixtureRegistrations -ne 1){throw 'RESTORE_XML_ROUNDTRIP_FAILED'}
$script:fixtureTask.Xml=$script:fixtureNewXml;$script:fixtureBinding.xml_sha256='f'*64
try{RestoreTask 'gateway';throw 'WRONG_OLD_XML_ACCEPTED'}catch{if($_.Exception.Message -cne 'RESTORE_ORIGINAL_XML_MISMATCH'){throw}}
if($script:fixtureRegistrations -ne 1){throw 'MISMATCH_REGISTERED'}
@{status='GATEWAY_ONLY_ROLE_AND_RESTORE_GUARDS_PASSED';mutation_functions=6;process_time_representation=$true;restore_xml_roundtrip=$true;mismatch_rejected_before_registration=$true;production_operations=0}|ConvertTo-Json
