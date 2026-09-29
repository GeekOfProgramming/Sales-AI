@echo off
chcp 65001 >nul
title SalesAI Local Server
cd /d "%~dp0"

echo =====================================
echo       SalesAI Local Server
echo =====================================
echo.

:: 1. Check Ollama Service
echo [1] Checking Ollama...
curl -s http://127.0.0.1:11434/api/tags >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [*] Ollama not detected on port 11434. Starting Ollama in background...
    if exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" (
        start /B "" "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" serve >nul 2>&1
    ) else (
        start /B "" ollama serve >nul 2>&1
    )
    timeout /t 3 /nobreak >nul
)
echo [OK] Ollama running on :11434

:: 2. Check models
echo [2] Checking models...
curl -s http://127.0.0.1:11434/api/tags | findstr "llama3" >nul
if %ERRORLEVEL% NEQ 0 (
    echo [*] llama3 not found.
) else (
    echo [OK] llama3 available
)

:: 3. Check Virtual Environment
if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment not found at: %~dp0.venv\Scripts\python.exe
    pause
    exit /b 1
)

:: 4. Free Port 8000 if occupied
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8000 ^| findstr LISTENING 2^>nul') do (
    if not "%%a"=="" if not "%%a"=="0" (
        taskkill /F /PID %%a >nul 2>&1
    )
)

echo [3] Starting FastAPI...
echo [OK] API running on :8000
echo.
echo SalesAI ready:
echo http://localhost:8000
echo docs:
echo http://localhost:8000/docs
echo.

:: Launch main gateway server
".venv\Scripts\python.exe" -u start_server.py

pause
