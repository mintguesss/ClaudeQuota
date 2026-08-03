' ClaudeQuota launcher - starts widget.py with pythonw (no console window).
' NOTE: keep this file ASCII-only. WScript parses .vbs as ANSI, so UTF-8
' Chinese comments here would break parsing and the script silently fails.
Option Explicit
Dim fso, sh, appDir, pyw
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")

appDir = fso.GetParentFolderName(WScript.ScriptFullName)
pyw = sh.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Programs\Python\Python39\pythonw.exe"
If Not fso.FileExists(pyw) Then pyw = "pythonw.exe"

sh.CurrentDirectory = appDir
sh.Run """" & pyw & """ """ & appDir & "\widget.py""", 0, False
