@echo off
chcp 65001 >nul
title SalesAI - Pulling Ollama Models
cd /d "%~dp0.."

echo =====================================
echo       Pulling AI Models (Ollama)
echo =====================================
echo.

:: Check if Ollama is running
curl -s http://127.0.0.1:11434/api/tags >nul 2>&1
if errorlevel 1 (
    echo [*] Ollama not detected on port 11434. Starting Ollama in background...
    if exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" (
        start /B "" "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" serve >nul 2>&1
    ) else (
        start /B "" ollama serve >nul 2>&1
    )
    timeout /t 5 /nobreak >nul
)

echo [*] Pulling Embedding Model (nomic-embed-text)...
ollama pull nomic-embed-text
if errorlevel 1 (
    echo [ERROR] Failed to pull nomic-embed-text.
    pause
    exit /b 1
)

echo [*] Pulling Coding/JSON Model (qwen2.5:1.5b)...
ollama pull qwen2.5:1.5b
if errorlevel 1 (
    echo [ERROR] Failed to pull qwen2.5:1.5b.
    pause
    exit /b 1
)

echo [*] Pulling NLP/General Model (llama3)...
ollama pull llama3
if errorlevel 1 (
    echo [ERROR] Failed to pull llama3.
    pause
    exit /b 1
)

echo.
echo [OK] All AI models pulled successfully.
pause
