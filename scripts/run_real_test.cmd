@echo off
chcp 65001 >nul
title AIWeb Real Test
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_real_test.ps1"
echo.
pause
