' Runs run-bridge.bat with no window at all.
'
' A scheduled task pointing straight at the .bat flashes a console on every logon,
' and a console that exists is a console someone eventually closes. The second
' argument 0 hides the window; False means do not wait for it to finish.

Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
shell.Run """" & here & "\run-bridge.bat""", 0, False
