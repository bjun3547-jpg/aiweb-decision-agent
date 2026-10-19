@echo off
chcp 65001 >nul
setlocal

rem ============================================================
rem  Run the decision Agent from anywhere.
rem  Always uses the project venv (NOT the system python).
rem
rem    run_agent.cmd                       -> built-in demo text
rem    run_agent.cmd "your own request"    -> custom text
rem ============================================================

set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"
set "PY=%ROOT%\.venv\Scripts\python.exe"

if not exist "%PY%" (
    echo [ERROR] venv not found: %PY%
    echo         Run scripts\run_check.cmd first to create it.
    exit /b 1
)

cd /d "%ROOT%\backend"

"%PY%" -m app.agent %*

echo.
endlocal
