@echo off
rem Builds Hams.exe (opens the settings of the running Hams, or starts it) in the project root.
rem The launcher expects .venv\ and hams\main.py next to itself, so it goes one level above scripts\.
"%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe" /nologo /target:winexe /reference:System.Windows.Forms.dll "/out:%~dp0..\Hams.exe" "/win32icon:%~dp0..\hams\assets\branding\hams.ico" "%~dp0launch_hams.cs"
exit /b %errorlevel%
