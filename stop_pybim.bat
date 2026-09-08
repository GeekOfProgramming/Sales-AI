@echo off
chcp 65001 >nul
title Stop pyBIM-LLM Server
cd /d "%~dp0"

echo ======================================================================
echo           🛑 Stopping pyBIM-LLM Gateway Server
echo ======================================================================
echo.

echo Looking for processes listening on port 8000...
set FOUND=0
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8000 ^| findstr LISTENING') do (
    echo Stopping PID %%a...
    taskkill /F /PID %%a >nul 2>&1
    set FOUND=1
)

if "%FOUND%"=="1" (
    echo [OK] pyBIM-LLM server stopped successfully.
) else (
    echo [INFO] No server was running on port 8000.
)

echo.
timeout /t 3 /nobreak >nul
