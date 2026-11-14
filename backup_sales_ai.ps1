<#
.SYNOPSIS
    Sales-AI Manual On-Demand Database Backup Command (BACKUP-01)
.DESCRIPTION
    Creates an immediate, consistent SQLite online backup of the live Sales-AI
    CRM database (data/sales_outreach.db) without stopping the server.
    Verifies PRAGMA integrity_check, foreign keys, and calculates streaming SHA-256.
.PARAMETER Destination
    Optional destination directory. Defaults to data/backups.
.EXAMPLE
    .\backup_sales_ai.ps1
    .\backup_sales_ai.ps1 -VerifyRestore
#>
[CmdletBinding()]
param (
    [Parameter(Mandatory = $false)]
    [string]$Destination,

    [Parameter(Mandatory = $false)]
    [switch]$VerifyRestore
)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -Path $RepoRoot

$PythonExe = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    $PythonExe = "python.exe"
}

$ScriptPath = Join-Path $RepoRoot "scripts\backup_manager.py"
if (-not (Test-Path $ScriptPath)) {
    Write-Host "[ERROR] Backup manager script not found at: $ScriptPath" -ForegroundColor Red
    exit 1
}

$argsList = @("manual", "--json")
if ($Destination) {
    $argsList += @("--dest", $Destination)
}

Write-Host "Creating consistent SQLite online backup..." -ForegroundColor Cyan
$jsonRaw = & $PythonExe $ScriptPath @argsList | Out-String

try {
    $res = $jsonRaw | ConvertFrom-Json
} catch {
    Write-Host "[ERROR] Could not parse backup result: $jsonRaw" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host "                    Sales-AI Manual Database Backup                   " -ForegroundColor Cyan
Write-Host "======================================================================" -ForegroundColor Cyan

if ($res.success) {
    $sizeMb = [math]::Round($res.backup_size_bytes / 1MB, 2)
    Write-Host "Backup Filename:  $([System.IO.Path]::GetFileName($res.backup_path))" -ForegroundColor White
    Write-Host "Full Path:        $($res.backup_path)" -ForegroundColor Gray
    Write-Host "Backup Size:      $sizeMb MB ($($res.backup_size_bytes) bytes)" -ForegroundColor White
    Write-Host "Timestamp (UTC):  $($res.timestamp_utc)" -ForegroundColor White
    Write-Host "SHA-256 Checksum: $($res.sha256_checksum)" -ForegroundColor White
    Write-Host "Integrity Result: $($res.integrity_status)" -ForegroundColor Green
    Write-Host "Foreign Keys:     $($res.fk_violations_count) violation(s)" -ForegroundColor $(if ($res.fk_violations_count -eq 0) { "Green" } else { "Yellow" })
    Write-Host "Overall Result:   SUCCESS" -ForegroundColor Green
    Write-Host "======================================================================" -ForegroundColor Cyan
    Write-Host ""

    if ($VerifyRestore) {
        Write-Host "Verifying restore into disposable clone..." -ForegroundColor Cyan
        & $PythonExe $ScriptPath "verify-restore" "--backup" $res.backup_path
    }
    exit 0
} else {
    Write-Host "Integrity Result: $($res.integrity_status)" -ForegroundColor Red
    Write-Host "Error Details:    $($res.error_message)" -ForegroundColor Red
    Write-Host "Overall Result:   FAILED" -ForegroundColor Red
    Write-Host "======================================================================" -ForegroundColor Cyan
    Write-Host ""
    exit 1
}
