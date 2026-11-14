# ==============================================================================
# SalesAI Windows PowerShell Launcher (MVP-01 / LAN-01)
# Starts SalesAI Local Operator Console with automatic trusted LAN detection.
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

# 4. Network Interface, Private Profile & Local/LAN URL Configuration
Write-Host ""
Write-Host "Detecting active network interfaces..." -ForegroundColor Gray

# Retrieve physical network adapters that are Up (excludes virtual adapters, VPNs, Hyper-V, Docker)
$physicalAdapters = @(Get-NetAdapter -Physical -ErrorAction SilentlyContinue | Where-Object { $_.Status -eq 'Up' })

# Prefer Wi-Fi (802.11) or Ethernet (802.3)
$activeAdapter = $physicalAdapters | Where-Object {
    $_.PhysicalMediaType -match '802.11|Native 802.11' -or $_.Name -like '*Wi-Fi*' -or $_.InterfaceDescription -like '*Wireless*'
} | Select-Object -First 1

if (-not $activeAdapter) {
    $activeAdapter = $physicalAdapters | Where-Object {
        $_.PhysicalMediaType -match '802.3' -or $_.Name -like '*Ethernet*'
    } | Select-Object -First 1
}

if (-not $activeAdapter) {
    $activeAdapter = $physicalAdapters | Select-Object -First 1
}

$detectedLanIp = $null
$detectedProfileName = "Unknown"
$isPrivateProfile = $false
$lanReason = ""

if ($activeAdapter) {
    Write-Host "[INFO] Active physical interface: $($activeAdapter.Name) ($($activeAdapter.InterfaceDescription))" -ForegroundColor Gray

    # Retrieve IPv4 addresses associated with this interface
    $ipAddresses = @(Get-NetIPAddress -InterfaceIndex $activeAdapter.InterfaceIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object {
        $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254*'
    })

    # Validate RFC 1918 Private IPv4 address:
    # - 10.0.0.0/8
    # - 172.16.0.0/12 (172.16.0.0 - 172.31.255.255)
    # - 192.168.0.0/16
    foreach ($entry in $ipAddresses) {
        $addr = $entry.IPAddress
        if ($addr -match '^10\.' -or $addr -match '^192\.168\.' -or ($addr -match '^172\.(1[6-9]|2[0-9]|3[0-1])\.')) {
            $detectedLanIp = $addr
            break
        }
    }

    # Inspect Windows Network Profile
    $profile = Get-NetConnectionProfile -InterfaceIndex $activeAdapter.InterfaceIndex -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($profile) {
        $detectedProfileName = $profile.Name
        if ($profile.NetworkCategory -eq 'Private' -or $profile.NetworkCategory -eq 'DomainAuthenticated') {
            $isPrivateProfile = $true
        } else {
            $lanReason = "Connected network '$detectedProfileName' is configured as Windows '$($profile.NetworkCategory)' profile (requires 'Private')."
        }
    } else {
        $lanReason = "Could not verify Windows network profile category for '$($activeAdapter.Name)'."
    }
} else {
    $lanReason = "No active physical Wi-Fi or Ethernet network adapter detected."
}

# Determine Host Binding: 0.0.0.0 (LAN) vs 127.0.0.1 (Localhost)
$bindHost = "127.0.0.1"
$lanAvailable = $false

if ($env:SALESAI_ALLOW_LAN -in @("1", "true", "True", "YES", "yes") -or $env:SALESAI_HOST -eq "0.0.0.0") {
    $bindHost = "0.0.0.0"
    $lanAvailable = $true
    Write-Host "[INFO] Operator override active: binding Uvicorn to 0.0.0.0 (LAN enabled)." -ForegroundColor Cyan
} elseif ($detectedLanIp -and $isPrivateProfile) {
    $bindHost = "0.0.0.0"
    $lanAvailable = $true
    Write-Host "[OK] Trusted Private LAN verified on $($activeAdapter.Name) ($detectedLanIp). Binding to 0.0.0.0." -ForegroundColor Green
} else {
    $bindHost = "127.0.0.1"
    $lanAvailable = $false
    Write-Host "[NOTICE] LAN sharing unavailable: $lanReason" -ForegroundColor Yellow
    Write-Host "         Binding to 127.0.0.1 (Localhost only) for security." -ForegroundColor Yellow
    if ($detectedProfileName -ne "Unknown" -and (-not $isPrivateProfile)) {
        Write-Host "         To enable LAN sharing across laptops on this Wi-Fi, change network profile to 'Private' in Windows Settings:" -ForegroundColor Gray
        Write-Host "         Settings -> Network & internet -> Wi-Fi -> '$detectedProfileName' -> Network profile type -> Private" -ForegroundColor Gray
        Write-Host "         (Or set `$env:SALESAI_ALLOW_LAN = '1' to override for this session)." -ForegroundColor Gray
    }
}

