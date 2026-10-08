# ==============================================================================
# setup/hardware_audit.ps1
# SalesAI Production Readiness - Step 5 Hardware Audit Runner
# Safe, repeatable Windows hardware audit for Hamed's laptop.
# ==============================================================================

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "[*] SalesAI Hardware Audit and AI Capacity Profiler" -ForegroundColor Cyan
Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host ""

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

# Determine Python executable
$PythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    $PythonCmd = Get-Command "python" -ErrorAction SilentlyContinue
    if ($PythonCmd) {
        $PythonExe = $PythonCmd.Source
    } else {
        $PythonExe = $null
    }
}

$ProfileScript = Join-Path $ProjectRoot "scripts\hardware_profile.py"

if ($PythonExe -and (Test-Path $ProfileScript)) {
    Write-Host "[*] Executing hardware audit using Python: $PythonExe" -ForegroundColor Yellow
    & "$PythonExe" "$ProfileScript"
    $ExitCode = $LASTEXITCODE
} else {
    Write-Host "[!] Python interpreter not found. Running native PowerShell fallback audit..." -ForegroundColor Yellow

    $TargetDir = Join-Path $ProjectRoot "runtime\hardware"
    if (-not (Test-Path $TargetDir)) {
        New-Item -ItemType Directory -Force -Path $TargetDir | Out-Null
    }

    $OS = Get-CimInstance Win32_OperatingSystem | Select-Object Caption, Version, BuildNumber, OSArchitecture, TotalVisibleMemorySize, FreePhysicalMemory
    $CPU = Get-CimInstance Win32_Processor | Select-Object Manufacturer, Name, NumberOfCores, NumberOfLogicalProcessors
    $GPU = Get-CimInstance Win32_VideoController | Select-Object Name, DriverVersion, AdapterRAM
    $Disk = Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" | Select-Object DeviceID, FileSystem, Size, FreeSpace

    $OllamaInstalled = [bool](Get-Command "ollama" -ErrorAction SilentlyContinue)
    $GitInstalled = [bool](Get-Command "git" -ErrorAction SilentlyContinue)

    $Profile = @{
        generated_at = (Get-Date).ToUniversalTime().ToString("o")
        machine_label = "hamed_laptop"
        os = @{
            caption = $OS.Caption
            version = $OS.Version
            build_number = $OS.BuildNumber
            architecture = $OS.OSArchitecture
        }
        cpu = @{
            name = $CPU.Name
            manufacturer = $CPU.Manufacturer
            physical_cores = $CPU.NumberOfCores
            logical_processors = $CPU.NumberOfLogicalProcessors
        }
        memory = @{
            total_gb = [math]::Round($OS.TotalVisibleMemorySize / 1MB, 2)
            free_gb = [math]::Round($OS.FreePhysicalMemory / 1MB, 2)
        }
        gpus = @($GPU | ForEach-Object {
            @{
                name = $_.Name
                driver_version = $_.DriverVersion
                is_discrete = ($_.Name -match "NVIDIA|GeForce|RTX|GTX|Radeon RX")
            }
        })
        storage = @($Disk | ForEach-Object {
            @{
                drive = $_.DeviceID
                filesystem = $_.FileSystem
                total_gb = [math]::Round($_.Size / 1GB, 2)
                free_gb = [math]::Round($_.FreeSpace / 1GB, 2)
            }
        })
        toolchain = @{
            git = @{ installed = $GitInstalled }
            ollama = @{ installed = $OllamaInstalled }
        }
        capacity = @{
            recommended_test_tiers = @()
            warnings = @("Audit generated via PowerShell fallback; install Python/venv for full candidate modeling.")
            final_model_selected = $null
        }
    }

    $JsonPath = Join-Path $TargetDir "hardware_profile.json"
    $Profile | ConvertTo-Json -Depth 5 | Out-File -FilePath $JsonPath -Encoding utf8
    Write-Host "[OK] Fallback profile written to $JsonPath" -ForegroundColor Green
    $ExitCode = 0
}

Write-Host ""
$ReportFile = Join-Path $ProjectRoot "runtime\hardware\HARDWARE-REPORT.md"
$ProfileFile = Join-Path $ProjectRoot "runtime\hardware\hardware_profile.json"

if (Test-Path $ReportFile) {
    Write-Host "-----------------------------------------------------------------" -ForegroundColor Green
    Write-Host "[OK] HARDWARE REPORT READY FOR STEP 6 BENCHMARK:" -ForegroundColor Green
    Write-Host "   Report:  $ReportFile" -ForegroundColor Cyan
    Write-Host "   Profile: $ProfileFile" -ForegroundColor Cyan
    Write-Host "-----------------------------------------------------------------" -ForegroundColor Green
}

exit $ExitCode
