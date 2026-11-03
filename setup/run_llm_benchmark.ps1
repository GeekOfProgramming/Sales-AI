# setup/run_llm_benchmark.ps1
# Authoritative PowerShell Launcher for Real Ollama Benchmark (Production Readiness Step 6).
# Safe, idempotent execution for Hamed's Windows laptop.

[CmdletBinding()]
param(
    [string]$Model = "llama3.2:3b",
    [int]$Repetitions = 2,
    [string]$Endpoint = "http://127.0.0.1:11434",
    [switch]$DryRun,
    [switch]$ListModels,
    [string]$Task = ""
)

$ErrorActionPreference = "Stop"

Write-Host "`n========================================================" -ForegroundColor Cyan
Write-Host "   SalesAI - Production Readiness Step 6: LLM Benchmark" -ForegroundColor Cyan
Write-Host "========================================================`n" -ForegroundColor Cyan

# Locate project root
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$ProjectRoot = Resolve-Path "$ScriptDir\.."

# 1. Locate Python Interpreter
$PythonPath = $null
if (Test-Path "$ProjectRoot\.venv\Scripts\python.exe") {
    $PythonPath = "$ProjectRoot\.venv\Scripts\python.exe"
} elseif (Test-Path "C:\Sales-AI\.venv\Scripts\python.exe") {
    $PythonPath = "C:\Sales-AI\.venv\Scripts\python.exe"
} else {
    $SysPy = Get-Command python -ErrorAction SilentlyContinue
    if ($SysPy) {
        $PythonPath = $SysPy.Source
    }
}

if (-not $PythonPath) {
    Write-Host "[ERROR] Python interpreter not found in .venv or PATH." -ForegroundColor Red
    Write-Host "Please install Python 3.10+ or set up the virtual environment." -ForegroundColor Yellow
    exit 1
}

Write-Host "[OK] Using Python: $PythonPath" -ForegroundColor Green

# 2. Check Ollama daemon connectivity
if (-not $DryRun) {
    Write-Host "[CHECK] Testing connection to Ollama at $Endpoint..." -ForegroundColor Gray
    try {
        $resp = Invoke-RestMethod -Uri "$Endpoint/api/version" -Method Get -TimeoutSec 3 -ErrorAction Stop
        Write-Host "[OK] Connected to Ollama daemon (version: $($resp.version))" -ForegroundColor Green
    } catch {
        Write-Host "[WARNING] Could not reach Ollama daemon at $Endpoint." -ForegroundColor Yellow
        Write-Host "Please start the Ollama service ('ollama serve' or start Ollama app) before running the real benchmark." -ForegroundColor Yellow
        Write-Host "If you wish to test without Ollama, run with -DryRun." -ForegroundColor Gray
    }
}

# 3. Build arguments
$ArgsList = @(
    "$ProjectRoot\scripts\benchmark_ollama.py",
    "--model", $Model,
    "--repetitions", $Repetitions,
    "--endpoint", $Endpoint
)

if ($DryRun) {
    $ArgsList += "--dry-run"
}

if ($ListModels) {
    $ArgsList += "--list-models"
}

if ($Task -ne "") {
    $ArgsList += @("--task", $Task)
}

# 4. Execute Benchmark
Write-Host "`n[EXEC] Running benchmark runner...`n" -ForegroundColor Cyan
& $PythonPath @ArgsList

if ($LASTEXITCODE -eq 0) {
    Write-Host "`n[SUCCESS] Benchmark process completed successfully." -ForegroundColor Green
    Write-Host "Reports generated under: $ProjectRoot\runtime\benchmarks\" -ForegroundColor Gray
} else {
    Write-Host "`n[FAILED] Benchmark runner exited with code $LASTEXITCODE." -ForegroundColor Red
}
