Option Explicit
Dim shell, files, root, mode, command, result
Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")
root = files.GetParentFolderName(WScript.ScriptFullName)
mode = "daily"
If WScript.Arguments.Count > 1 Then WScript.Quit 2
If WScript.Arguments.Count = 1 Then mode = WScript.Arguments(0)
If mode <> "daily" And mode <> "immediate" Then WScript.Quit 2
command = """" & shell.ExpandEnvironmentStrings("%SystemRoot%") & "\System32\WindowsPowerShell\v1.0\powershell.exe"" -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File """ & root & "\Run-DraftReviewer.ps1"" -Mode " & mode
result = shell.Run(command, 0, True)
WScript.Quit result
