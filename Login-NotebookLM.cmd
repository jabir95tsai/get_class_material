@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if not exist "%USERPROFILE%\.ntu-cool-gcm\notebooklm\venv\Scripts\notebooklm.exe" (
  echo Please double-click Install-NotebookLM.cmd first.
  pause
  exit /b 1
)
if not defined NOTEBOOKLM_HOME set "NOTEBOOKLM_HOME=%CD%\.secrets\notebooklm-api"
echo Sign in yourself in the separate Chrome window. No existing browser cookies are extracted.
echo If Google refuses the login, close the window and stop.
"%USERPROFILE%\.ntu-cool-gcm\notebooklm\venv\Scripts\notebooklm.exe" login --browser chrome --browser-timeout 300
set "login_result=%errorlevel%"
pause
exit /b %login_result%
