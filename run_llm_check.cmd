@echo off
chcp 65001 >nul
setlocal

rem ============================================================
rem  Self-check for the LLM layer (backend/app/llm.py).
rem  Verifies: .env loaded / API key valid / network / schema guard.
rem
rem    run_llm_check.cmd            -> full check
rem    run_llm_check.cmd --text     -> text generation only
rem    run_llm_check.cmd --json     -> structured output only
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

"%PY%" app\llm.py %*

echo.
pause
endlocal
