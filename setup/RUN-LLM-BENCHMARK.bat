@echo off
REM ========================================================================
REM  SalesAI - Production Readiness Step 6: Real Ollama Benchmark Runner
REM  Double-click Windows launcher for Hamed's Laptop
REM ========================================================================

setlocal
cd /d "%~dp0\.."

echo.
echo ========================================================================
echo   Starting SalesAI Real Ollama Benchmark (Production Step 6)
echo ========================================================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_llm_benchmark.ps1" %*

echo.
echo ========================================================================
echo   Finished. Press any key to exit.
echo ========================================================================
pause >nul
