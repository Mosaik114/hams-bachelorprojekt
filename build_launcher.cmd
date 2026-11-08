@echo off
"%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe" /nologo /target:winexe /reference:System.Windows.Forms.dll "/out:%~dp0Hams.exe" "/win32icon:%~dp0wisper\assets\branding\hams.ico" "%~dp0launch_hams.cs"
exit /b %errorlevel%
