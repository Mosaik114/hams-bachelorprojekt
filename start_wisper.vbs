' Startet Wisper im Hintergrund ohne Konsolenfenster.
' Pfade werden relativ zum Skript aufgelöst, damit das Projekt verschoben werden kann.
' Bevorzugt pythonw.exe aus .venv, fällt sonst auf system-pythonw.exe zurück.

Set fso = CreateObject("Scripting.FileSystemObject")
Set WshShell = CreateObject("WScript.Shell")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
mainPy = fso.BuildPath(scriptDir, "wisper\main.py")
venvPythonw = fso.BuildPath(scriptDir, ".venv\Scripts\pythonw.exe")
venvPython = fso.BuildPath(scriptDir, ".venv\Scripts\python.exe")

If fso.FileExists(venvPythonw) Then
    pythonExe = venvPythonw
ElseIf fso.FileExists(venvPython) Then
    pythonExe = venvPython
Else
    pythonExe = "pythonw.exe"
End If

' Arbeitsverzeichnis = Projekt-Root, damit relative Pfade unabhaengig
' vom Startkontext (Autostart, Explorer, Konsole) stimmen.
WshShell.CurrentDirectory = scriptDir

WshShell.Run """" & pythonExe & """ """ & mainPy & """", 0, False
