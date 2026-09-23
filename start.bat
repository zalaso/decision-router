@echo off
rem Decision Router: creates .venv on first run, then opens the dashboard.
rem Extra arguments are passed to "serve", e.g. start.bat --port 9000
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" goto check
echo [1/2] Creating the Python environment (.venv)...
py -3.12 -m venv .venv >nul 2>&1
if not exist ".venv\Scripts\python.exe" py -3 -m venv .venv >nul 2>&1
if not exist ".venv\Scripts\python.exe" python -m venv .venv >nul 2>&1
if not exist ".venv\Scripts\python.exe" goto nopython

:check
".venv\Scripts\python.exe" -c "import sys; sys.exit(sys.version_info < (3, 12))"
if errorlevel 1 goto oldpython
".venv\Scripts\python.exe" -c "import decision_router.dashboard" >nul 2>&1
if not errorlevel 1 goto run
echo [2/2] Installing dependencies (first run only)...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -e .
if errorlevel 1 goto failed

:run
".venv\Scripts\python.exe" -m decision_router.cli serve --open %*
goto end

:nopython
echo Python 3.12 or newer is required: https://www.python.org/downloads/
goto failed
:oldpython
echo The .venv folder uses Python older than 3.12. Delete .venv and install Python 3.12+.
:failed
pause
exit /b 1
:end
endlocal
