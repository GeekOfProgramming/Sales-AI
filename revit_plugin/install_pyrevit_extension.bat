@echo off
chcp 65001 >nul
title pyBIM - pyRevit Extension Installer
cd /d "%~dp0"

echo ======================================================================
echo          🏛️ pyBIM-LLM - Autodesk Revit (pyRevit) Installer
echo ======================================================================
echo.

set SOURCE_DIR=%~dp0pyBIM.extension
if not exist "%SOURCE_DIR%" (
    echo [ERROR] pyBIM.extension folder not found at:
    echo %SOURCE_DIR%
    pause
    exit /b 1
)

:: Common pyRevit extension directories
set TARGET_DIR=%APPDATA%\pyRevit\Extensions\pyBIM.extension

echo [1/3] Target pyRevit Extension Directory:
echo %TARGET_DIR%
echo.

echo [2/3] Installing / Updating pyBIM Extension...
if not exist "%APPDATA%\pyRevit\Extensions" (
    mkdir "%APPDATA%\pyRevit\Extensions" 2>nul
)

:: Use robocopy for fast, clean mirroring
robocopy "%SOURCE_DIR%" "%TARGET_DIR%" /E /NP /NFL /NDL /NJH /NJS

echo [OK] Files copied successfully.
echo.

:: Check pyRevit CLI
echo [3/3] Checking pyRevit CLI installation...
where pyrevit >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [*] pyRevit CLI found. Reloading pyRevit session...
    pyrevit reload >nul 2>&1
    echo [OK] pyRevit reloaded.
) else (
    echo [INFO] pyRevit CLI not in PATH. Extension will automatically load when Revit starts.
)

echo.
echo ======================================================================
echo   🎉 INSTALLATION COMPLETE!
echo.
echo   Next steps in Autodesk Revit:
echo   1. Launch Autodesk Revit on this laptop.
echo   2. You will see a new tab: "pyBIM" in the top Ribbon.
echo   3. Click "Server Config" to point to your AI Workstation:
echo      http://10.120.24.34:8000
echo   4. Select any Revit elements and click "AI Assistant"!
echo ======================================================================
echo.
pause
