@echo off
chcp 65001 >nul
title SalesAI - Requirements Installation
cd /d "%~dp0.."

echo =====================================
echo       Installing Python Packages
echo =====================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [*] Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment. Make sure python is installed.
        pause
        exit /b 1
    )
)

echo [*] Upgrading pip...
".venv\Scripts\python.exe" -m pip install --upgrade pip

echo [*] Installing requirements from requirements.txt...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Failed to install requirements.
    pause
    exit /b 1
)

echo.
echo [OK] All Python dependencies installed successfully.
pause
