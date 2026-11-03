@echo off
chcp 65001 >nul
title SalesAI Local Server
cd /d "%~dp0"

:: Delegate startup cleanly to PowerShell launcher to ensure secure environment
:: propagation (DPAPI credentials such as GEMINI_API_KEY) and single-process lifecycle.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_sales_ai.ps1" %*

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] SalesAI PowerShell launcher exited with code %ERRORLEVEL%.
    pause
    exit /b %ERRORLEVEL%
)

exit /b 0
