Set WshShell = CreateObject("WScript.Shell")
Set oFSO = CreateObject("Scripting.FileSystemObject")
strPath = oFSO.GetParentFolderName(WScript.ScriptFullName)
WshShell.Run chr(34) & strPath & "\runner.bat" & Chr(34), 0
Set WshShell = Nothing
