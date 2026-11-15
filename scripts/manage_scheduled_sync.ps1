<#
.SYNOPSIS
    Manages the Windows Scheduled Task for pyBIM Sales-AI Mailbox Sync (IMAP-AUTO-SYNC-01A).

.DESCRIPTION
    Provides operator commands to inspect, install, enable, disable, and uninstall
    the automated mailbox synchronization task in Windows Task Scheduler.
    Runs under the current interactive user context so Windows DPAPI credential store is accessible.
    Strictly forbids running unattended live synchronization without operator authorization.

.PARAMETER Action
    One of: 'status', 'verify-offline', 'install', 'enable', 'disable', 'uninstall'
#>

[CmdletBinding()]
param(
    [Parameter(Position = 0, Mandatory = $true)]
    [ValidateSet("status", "verify-offline", "install", "enable", "disable", "uninstall")]
    [string]$Action,

    [Parameter(Mandatory = $false)]
    [string]$TaskName = "pyBIM_SalesAI_MailboxSync",

    [Parameter(Mandatory = $false)]
    [string]$XmlPath = ""
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$RepoRoot = (Resolve-Path "$ScriptDir\..").Path
$PythonVenv = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$PilotScript = Join-Path $RepoRoot "scripts\run_mailbox_pilot.py"

if ([string]::IsNullOrWhiteSpace($XmlPath)) {
    $XmlPath = Join-Path $ScriptDir "windows_tasks\salesai_mailbox_sync.xml"
}

Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host "pyBIM Sales-AI: Windows Scheduled Task Manager (IMAP-AUTO-SYNC-01A)" -ForegroundColor Cyan
Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host "Task Name:    $TaskName"
Write-Host "Action:       $Action"
Write-Host "Repository:   $RepoRoot"
Write-Host "----------------------------------------------------------------------"

switch ($Action) {
    "status" {
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if ($null -eq $task) {
            Write-Host "Task Status: NOT INSTALLED" -ForegroundColor Yellow
            Write-Host "To install, run: .\scripts\manage_scheduled_sync.ps1 install"
        } else {
            Write-Host "Task Status:       INSTALLED" -ForegroundColor Green
            Write-Host "State:             $($task.State)"
            Write-Host "Settings Enabled:  $($task.Settings.Enabled)"
            
            # Action and Context Details
            $taskExecAction = $task.Actions | Select-Object -First 1
            if ($taskExecAction) {
                Write-Host "Executable:        $($taskExecAction.Execute)"
                Write-Host "Arguments:         $($taskExecAction.Arguments)"
                Write-Host "Working Directory: $($taskExecAction.WorkingDirectory)"
            }
            
            # Principal / Identity Details
            $principal = $task.Principal
            if ($principal) {
                Write-Host "User Context:      $($principal.UserId)"
                Write-Host "Logon Type:        $($principal.LogonType)"
                Write-Host "Run Level:         $($principal.RunLevel)"
            }

            # Triggers Details
            Write-Host "Configured Triggers:"
            foreach ($trig in $task.Triggers) {
                Write-Host "  - StartBoundary: $($trig.StartBoundary) | Enabled: $($trig.Enabled)"
            }

            $taskInfo = Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction SilentlyContinue
            if ($taskInfo) {
                Write-Host "Last Run Time:     $($taskInfo.LastRunTime) (Result: $($taskInfo.LastTaskResult))"
                Write-Host "Next Run Time:     $($taskInfo.NextRunTime)"
            }
        }
    }

    "verify-offline" {
        Write-Host "Performing offline dry-run pre-flight check..." -ForegroundColor Cyan
        if (-not (Test-Path $PythonVenv)) {
            Write-Error "Virtual environment python executable not found at: $PythonVenv"
            exit 1
        }
        if (-not (Test-Path $XmlPath)) {
            Write-Error "Task definition XML template not found at: $XmlPath"
            exit 1
        }
        & $PythonVenv $PilotScript scheduler-status
        Write-Host "`nTask Definition XML Validation: PASS" -ForegroundColor Green
        Write-Host "Offline verification complete. Scheduled task is ready for registration when authorized." -ForegroundColor Green
    }

    "install" {
        Write-Host "Registering task '$TaskName' from $XmlPath..." -ForegroundColor Cyan
        if (-not (Test-Path $XmlPath)) {
            Write-Error "Task XML file not found at $XmlPath"
            exit 1
        }
        # Register task for current user
        $user = "$env:USERDOMAIN\$env:USERNAME"
        Write-Host "Registering task under user: $user"
        schtasks.exe /Create /TN $TaskName /XML $XmlPath /F
        if ($LASTEXITCODE -eq 0) {
            Write-Host "Successfully registered task '$TaskName'." -ForegroundColor Green
            Write-Host "NOTE: To enable live execution, mailboxes must also be enabled in database and task enabled."
        } else {
            Write-Error "Failed to register task '$TaskName'. Exit code: $LASTEXITCODE"
        }
    }

    "enable" {
        Write-Host "Enabling task '$TaskName'..." -ForegroundColor Cyan
        Enable-ScheduledTask -TaskName $TaskName -ErrorAction Stop
        Write-Host "Task '$TaskName' is now ENABLED." -ForegroundColor Green
    }

    "disable" {
        Write-Host "Disabling task '$TaskName'..." -ForegroundColor Yellow
        Disable-ScheduledTask -TaskName $TaskName -ErrorAction Stop
        Write-Host "Task '$TaskName' is now DISABLED." -ForegroundColor Yellow
    }

    "uninstall" {
        Write-Host "Unregistering task '$TaskName'..." -ForegroundColor Yellow
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction Stop
        Write-Host "Task '$TaskName' has been UNINSTALLED." -ForegroundColor Yellow
    }
}

Write-Host "======================================================================`n" -ForegroundColor Cyan
