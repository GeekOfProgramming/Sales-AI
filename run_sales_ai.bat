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
echo [2] Checking Sales model...
set SALES_MODEL=qwen2.5:1.5b
if exist .env (
    for /f "tokens=1,2 delims==" %%A in (.env) do (
        if "%%A"=="SALES_LLM_MODEL" set SALES_MODEL=%%B
    )
)
echo Configured model: %SALES_MODEL%
curl -s http://127.0.0.1:11434/api/tags | findstr "%SALES_MODEL%" >nul
if %ERRORLEVEL% NEQ 0 (
    echo [NOT FOUND]
    echo [*] Auto-pulling %SALES_MODEL%...
    ollama pull %SALES_MODEL%
    if errorlevel 1 (
        echo [ERROR] Failed to pull %SALES_MODEL%. Please check your connection.
        pause
        exit /b 1
    )
    echo [OK] %SALES_MODEL% ready
) else (
    echo [OK] %SALES_MODEL% available
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
