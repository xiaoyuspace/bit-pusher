@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -c "import sys, pip; sys.exit(sys.version_info < (3,10))" >nul 2>&1
  if not errorlevel 1 goto install
  echo Repairing the virtual environment for this computer...
)
set "PYEXE=py"
set "PYARGS=-3"
py -3 -c "import sys;sys.exit(sys.version_info < (3,10))" >nul 2>&1
if errorlevel 1 (
  set "PYEXE=python"
  set "PYARGS="
  python -c "import sys;sys.exit(sys.version_info < (3,10))" >nul 2>&1
  if errorlevel 1 (
    echo Install Python 3.10 or newer, then run setup.cmd again.
    echo https://www.python.org/downloads/windows/
    pause
    exit /b 1
  )
)
"%PYEXE%" %PYARGS% -m venv ".venv"
if errorlevel 1 goto failed

:install
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -c "from playwright.sync_api import sync_playwright"
if errorlevel 1 goto failed
echo Setup complete. Double-click start.cmd to run Bit Publisher.
pause
exit /b 0

:failed
echo Setup failed. Review the error above and run setup.cmd again.
pause
exit /b 1
