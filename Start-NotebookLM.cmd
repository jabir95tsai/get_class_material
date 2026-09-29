@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if not exist "%USERPROFILE%\.ntu-cool-gcm\notebooklm\venv\Scripts\python.exe" (
  echo Please run Install-NotebookLM.cmd first.
  exit /b 1
)
if not defined NOTEBOOKLM_HOME set "NOTEBOOKLM_HOME=%CD%\.secrets\notebooklm-api"
echo "%*" | findstr /i /c:"--dry-run" /c:"--help" /c:"-h" >nul
if errorlevel 1 (
  "%USERPROFILE%\.ntu-cool-gcm\notebooklm\venv\Scripts\notebooklm.exe" auth check --test >nul 2>&1
  if errorlevel 1 (
    echo [NotebookLM] 登入已過期或尚未登入，正在跳出 Chrome 登入視窗...
    echo 請在視窗中完成 Google 登入；登入完成後視窗會自動關閉並繼續匯入。
    "%USERPROFILE%\.ntu-cool-gcm\notebooklm\venv\Scripts\notebooklm.exe" login --browser chrome --browser-timeout 300
    if errorlevel 1 (
      echo [NotebookLM] 登入未完成，匯入停止。
      exit /b 1
    )
    echo [NotebookLM] 登入成功，繼續執行自動匯入...
  )
)
"%USERPROFILE%\.ntu-cool-gcm\notebooklm\venv\Scripts\python.exe" -X utf8 -m ntu_cool_materials notebooklm %*
set "import_result=%errorlevel%"
exit /b %import_result%

