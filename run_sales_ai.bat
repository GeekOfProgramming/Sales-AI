@echo off
chcp 65001 >nul
title SalesAI Local Server
cd /d "%~dp0"

echo =====================================
echo       SalesAI Local Server
echo =====================================
echo.

:: 1. Check Python Virtual Environment
echo [1/3] Checking Python Virtual Environment (.venv)...
if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment not found at: %~dp0.venv\Scripts\python.exe
    echo Run setup\install_requirements.bat to configure the environment.
    pause
    exit /b 1
)
echo [OK] Python virtual environment ready.

:: 2. Check Optional LLM Service (No auto-start or auto-download)
echo [2/3] Checking LLM status...
curl -s http://127.0.0.1:11434/api/tags >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [OK] Local Ollama service detected on port 11434.
) else (
    echo [INFO] Local Ollama not running (optional). SalesAI operates in deterministic mode.
    echo        External LLM API integration planned for Stage S1.
)

:: 3. Check Port 8000 availability (No arbitrary taskkill)
echo [3/3] Checking Port 8000 availability...
set PORT_BUSY=0
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8000 ^| findstr LISTENING 2^>nul') do (
    if not "%%a"=="" if not "%%a"=="0" (
        echo [WARNING] Port 8000 is occupied by process PID %%a.
        set PORT_BUSY=1
    )
)
if "%PORT_BUSY%"=="1" (
    echo [NOTICE] If startup fails with address-in-use, please close the existing server first.
) else (
    echo [OK] Port 8000 is available.
)

echo.
echo ======================================================================
echo   📍 Sales Operator Console:   http://127.0.0.1:8000/sales
echo   📚 Swagger API Docs:         http://127.0.0.1:8000/docs
echo   🩺 Health Check:             http://127.0.0.1:8000/health
echo ======================================================================
echo.

:: 4. Secure Automatic Single-Provider Credential Loading (DPAPI)
echo [4/4] Checking secure local credentials (DPAPI)...
if exist "scripts\SALESAI-SEVEN-PROVIDERS.ps1" (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "& '.\scripts\SALESAI-SEVEN-PROVIDERS.ps1' -Mode LoadGemini; & '.\scripts\SALESAI-SEVEN-PROVIDERS.ps1' -Mode LoadBrave" >nul 2>&1
)

:: Launch main SalesAI server
".venv\Scripts\python.exe" -u start_server.py

pause