$env:SALESAI_HOST = $bindHost
$env:SALESAI_PORT = "$targetPort"
$env:SALESAI_LAN_IP = if ($detectedLanIp) { $detectedLanIp } else { "" }

# 5. Check Windows Firewall Inbound Rule for Port 8000
$hasFirewallRule = $false
try {
    $ruleCheck = netsh advfirewall firewall show rule name="SalesAI Local Operator Console (Port 8000)" 2>$null | Out-String
    if ($LASTEXITCODE -eq 0 -and $ruleCheck -match "Action:\s+Allow") {
        $hasFirewallRule = $true
    }
} catch {
    $hasFirewallRule = $false
}

if ($lanAvailable -and (-not $hasFirewallRule)) {
    Write-Host ""
    Write-Host "[FIREWALL] Inbound Windows Firewall rule for TCP port $targetPort is not configured." -ForegroundColor Yellow
    Write-Host "           Other laptops on the Wi-Fi may be blocked by Windows Firewall." -ForegroundColor Yellow
    $isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if ($isAdmin) {
        $fwConfirm = Read-Host "Would you like to automatically create the Windows Firewall rule for Private local subnet now? (y/N)"
        if ($fwConfirm -match "^[yY](es)?$") {
            try {
                netsh advfirewall firewall add rule name="SalesAI Local Operator Console (Port $targetPort)" dir=in action=allow protocol=TCP localport=$targetPort profile=private remoteip=localsubnet | Out-Null
                Write-Host "[OK] Windows Firewall rule added: Private profile, local subnet only." -ForegroundColor Green
                $hasFirewallRule = $true
            } catch {
                Write-Host "[WARN] Could not add firewall rule: $($_.Exception.Message)" -ForegroundColor Red
            }
        }
    } else {
        Write-Host "           To allow inbound traffic on Private networks, run this command in an Administrator PowerShell:" -ForegroundColor Gray
        Write-Host "           netsh advfirewall firewall add rule name=`"SalesAI Local Operator Console (Port $targetPort)`" dir=in action=allow protocol=TCP localport=$targetPort profile=private remoteip=localsubnet" -ForegroundColor Cyan
    }
}

# 6. Secure Automatic Brave Search Credential Loading (DPAPI)
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

# 7. Secure Automatic Gemini LLM Credential Loading (DPAPI)
$geminiVar = "GEMINI_API_KEY"
if ($env:GEMINI_API_KEY -and $env:GEMINI_API_KEY.Trim()) {
    Write-Host "[INFO] GEMINI_API_KEY is already present in current process environment." -ForegroundColor Green
} else {
    $sevenProvidersScript = Join-Path $RepoRoot "scripts\SALESAI-SEVEN-PROVIDERS.ps1"
    if (Test-Path -LiteralPath $sevenProvidersScript) {
        try {
            # Load only the Gemini credential without loading unrelated keys
            & $sevenProvidersScript -Mode LoadGemini | Out-Null
        } catch {
            Write-Host "[WARN] Encountered error loading Gemini DPAPI credential: $($_.Exception.Message)" -ForegroundColor Yellow
        }
    }
}

if ($env:GEMINI_API_KEY -and $env:GEMINI_API_KEY.Trim()) {
    Write-Host "[INFO] GEMINI_API_KEY is configured (ready for operator-authorized LLM research)." -ForegroundColor Green
} else {
    Write-Host "[NOTICE] GEMINI_API_KEY is not configured. Paid LLM analysis is disabled; local heuristics active." -ForegroundColor Gray
}

# 8. Display Connectivity URLs Dynamically
Write-Host ""
Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host "Sales-AI Server" -ForegroundColor Cyan
Write-Host "Local (this laptop):" -ForegroundColor White
Write-Host "http://127.0.0.1:$targetPort/sales" -ForegroundColor Green
Write-Host ""
Write-Host "LAN (share with another laptop on the same Wi-Fi):" -ForegroundColor White
if ($detectedLanIp) {
    if ($lanAvailable) {
        Write-Host "http://${detectedLanIp}:$targetPort/sales" -ForegroundColor Green
    } else {
        Write-Host "http://${detectedLanIp}:$targetPort/sales" -ForegroundColor Gray
        Write-Host "  (Status: Inactive - Uvicorn bound to 127.0.0.1 because network profile is not Private)" -ForegroundColor Yellow
    }
} else {
    Write-Host "[No private LAN IPv4 detected]" -ForegroundColor Gray
}
Write-Host ""
Write-Host "Access Scope: Private Local Network (No public internet exposure)" -ForegroundColor Gray
Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Starting Uvicorn server on http://${bindHost}:${targetPort} (Press Ctrl+C to stop)..." -ForegroundColor Green
Write-Host ""

& $PythonExe -u start_server.py
