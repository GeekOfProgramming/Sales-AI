@echo off
chcp 65001 >nul
title pyBIM-LLM Local AI ^& BIM Gateway
cd /d "%~dp0"

echo ======================================================================
echo           🏛️ pyBIM-LLM - Local AI ^& RAG Gateway for Revit
echo ======================================================================
echo.

:: 1. Check Ollama Service
echo [1/3] Checking Ollama AI Daemon...
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
echo [OK] Ollama AI core is online.

:: 2. Check Virtual Environment
echo [2/3] Checking Python Virtual Environment (.venv)...
if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment not found at: %~dp0.venv\Scripts\python.exe
    echo Please make sure the virtual environment exists before launching.
    pause
    exit /b 1
)
echo [OK] Python virtual environment ready.

:: 3. Free Port 8000 if occupied
echo [3/3] Checking Port 8000 availability...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8000 ^| findstr LISTENING 2^>nul') do (
    if not "%%a"=="" if not "%%a"=="0" (
        echo [*] Freeing busy port 8000 [PID %%a]...
        taskkill /F /PID %%a >nul 2>&1
    )
)
echo [OK] Port 8000 is ready.

:: 4. Launch Browser and Server
echo.
echo ======================================================================
echo   📍 Local Studio:        http://localhost:8000/ui
echo   📚 Swagger API Docs:    http://localhost:8000/docs
echo   🔌 Revit API Endpoint:  http://localhost:8000/generate-script
echo ======================================================================
echo.
echo Opening pyBIM-LLM Studio in your default browser...

:: Open browser automatically after 2 seconds
start "" cmd /c "timeout /t 2 /nobreak >nul & start http://localhost:8000/ui"

:: Launch main gateway server
".venv\Scripts\python.exe" -u start_server.py

pause
