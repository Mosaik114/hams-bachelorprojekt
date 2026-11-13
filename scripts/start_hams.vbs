' Starts Hams without a console window.
' Paths are resolved relative to this script (scripts\ inside the project), so the project can be moved.
' Prefers pythonw.exe from .venv, otherwise falls back to the system pythonw.exe.

Set fso = CreateObject("Scripting.FileSystemObject")
Set WshShell = CreateObject("WScript.Shell")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
projectDir = fso.GetParentFolderName(scriptDir)
mainPy = fso.BuildPath(projectDir, "hams\main.py")
venvPythonw = fso.BuildPath(projectDir, ".venv\Scripts\pythonw.exe")
venvPython = fso.BuildPath(projectDir, ".venv\Scripts\python.exe")

If fso.FileExists(venvPythonw) Then
    pythonExe = venvPythonw
ElseIf fso.FileExists(venvPython) Then
    pythonExe = venvPython
Else
    pythonExe = "pythonw.exe"
End If

' Working directory = project root, so relative paths are the same no matter
' how Hams was started (autostart, Explorer, console).
WshShell.CurrentDirectory = projectDir

' Arguments are passed on: the autostart entry adds --background (start in the tray). Started by hand
' (double click), Hams shows its window.
args = ""
For Each arg In WScript.Arguments
    args = args & " """ & arg & """"
Next

WshShell.Run """" & pythonExe & """ """ & mainPy & """" & args, 0, False
