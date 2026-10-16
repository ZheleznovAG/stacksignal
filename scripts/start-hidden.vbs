Option Explicit

Dim shell, files, scriptDir, supervisor, command
Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")
scriptDir = files.GetParentFolderName(WScript.ScriptFullName)
supervisor = files.BuildPath(scriptDir, "supervisor.ps1")
command = "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File " & Chr(34) & supervisor & Chr(34)
WScript.Quit shell.Run(command, 0, True)
