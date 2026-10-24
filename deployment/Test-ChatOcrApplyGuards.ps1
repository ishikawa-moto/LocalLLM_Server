#Requires -Version 7.2
# Public, in-memory Scheduler definitions only. Never obtains/registers a real task.
$ErrorActionPreference='Stop'
$path=Join-Path $PSScriptRoot 'Apply-ChatOcr.ps1';$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($path,[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'APPLY_PARSE_FAILED'}
$wanted=@('HashBytes','StringHash','Binding','Task','AssertDefinition','SuspendTask','UpdateTask','EnableNewTask')
foreach($name in $wanted){
 $found=@($ast.FindAll({param($node)$node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -ceq $name},$true))
 if($found.Count -ne 1){throw 'TEST_FUNCTION_NOT_UNIQUE'}
 . ([ScriptBlock]::Create($found[0].Extent.Text))
}
$phases=[Collections.Generic.List[string]]::new()
function Step([string]$value){$phases.Add($value)}
$service=New-Object -ComObject Schedule.Service;$service.Connect()
$security='O:BAG:BAD:(A;;FA;;;SY)(A;;FA;;;BA)'
$root='C:\PublicFixture\Data'
$intent=@{python='C:\PublicFixture\python.exe';entry_path='C:\PublicFixture\protected\chat_ocr_entry.py';
 activation_root='C:\PublicFixture\protected';spec_sha256=('a'*64);tasks=@()}
$record=@{disabled_tasks=@{};new_tasks=@{}}
$folder=[pscustomobject]@{Tasks=@{};Registrations=0}
$folder|Add-Member ScriptMethod GetTask {param($name)return $this.Tasks[$name]}
$folder|Add-Member ScriptMethod RegisterTaskDefinition {
 param($name,$definition,$flags,$user,$password,$logon,$sddl)
 if($flags -ne 52 -or $user -cne 'SYSTEM' -or $null -ne $password -or $logon -ne 5 -or $sddl -cne $script:security){throw 'REGISTER_SCOPE_BROKEN'}
 $this.Registrations++;$this.Tasks[$name].Definition=$definition
 return $this.Tasks[$name]
}
function Signature([string]$xml){
 $doc=[xml]$xml
 foreach($node in @($doc.SelectNodes('//*[local-name()="Actions"]/*/*'))){$null=$node.ParentNode.RemoveChild($node)}
 foreach($node in @($doc.SelectNodes('//*[local-name()="Enabled"]'))){$node.InnerText='true'}
 foreach($node in @($doc.SelectNodes('//*[local-name()="SecurityDescriptor"]'))){$null=$node.ParentNode.RemoveChild($node)}
 return $doc.OuterXml
}
$checks=1
foreach($role in @('chat','gateway')){
 $name=if($role -eq 'chat'){'LocalBrain Chat Web'}else{'LocalBrain Control Gateway'}
 $d=$service.NewTask(0);$d.RegistrationInfo.Author='Public fixture';$d.RegistrationInfo.Description='Do not register this task'
 $d.Principal.UserId='SYSTEM';$d.Principal.LogonType=5
 $d.Settings.Enabled=$true;$d.Settings.MultipleInstances=2;$d.Settings.RestartCount=3;$d.Settings.RestartInterval='PT1M'
 $null=$d.Triggers.Create(8);$action=$d.Actions.Create(0)
 $action.Path=$intent.python;$action.Arguments='-B "C:\PublicFixture\old.py"';$action.WorkingDirectory=$root
 $fixture=[pscustomobject]@{Definition=$d;Security=$security}
 $fixture|Add-Member ScriptProperty Xml {return $this.Definition.XmlText}
 $fixture|Add-Member ScriptProperty Enabled {return [bool]$this.Definition.Settings.Enabled} {param($value)$this.Definition.Settings.Enabled=$value}
 $fixture|Add-Member ScriptProperty State {if($this.Enabled){return 3}else{return 1}}
 $fixture|Add-Member ScriptMethod GetSecurityDescriptor {param($flags)return $this.Security}
 $fixture|Add-Member ScriptMethod GetInstances {param($flags)return @()}
 $folder.Tasks[$name]=$fixture
 $intent.tasks+=@{role=$role;name=$name;xml_sha256=StringHash $fixture.Xml;sddl_sha256=StringHash $security}
 $before=Signature $fixture.Xml
 $null=AssertDefinition $role $false
 SuspendTask $role
 if($fixture.Enabled -or $fixture.State -ne 1){throw 'SUSPEND_FAILED'};$checks++
 UpdateTask $role
 if($fixture.Enabled -or $fixture.Definition.Settings.RestartCount -ne 3 -or (Signature $fixture.Xml) -cne $before){throw 'UNAPPROVED_TASK_DELTA'};$checks++
 EnableNewTask $role
 if(-not $fixture.Enabled -or (Signature $fixture.Xml) -cne $before){throw 'ENABLE_CHANGED_POLICY'};$checks++
 $fixture.Definition.Settings.RestartCount=2
 $rejected=$false;try{$null=AssertDefinition $role $true}catch{$rejected=$true}
 if(-not $rejected){throw 'WHOLE_DEFINITION_DRIFT_ACCEPTED'};$checks++
 $fixture.Definition.Settings.RestartCount=3
 $fixture.Security='O:BAG:BAD:(A;;FA;;;SY)'
 $rejected=$false;try{$null=AssertDefinition $role $true}catch{$rejected=$true}
 if(-not $rejected){throw 'SECURITY_DRIFT_ACCEPTED'};$checks++
}
if($folder.Registrations -ne 2){throw 'REGISTER_COUNT_BROKEN'};$checks++
@{status='PUBLIC_GUARDS_PASS';checks=$checks;real_tasks_obtained=0;real_tasks_registered=0;
  production_mutations=0;scope='real Scheduler in-memory canonicalization with mocked registered-task/folder operations'}|ConvertTo-Json
