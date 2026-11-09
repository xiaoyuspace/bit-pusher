@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" goto needs_setup
".venv\Scripts\python.exe" -c "import sys; from playwright.sync_api import sync_playwright; sys.exit(sys.version_info < (3,10))" >nul 2>&1
if errorlevel 1 goto needs_setup
echo Bit Publisher: http://127.0.0.1:8766
echo Keep BitBrowser running with Local API on port 54345.
echo Keep this console open while publishing.
".venv\Scripts\python.exe" -X utf8 server.py
set "SERVER_EXIT=%ERRORLEVEL%"
pause
exit /b %SERVER_EXIT%

:needs_setup
echo The Python environment is missing or incomplete. Run setup.cmd on this computer first.
pause
exit /b 1
