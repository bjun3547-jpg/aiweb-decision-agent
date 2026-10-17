@echo off
chcp 65001 >nul
title AIWeb Env Check
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0check_env.ps1"
echo.
pause
