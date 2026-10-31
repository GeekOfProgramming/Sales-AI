# ==============================================================================
# SalesAI Windows PowerShell Launcher (MVP-01)
# Starts SalesAI Local Operator Console without mandatory Ollama or GPU dependencies.
# ==============================================================================

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -Path $RepoRoot

Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host "                    SalesAI Local Operator Console                    " -ForegroundColor Cyan
Write-Host "                     pyBIM Lead Discovery MVP-01                      " -ForegroundColor Cyan
Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host ""

# 1. Check Python Virtual Environment
$PythonExe = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    Write-Host "[ERROR] Python virtual environment not found at: $PythonExe" -ForegroundColor Red
    Write-Host "Please set up .venv before launching." -ForegroundColor Yellow
    exit 1
}
Write-Host "[OK] Python environment verified." -ForegroundColor Green

# 2. Check Optional LLM Service (Non-blocking)
try {
    $response = Invoke-WebRequest -Uri "http://127.0.0.1:11434/api/tags" -Method Get -TimeoutSec 2 -ErrorAction SilentlyContinue
    if ($response.StatusCode -eq 200) {
        Write-Host "[INFO] Local Ollama service detected on port 11434." -ForegroundColor Green
    }
} catch {
    Write-Host "[INFO] Local Ollama not running (optional). SalesAI operates in deterministic mode." -ForegroundColor Gray
}

# 3. Check Port 8000 & Identify Process
$targetPort = 8000
$portConnections = @(Get-NetTCPConnection -LocalPort $targetPort -State Listen -ErrorAction SilentlyContinue)

if ($portConnections.Count -gt 0) {
    $owningPid = $portConnections[0].OwningProcess
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $owningPid" -ErrorAction SilentlyContinue
    $procCmd = if ($proc) { $proc.CommandLine } else { "" }
    $procName = if ($proc) { $proc.Name } else { "Unknown" }

    Write-Host ""
    Write-Host "[CONFLICT] Port $targetPort is currently occupied by PID $owningPid ($procName)." -ForegroundColor Red

    $isSalesAI = ($procCmd -like "*start_server.py*" -or $procCmd -like "*backend.main:app*")

    if ($isSalesAI) {
        Write-Host "Detected an existing SalesAI server process from a previous session." -ForegroundColor Yellow
        Write-Host "Command: $procCmd" -ForegroundColor Gray
        $confirmation = Read-Host "Would you like to cleanly stop this stale SalesAI instance and restart? (y/N)"
        if ($confirmation -match "^[yY](es)?$") {
            Write-Host "Stopping stale SalesAI process tree for PID $owningPid..." -ForegroundColor Cyan
            # Find and stop child worker/reloader processes safely
            $children = Get-CimInstance Win32_Process -Filter "ParentProcessId = $owningPid" -ErrorAction SilentlyContinue
            foreach ($child in $children) {
                Write-Host "Stopping child process PID $($child.ProcessId)..." -ForegroundColor Gray
                Stop-Process -Id $child.ProcessId -ErrorAction SilentlyContinue
            }
            Stop-Process -Id $owningPid -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 1

            # Re-verify port is released
            $recheck = Get-NetTCPConnection -LocalPort $targetPort -State Listen -ErrorAction SilentlyContinue
            if ($recheck) {
                Write-Host "[ERROR] Port $targetPort is still occupied after termination attempt. Startup aborted." -ForegroundColor Red
                exit 1
            }
            Write-Host "[OK] Stale SalesAI process stopped. Port $targetPort is now available." -ForegroundColor Green
        } else {
            Write-Host "[ABORT] Cannot start new server while port $targetPort is occupied by PID $owningPid." -ForegroundColor Red
            Write-Host "Please stop the existing process or run the launcher again with restart confirmation." -ForegroundColor Yellow
            exit 1
        }
    } else {
        Write-Host "[ERROR] Port $targetPort is owned by an external application: $procName (PID: $owningPid)." -ForegroundColor Red
        Write-Host "Command: $procCmd" -ForegroundColor Gray
        Write-Host "SalesAI will not terminate external or unidentified processes. Please free port $targetPort and retry." -ForegroundColor Yellow
        exit 1
    }
} else {
    Write-Host "[OK] Port $targetPort is available." -ForegroundColor Green
}

# 4. Secure Automatic Brave Search Credential Loading (DPAPI)
$braveVar = "BRAVE_SEARCH_API_KEY"
if ($env:BRAVE_SEARCH_API_KEY -and $env:BRAVE_SEARCH_API_KEY.Trim()) {
    Write-Host "[INFO] BRAVE_SEARCH_API_KEY is already present in current process environment." -ForegroundColor Green
} else {
    $sevenProvidersScript = Join-Path $RepoRoot "scripts\SALESAI-SEVEN-PROVIDERS.ps1"
    if (Test-Path -LiteralPath $sevenProvidersScript) {
        try {
            # Load only the Brave credential without loading unrelated LLM keys
            & $sevenProvidersScript -Mode LoadBrave | Out-Null
        } catch {
            Write-Host "[WARN] Encountered error loading Brave DPAPI credential: $($_.Exception.Message)" -ForegroundColor Yellow
        }
    }
}

if ($env:BRAVE_SEARCH_API_KEY -and $env:BRAVE_SEARCH_API_KEY.Trim()) {
    Write-Host "[INFO] BRAVE_SEARCH_API_KEY is configured (ready for operator-authorized searches)." -ForegroundColor Green
} else {
    Write-Host "[NOTICE] BRAVE_SEARCH_API_KEY is not configured. Live web discovery is blocked; dry-run preview is enabled." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host "  - Operator Dashboard:   http://127.0.0.1:8000/sales                " -ForegroundColor White
Write-Host "  - Discovery Search:     http://127.0.0.1:8000/sales/discovery      " -ForegroundColor White
Write-Host "  - Candidate Review:     http://127.0.0.1:8000/sales/candidates     " -ForegroundColor White
Write-Host "  - Workbook Import:      http://127.0.0.1:8000/sales/sources        " -ForegroundColor White
Write-Host "  - API Documentation:    http://127.0.0.1:8000/docs                 " -ForegroundColor White
Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Starting Uvicorn server on http://127.0.0.1:8000 (Press Ctrl+C to stop)..." -ForegroundColor Green
Write-Host ""

& $PythonExe -u start_server.py
