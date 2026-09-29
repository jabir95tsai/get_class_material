@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
py -3 -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>&1
if not errorlevel 1 (
  py -3 -X utf8 "%~dp0tools\install_notebooklm.py"
  goto finish
)
python -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>&1
if not errorlevel 1 (
  python -X utf8 "%~dp0tools\install_notebooklm.py"
  goto finish
)
"%USERPROFILE%\miniconda3\python.exe" -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>&1
if not errorlevel 1 (
  "%USERPROFILE%\miniconda3\python.exe" -X utf8 "%~dp0tools\install_notebooklm.py"
  goto finish
)
echo Python 3.11+ is required. Install Python, then double-click this file again.
echo https://www.python.org/downloads/windows/
pause
exit /b 1
:finish
set "install_result=%errorlevel%"
if not "%install_result%"=="0" echo Installation failed. Please keep the error message above.
pause
exit /b %install_result%
