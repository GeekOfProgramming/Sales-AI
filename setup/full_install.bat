@echo off
chcp 65001 >nul
title SalesAI - Full Installation
cd /d "%~dp0.."

echo =====================================
echo       SalesAI Full Installation
echo =====================================
echo.

echo [*] Step 1: Installing Python Dependencies...
call setup\install_requirements.bat
if errorlevel 1 (
    echo [ERROR] Step 1 failed. Aborting.
    pause
    exit /b 1
)

echo.
echo [*] Step 2: Pulling Local AI Models...
call setup\pull_models.bat
if errorlevel 1 (
    echo [ERROR] Step 2 failed. Aborting.
    pause
    exit /b 1
)

echo.
echo =====================================
echo       INSTALLATION COMPLETE
echo =====================================
echo.
echo You can now start the server by running:
echo run_sales_ai.bat
echo.
pause
