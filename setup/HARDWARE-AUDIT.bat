@echo off
chcp 65001 >nul
title SalesAI Hardware Audit - Hamed Laptop
cd /d "%~dp0.."

echo =================================================================
echo        SalesAI Hardware Audit - Step 5
echo =================================================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0hardware_audit.ps1"

echo.
echo =================================================================
echo Audit execution complete.
echo See output in runtime\hardware\
echo =================================================================
pause
