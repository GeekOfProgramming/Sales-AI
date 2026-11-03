# SalesAI - Unified 8-Provider Credential Manager & Metadata Probe
# Windows PowerShell 5.1+ or PowerShell 7+ on Windows only.
# Modes: StatusAll, ProbeAll, LoadAll, SetupAll, SetupOne, ProbeBrave,
#        Setup (compat), Probe (compat), Load (compat), SetupBrave (compat), LoadBrave (compat), StatusBrave (compat).
# No token is printed, transmitted elsewhere, or saved in the source repository.

[CmdletBinding()]
param(
    [ValidateSet(
        'StatusAll', 'ProbeAll', 'LoadAll', 'SetupAll', 'SetupOne', 'ProbeBrave',
        'Setup', 'Probe', 'Load', 'SetupBrave', 'LoadBrave', 'StatusBrave', 'LoadGemini'
    )]
    [string]$Mode = 'StatusAll',

    [ValidateSet('Gemini', 'GroqCloud', 'OpenAI', 'OpenRouter', 'Cohere', 'NVIDIA', 'Cloudflare', 'Brave')]
    [string]$Provider = $null,

    [switch]$RefreshStale = $false,

    [string]$AuthorizeBrave = $null
)

$ErrorActionPreference = 'Stop'

if (-not [Runtime.InteropServices.RuntimeInformation]::IsOSPlatform([Runtime.InteropServices.OSPlatform]::Windows)) {
    throw 'This script uses Windows DPAPI and must run on Windows.'
}

$secretFolder = Join-Path $env:APPDATA 'pyBIM\SalesAI\api-secrets'
$accountIdFile = Join-Path $secretFolder 'cloudflare-account-id.txt'

# 8 providers definition: 7 LLMs + 1 Web Search
$allProviders = @(
    [pscustomobject]@{ Key = 'Gemini';     Name = 'Google Gemini';         Env = 'GEMINI_API_KEY';        Type = 'LLM' },
    [pscustomobject]@{ Key = 'GroqCloud';  Name = 'GroqCloud';             Env = 'GROQ_API_KEY';          Type = 'LLM' },
    [pscustomobject]@{ Key = 'OpenAI';     Name = 'OpenAI';                Env = 'OPENAI_API_KEY';        Type = 'LLM' },
    [pscustomobject]@{ Key = 'OpenRouter'; Name = 'OpenRouter';            Env = 'OPENROUTER_API_KEY';    Type = 'LLM' },
    [pscustomobject]@{ Key = 'Cohere';     Name = 'Cohere';                Env = 'COHERE_API_KEY';        Type = 'LLM' },
    [pscustomobject]@{ Key = 'NVIDIA';     Name = 'NVIDIA NIM';            Env = 'NVIDIA_API_KEY';        Type = 'LLM' },
    [pscustomobject]@{ Key = 'Cloudflare'; Name = 'Cloudflare Workers AI'; Env = 'CLOUDFLARE_API_TOKEN'; Type = 'LLM' },
    [pscustomobject]@{ Key = 'Brave';      Name = 'Brave Search';          Env = 'BRAVE_SEARCH_API_KEY';  Type = 'Search' }
)

# Legacy 7 LLM providers definition
$legacyProviders = @($allProviders | Where-Object { $_.Type -eq 'LLM' })

function Get-SecretPath([string]$variableName) {
    return (Join-Path $secretFolder ($variableName + '.dpapi'))
}

function Get-ReturnedCount($items) {
    if ($null -eq $items) { return 0 }
    return @($items).Count
}

function Unprotect-ApiSecret([string]$variableName) {
    $file = Get-SecretPath $variableName
    if (-not (Test-Path -LiteralPath $file)) {
        throw "Missing locally saved key: $variableName"
    }
    $cipher = (Get-Content -LiteralPath $file -Raw -Encoding UTF8).Trim()
    $secure = ConvertTo-SecureString -String $cipher
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
        $secure.Dispose()
    }
}

function Protect-ApiSecret([string]$variableName, [System.Security.SecureString]$secure) {
    New-Item -ItemType Directory -Force -Path $secretFolder | Out-Null
    $file = Get-SecretPath $variableName
    $cipher = ConvertFrom-SecureString -SecureString $secure
    Set-Content -LiteralPath $file -Value $cipher -NoNewline -Encoding UTF8

    try {
        $currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
        $acl = Get-Acl -LiteralPath $file
        $acl.SetAccessRuleProtection($true, $false)
        $userRule = New-Object System.Security.AccessControl.FileSystemAccessRule($currentUser, 'FullControl', 'Allow')
        $systemRule = New-Object System.Security.AccessControl.FileSystemAccessRule('NT AUTHORITY\SYSTEM', 'FullControl', 'Allow')
        $adminRule = New-Object System.Security.AccessControl.FileSystemAccessRule('BUILTIN\Administrators', 'FullControl', 'Allow')
        $acl.ResetAccessRule($userRule)
        $acl.AddAccessRule($systemRule)
        $acl.AddAccessRule($adminRule)
        Set-Acl -LiteralPath $file -AclObject $acl
    }
    catch {
        # Non-blocking if restricted by environment / sandbox policy
    }
}

function Save-SingleProviderCredential([psobject]$prov) {
    $secure = Read-Host ("API key / token for {0} ({1})" -f $prov.Name, $prov.Env) -AsSecureString
    try {
        if ($null -eq $secure -or $secure.Length -eq 0) {
            $existingFile = Get-SecretPath $prov.Env
            if (Test-Path -LiteralPath $existingFile) {
                Write-Host ("PRESERVED: {0} ({1}) kept unchanged (blank input)." -f $prov.Name, $prov.Env) -ForegroundColor Yellow
            }
            else {
                Write-Host ("SKIPPED: {0} (blank input, no previously saved key)." -f $prov.Name) -ForegroundColor Yellow
            }
        }
        else {
            Protect-ApiSecret -variableName $prov.Env -secure $secure
            Write-Host ("SAVED: {0} ({1}) encrypted into DPAPI vault." -f $prov.Name, $prov.Env) -ForegroundColor Green
        }
    }
    finally {
        if ($null -ne $secure) { $secure.Dispose() }
    }

    if ($prov.Key -eq 'Cloudflare') {
        Write-Host ''
        $existingAcct = ''
        if (Test-Path -LiteralPath $accountIdFile) {
            $existingAcct = (Get-Content -LiteralPath $accountIdFile -Raw -Encoding UTF8).Trim()
        }
        $hint = if ($existingAcct) { ' [Press Enter to keep existing]' } else { '' }
        $accountIdInput = (Read-Host ("Cloudflare Account ID (32 hex characters)$hint")).Trim()
        if ([string]::IsNullOrEmpty($accountIdInput)) {
            if ($existingAcct) {
                Write-Host 'PRESERVED: Existing Cloudflare Account ID kept unchanged.' -ForegroundColor Yellow
            }
            else {
                Write-Host 'SKIPPED: Cloudflare Account ID not entered.' -ForegroundColor Yellow
            }
        }
        elseif ($accountIdInput -match '^[a-fA-F0-9]{32}$') {
            Set-Content -LiteralPath $accountIdFile -Value $accountIdInput -NoNewline -Encoding UTF8
            Write-Host 'SAVED: CLOUDFLARE_ACCOUNT_ID' -ForegroundColor Green
        }
        else {
            Write-Host 'NOT SAVED: Invalid Cloudflare Account ID format (must be 32 hexadecimal characters).' -ForegroundColor Red
        }
    }
}

# ==============================================================================
# MODE: StatusAll (P0 - Completely offline inspection of all 8 providers)
# ==============================================================================
if ($Mode -eq 'StatusAll') {
    Write-Host '======================================================================' -ForegroundColor Cyan
    Write-Host '            SalesAI Unified 8-Provider Credential Diagnostic          ' -ForegroundColor Cyan
    Write-Host '                 (Completely Offline - Zero Network Requests)          ' -ForegroundColor Cyan
    Write-Host '======================================================================' -ForegroundColor Cyan
    Write-Host ''

    $summary = @()
    foreach ($prov in $allProviders) {
        $file = Get-SecretPath $prov.Env
        $vaultStatus = 'NOT_CONFIGURED'
        $processStatus = 'NOT_LOADED'
        $decryptedSecret = $null
        $detail = ''

        # 1. Local Vault Inspection
        if (Test-Path -LiteralPath $file) {
            try {
                $decryptedSecret = Unprotect-ApiSecret $prov.Env
                if ($decryptedSecret -and $decryptedSecret.Trim().Length -gt 0) {
                    $vaultStatus = 'STORED_AND_READABLE'
                }
                else {
                    $vaultStatus = 'EMPTY_PAYLOAD'
                    $detail = 'DPAPI ciphertext decrypted to empty string'
                }
            }
            catch {
                $vaultStatus = 'UNREADABLE_OR_CORRUPT'
                $detail = $_.Exception.Message
            }
        }
        else {
            $vaultStatus = 'NOT_CONFIGURED'
            $detail = 'No encrypted DPAPI file found'
        }

        # 2. Process Environment Inspection
        $procVal = [Environment]::GetEnvironmentVariable($prov.Env, 'Process')
        if ($procVal -and $procVal.Trim().Length -gt 0) {
            if ($vaultStatus -eq 'STORED_AND_READABLE') {
                if ($procVal -eq $decryptedSecret) {
                    $processStatus = 'PROCESS_LOADED'
                    if (-not $detail) { $detail = 'Process env matches encrypted vault' }
                }
                else {
                    $processStatus = 'STALE_PROCESS_VALUE'
                    $detail = 'Process env differs from encrypted vault (use -RefreshStale)'
                }
            }
            else {
                $processStatus = 'PROCESS_LOADED'
                if (-not $detail) { $detail = 'Process env set; vault not configured/readable' }
            }
        }
        else {
            $processStatus = 'NOT_LOADED'
            if ($vaultStatus -eq 'STORED_AND_READABLE' -and -not $detail) {
                $detail = 'Vault ready; not loaded in current process'
            }
        }

        # Special check for Cloudflare Account ID
        if ($prov.Key -eq 'Cloudflare') {
            $cfAcctStatus = 'NOT_CONFIGURED'
            if (Test-Path -LiteralPath $accountIdFile) {
                $rawId = (Get-Content -LiteralPath $accountIdFile -Raw -Encoding UTF8).Trim()
                if ($rawId -match '^[a-fA-F0-9]{32}$') {
                    $procAcct = [Environment]::GetEnvironmentVariable('CLOUDFLARE_ACCOUNT_ID', 'Process')
                    if ($procAcct -eq $rawId) {
                        $cfAcctStatus = 'VALID_AND_LOADED'
                    }
                    else {
                        $cfAcctStatus = 'VALID_ON_DISK'
                    }
                }
                else {
                    $cfAcctStatus = 'INVALID_FORMAT'
                }
            }
            $detail += " | AccountID: $cfAcctStatus"
        }

        # Clean in-memory decrypted secret reference
        $decryptedSecret = $null
        $procVal = $null

        $summary += [pscustomobject]@{
            Provider           = $prov.Name
            EnvironmentVar     = $prov.Env
            LocalVault         = $vaultStatus
            ProcessEnvironment = $processStatus
            Detail             = $detail
        }
    }

    $summary | Format-Table -AutoSize -Wrap
    Write-Host 'Status legend:' -ForegroundColor Gray
    Write-Host '  STORED_AND_READABLE: Saved in DPAPI vault and decryptable by current Windows user.' -ForegroundColor Gray
    Write-Host '  NOT_CONFIGURED:      No DPAPI file on disk outside repository.' -ForegroundColor Gray
    Write-Host '  PROCESS_LOADED:      Current PowerShell process environment variable is set.' -ForegroundColor Gray
    Write-Host '  STALE_PROCESS_VALUE: Current process value does NOT match the saved vault key.' -ForegroundColor Gray
    Write-Host ''
    return
}

# ==============================================================================
# MODE: LoadAll (P0 - Load all 8 credentials into current process)
# ==============================================================================
if ($Mode -eq 'LoadAll') {
    Write-Host 'SalesAI / Loading all 8 Provider Credentials into PowerShell Process' -ForegroundColor Cyan
    Write-Host 'Environment variables apply to current process and child processes only.'
    if ($RefreshStale) {
        Write-Host '[-RefreshStale active: Overwriting stale process variables with vault values]' -ForegroundColor Yellow
    }
    Write-Host ''

    foreach ($prov in $allProviders) {
        $file = Get-SecretPath $prov.Env
        $existing = [Environment]::GetEnvironmentVariable($prov.Env, 'Process')
        $plain = $null

        if (-not (Test-Path -LiteralPath $file)) {
            if ($existing -and $existing.Trim()) {
                Write-Host ("NOTICE: {0} ({1}) is set in process, but not in DPAPI vault." -f $prov.Name, $prov.Env) -ForegroundColor Gray
            }
            else {
                Write-Host ("SKIPPED: {0} ({1}) not found in vault." -f $prov.Name, $prov.Env) -ForegroundColor Yellow
            }
            continue
        }

        try {
            $plain = Unprotect-ApiSecret $prov.Env
            if (-not $plain -or -not $plain.Trim()) {
                Write-Host ("FAILED: {0} ({1}) decrypted to empty string." -f $prov.Name, $prov.Env) -ForegroundColor Yellow
                continue
            }

            if ($existing -and $existing.Trim()) {
                if ($existing -eq $plain) {
                    Write-Host ("VERIFIED: {0} ({1}) already loaded and matches vault." -f $prov.Name, $prov.Env) -ForegroundColor Green
                }
                elseif ($RefreshStale) {
                    [Environment]::SetEnvironmentVariable($prov.Env, $plain, 'Process')
                    Write-Host ("REFRESHED: {0} ({1}) overwritten from vault (was stale)." -f $prov.Name, $prov.Env) -ForegroundColor Green
                }
                else {
                    Write-Host ("STALE PROCESS VALUE: {0} ({1}) differs from vault! Use -RefreshStale to update." -f $prov.Name, $prov.Env) -ForegroundColor Yellow
                }
            }
            else {
                [Environment]::SetEnvironmentVariable($prov.Env, $plain, 'Process')
                Write-Host ("LOADED: {0} ({1})" -f $prov.Name, $prov.Env) -ForegroundColor Green
            }
        }
        catch {
            Write-Host ("UNABLE TO LOAD: {0} ({1}): {2}" -f $prov.Name, $prov.Env, $_.Exception.Message) -ForegroundColor Yellow
        }
        finally {
            $plain = $null
            $existing = $null
        }
    }

    # Cloudflare Account ID loading
    if (Test-Path -LiteralPath $accountIdFile) {
        $id = (Get-Content -LiteralPath $accountIdFile -Raw -Encoding UTF8).Trim()
        if ($id -match '^[a-fA-F0-9]{32}$') {
            $existingId = [Environment]::GetEnvironmentVariable('CLOUDFLARE_ACCOUNT_ID', 'Process')
            if ($existingId -and $existingId -ne $id -and -not $RefreshStale) {
                Write-Host 'STALE PROCESS VALUE: CLOUDFLARE_ACCOUNT_ID differs from disk! Use -RefreshStale to update.' -ForegroundColor Yellow
            }
            else {
                [Environment]::SetEnvironmentVariable('CLOUDFLARE_ACCOUNT_ID', $id, 'Process')
                Write-Host 'LOADED: CLOUDFLARE_ACCOUNT_ID' -ForegroundColor Green
            }
        }
        else {
            Write-Host 'WARNING: Cloudflare Account ID file contains invalid format; not loaded.' -ForegroundColor Red
        }
    }
    Write-Host ''
    Write-Host 'Process environment update complete. No Windows User/Machine variables were touched.' -ForegroundColor Cyan
    return
}

# ==============================================================================
# MODE: SetupAll & SetupOne (P0 - Setup all or single provider)
# ==============================================================================
if ($Mode -eq 'SetupAll') {
    New-Item -ItemType Directory -Force -Path $secretFolder | Out-Null
    Write-Host '======================================================================' -ForegroundColor Cyan
    Write-Host '           SalesAI Secure Setup: All 8 API Providers                  ' -ForegroundColor Cyan
    Write-Host '   (Input is hidden; blank input preserves previously saved keys)     ' -ForegroundColor Cyan
    Write-Host '======================================================================' -ForegroundColor Cyan
    Write-Host ''

    foreach ($prov in $allProviders) {
        Save-SingleProviderCredential -prov $prov
    }

    Write-Host ''
    Write-Host 'SetupAll completed. Run -Mode StatusAll to inspect status.' -ForegroundColor Cyan
    return
}

if ($Mode -eq 'SetupOne') {
    if (-not $Provider) {
        throw "SetupOne requires -Provider parameter (Gemini, GroqCloud, OpenAI, OpenRouter, Cohere, NVIDIA, Cloudflare, Brave)"
    }
    $target = $allProviders | Where-Object { $_.Key -eq $Provider }
    if (-not $target) {
        throw "Unknown provider '$Provider'. Supported values: Gemini, GroqCloud, OpenAI, OpenRouter, Cohere, NVIDIA, Cloudflare, Brave"
    }

    New-Item -ItemType Directory -Force -Path $secretFolder | Out-Null
    Write-Host ("SalesAI / Secure Single-Provider Setup: {0}" -f $target.Name) -ForegroundColor Cyan
    Write-Host 'Other provider credentials will remain completely untouched.'
    Write-Host 'Blank input will preserve the existing saved key.'
    Write-Host ''

    Save-SingleProviderCredential -prov $target

    Write-Host ''
    Write-Host ("SetupOne for {0} completed. Run -Mode StatusAll to check status." -f $target.Name) -ForegroundColor Cyan
    return
}

# ==============================================================================
# MODE: ProbeAll (P0 - Authenticated metadata probes for 7 LLMs; local check for Brave)
# ==============================================================================
if ($Mode -eq 'ProbeAll') {
    Write-Host '======================================================================' -ForegroundColor Cyan
    Write-Host '          SalesAI Unified 8-Provider Authentication Probe             ' -ForegroundColor Cyan
    Write-Host '         (Metadata GET requests only; zero text generation/Brave API)  ' -ForegroundColor Cyan
    Write-Host '======================================================================' -ForegroundColor Cyan
    Write-Host ''

    $report = @()
    foreach ($prov in $allProviders) {
        $vaultStatus = 'NOT_CONFIGURED'
        $procStatus = 'NOT_LOADED'
        $apiAuth = 'NOT_RUN'
        $detail = ''
        $plain = $null

        # 1. Vault check
        $file = Get-SecretPath $prov.Env
        if (Test-Path -LiteralPath $file) {
            try {
                $plain = Unprotect-ApiSecret $prov.Env
                if ($plain -and $plain.Trim().Length -gt 0) {
                    $vaultStatus = 'STORED_AND_READABLE'
                }
                else {
                    $vaultStatus = 'EMPTY_PAYLOAD'
                }
            }
            catch {
                $vaultStatus = 'UNREADABLE_OR_CORRUPT'
            }
        }

        # 2. Process check
        $procVal = [Environment]::GetEnvironmentVariable($prov.Env, 'Process')
        if ($procVal -and $procVal.Trim().Length -gt 0) {
            $procStatus = 'PROCESS_LOADED'
        }

        # 3. Brave handling in ProbeAll: NO live API search, report local status
        if ($prov.Key -eq 'Brave') {
            if ($vaultStatus -eq 'STORED_AND_READABLE') {
                $apiAuth = 'NOT_LIVE_TESTED'
                $detail = 'Key decryptable. Real Brave search requires explicit authorization (run -Mode ProbeBrave).'
            }
            else {
                $apiAuth = 'NOT_CONFIGURED'
                $detail = 'No Brave API key stored in vault.'
            }
            $report += [pscustomobject]@{
                Provider          = $prov.Name
                LocalVault        = $vaultStatus
                Process           = $procStatus
                APIAuthentication = $apiAuth
                Detail            = $detail
            }
            continue
        }

        # 4. LLM Providers: Metadata GET probes
        if ($vaultStatus -ne 'STORED_AND_READABLE') {
            $apiAuth = 'NOT_CONFIGURED'
            $detail = 'Key not saved in local DPAPI vault; run -Mode SetupOne'
            $report += [pscustomobject]@{
                Provider          = $prov.Name
                LocalVault        = $vaultStatus
                Process           = $procStatus
                APIAuthentication = $apiAuth
                Detail            = $detail
            }
            continue
        }

        try {
            $headers = @{ Accept = 'application/json' }
            if ($prov.Key -eq 'Gemini') {
                $headers['x-goog-api-key'] = $plain
                $uri = 'https://generativelanguage.googleapis.com/v1beta/models?pageSize=100'
            }
            else {
                $headers['Authorization'] = "Bearer $plain"
                switch ($prov.Key) {
                    'GroqCloud'  { $uri = 'https://api.groq.com/openai/v1/models' }
                    'OpenAI'     { $uri = 'https://api.openai.com/v1/models' }
                    'OpenRouter' { $uri = 'https://openrouter.ai/api/v1/key' }
                    'Cohere'     { $uri = 'https://api.cohere.com/v1/models?page_size=20' }
                    'NVIDIA'     { $uri = 'https://integrate.api.nvidia.com/v1/models' }
                    'Cloudflare' {
                        if (-not (Test-Path -LiteralPath $accountIdFile)) {
                            throw 'Cloudflare Account ID missing'
                        }
                        $id = (Get-Content -LiteralPath $accountIdFile -Raw -Encoding UTF8).Trim()
                        if ($id -notmatch '^[a-fA-F0-9]{32}$') {
                            throw 'Cloudflare Account ID invalid'
                        }
                        $uri = "https://api.cloudflare.com/client/v4/accounts/$id/ai/models/search?per_page=20"
                    }
                }
            }

            $response = Invoke-RestMethod -Uri $uri -Method Get -Headers $headers -TimeoutSec 30 -ErrorAction Stop
            $apiAuth = 'OK'

            switch ($prov.Key) {
                'Gemini'     { $detail = "Models listed: $(Get-ReturnedCount $response.models) (GET metadata only)" }
                'GroqCloud'  { $detail = "Models listed: $(Get-ReturnedCount $response.data) (GET metadata only)" }
                'OpenAI'     { $detail = "Models listed: $(Get-ReturnedCount $response.data) (GET metadata only)" }
                'OpenRouter' {
                    if ($null -ne $response.data.is_free_tier) {
                        $detail = "Authenticated key info; free-tier key: $($response.data.is_free_tier)"
                    } else {
                        $detail = 'Authenticated key info returned'
                    }
                }
                'Cohere'     { $detail = "Models listed: $(Get-ReturnedCount $response.models) (GET metadata only)" }
                'NVIDIA'     { $detail = "Catalog entries: $(Get-ReturnedCount $response.data); inference access not verified" }
                'Cloudflare' {
                    if ($response.success -ne $true) {
                        $apiAuth = 'CHECK'
                        $detail = 'Cloudflare did not report success'
                    } else {
                        $detail = "Catalog entries: $(Get-ReturnedCount $response.result) (GET metadata only)" }
                }
            }
        }
        catch {
            $apiAuth = 'ERROR'
            if ($_.Exception.Response -and $null -ne $_.Exception.Response.StatusCode) {
                $code = [int]$_.Exception.Response.StatusCode
                $detail = "HTTP $code; check token, endpoint, account permissions or quota"
            }
            elseif ($_.Exception.Message -like '*Cloudflare Account ID*') {
                $detail = 'Missing or invalid Cloudflare Account ID'
            }
            else {
                $detail = 'Network/TLS error or invalid endpoint'
            }
        }
        finally {
            $plain = $null
            $headers = $null
            $response = $null
        }

        $report += [pscustomobject]@{
            Provider          = $prov.Name
            LocalVault        = $vaultStatus
            Process           = $procStatus
            APIAuthentication = $apiAuth
            Detail            = $detail
        }
    }

    $report | Format-Table -AutoSize -Wrap
    Write-Host 'CRITICAL DISTINCTIONS:' -ForegroundColor Yellow
    Write-Host '1. LocalVault (STORED_AND_READABLE) confirms local DPAPI encryption only.' -ForegroundColor Gray
    Write-Host '2. APIAuthentication (OK) confirms metadata endpoint accessibility only.' -ForegroundColor Gray
    Write-Host '3. Model listings DO NOT prove that text generation or inference is funded, enabled, or free.' -ForegroundColor Gray
    Write-Host '4. Brave Search is NOT queried during ProbeAll to prevent unintentional billing.' -ForegroundColor Gray
    Write-Host ''
    return
}

# ==============================================================================
# MODE: ProbeBrave (P1 - Optional explicit single-request verification)
# ==============================================================================
if ($Mode -eq 'ProbeBrave') {
    Write-Host '======================================================================' -ForegroundColor Cyan
    Write-Host '          SalesAI Brave Search Live API Verification Probe            ' -ForegroundColor Cyan
    Write-Host '======================================================================' -ForegroundColor Cyan
    Write-Host ''

    $braveVar = 'BRAVE_SEARCH_API_KEY'
    $braveFile = Get-SecretPath $braveVar
    if (-not (Test-Path -LiteralPath $braveFile)) {
        Write-Host 'ABORTED: No Brave Search API key found in DPAPI vault. Run -Mode SetupOne -Provider Brave.' -ForegroundColor Red
        return
    }

    $plainKey = $null
    try {
        $plainKey = Unprotect-ApiSecret $braveVar
    }
    catch {
        Write-Host "ABORTED: Unable to decrypt saved Brave key: $($_.Exception.Message)" -ForegroundColor Red
        return
    }

    if (-not $plainKey -or -not $plainKey.Trim()) {
        Write-Host 'ABORTED: Decrypted Brave API key is empty.' -ForegroundColor Red
        return
    }

    Write-Host 'PREFLIGHT BILLING AND USAGE NOTICE:' -ForegroundColor Yellow
    Write-Host '  Target Service:        Brave Web Search API'
    Write-Host '  Target Endpoint:       https://api.search.brave.com/res/v1/web/search'
    Write-Host '  Estimated Request Cost: ~$0.005 USD (within free tier allowance if active)'
    Write-Host '  Bounding Limits:       Max 1 request, count=1 result, timeout=10s, NO retries'
    Write-Host '  Safety Isolation:      Zero database writes, zero candidate creation'
    Write-Host ''

    $confirm = $AuthorizeBrave
    if ([string]::IsNullOrEmpty($confirm)) {
        $confirm = Read-Host 'Do you authorize sending exactly ONE live search request to Brave API? (type YES to proceed)'
    }
    if ($confirm.Trim() -ne 'YES') {
        Write-Host 'CANCELLED: Operator did not explicitly authorize live Brave API request.' -ForegroundColor Gray
        $plainKey = $null
        return
    }

    Write-Host ''
    Write-Host 'Dispatching single bounded search request to Brave Search API...' -ForegroundColor Cyan
    $headers = @{
        'Accept' = 'application/json'
        'X-Subscription-Token' = $plainKey
    }
    $testQuery = 'site:.it "BIM" test verification'
    $uri = "https://api.search.brave.com/res/v1/web/search?q=$([System.Uri]::EscapeDataString($testQuery))&count=1"

    $startTime = [System.DateTime]::UtcNow
    try {
        $response = Invoke-RestMethod -Uri $uri -Method Get -Headers $headers -TimeoutSec 10 -ErrorAction Stop
        $latencyMs = [math]::Round(([System.DateTime]::UtcNow - $startTime).TotalMilliseconds)
        $resultCount = Get-ReturnedCount $response.web.results

        Write-Host 'BRAVE API PROBE SUCCESSFUL' -ForegroundColor Green
        Write-Host "  HTTP Status:       200 OK"
        Write-Host "  Response Latency:  ${latencyMs} ms"
        Write-Host "  Results Returned:  $resultCount"
        Write-Host "  Authentication:    VALID"
        Write-Host "  Operational DB:    Zero writes (isolated diagnostic)"
    }
    catch {
        Write-Host 'BRAVE API PROBE FAILED' -ForegroundColor Red
        if ($_.Exception.Response -and $null -ne $_.Exception.Response.StatusCode) {
            $code = [int]$_.Exception.Response.StatusCode
            if ($code -eq 401 -or $code -eq 403) {
                Write-Host "  HTTP Status:       $code (Authentication / Permission Denied)" -ForegroundColor Red
                Write-Host "  Diagnostic:        Invalid or expired subscription token. Check Brave Search Developer dashboard."
            }
            elseif ($code -eq 429) {
                Write-Host "  HTTP Status:       429 (Rate Limit / Quota Exceeded)" -ForegroundColor Yellow
                Write-Host "  Diagnostic:        Monthly free quota exhausted or rate limit hit."
            }
            else {
                Write-Host "  HTTP Status:       $code" -ForegroundColor Red
            }
        }
        else {
            Write-Host "  Error Detail:      $($_.Exception.Message)" -ForegroundColor Red
        }
    }
    finally {
        $plainKey = $null
        $headers = $null
    }
    return
}

# ==============================================================================
# BACKWARD COMPATIBILITY MODES
# ==============================================================================

if ($Mode -eq 'Setup') {
    New-Item -ItemType Directory -Force -Path $secretFolder | Out-Null
    Write-Host 'SalesAI / Secure local API setup (Legacy 7 LLM Providers)' -ForegroundColor Cyan
    Write-Host 'Enter keys one by one. Input is hidden. Blank input skips.'
    Write-Host ''

    foreach ($legacyItem in $legacyProviders) {
        $secure = Read-Host ("API key for {0} ({1})" -f $legacyItem.Name, $legacyItem.Env) -AsSecureString
        try {
            if ($secure.Length -eq 0) {
                Write-Host ("SKIPPED: {0} (empty input)" -f $legacyItem.Name) -ForegroundColor Yellow
                continue
            }
            Protect-ApiSecret -variableName $legacyItem.Env -secure $secure
            Write-Host ("SAVED: {0}" -f $legacyItem.Name) -ForegroundColor Green
        }
        finally {
            if ($null -ne $secure) { $secure.Dispose() }
        }
    }

    Write-Host ''
    $accountId = (Read-Host 'Cloudflare Account ID (32 hexadecimal characters)').Trim()
    if ($accountId -match '^[a-fA-F0-9]{32}$') {
        Set-Content -LiteralPath $accountIdFile -Value $accountId -NoNewline -Encoding UTF8
        Write-Host 'SAVED: CLOUDFLARE_ACCOUNT_ID' -ForegroundColor Green
    }
    else {
        Write-Host 'NOT SAVED: Invalid Cloudflare Account ID; it must be 32 hex characters.' -ForegroundColor Yellow
    }

    Write-Host ''
    Write-Host 'Setup completed. Run with -Mode StatusAll or -Mode Probe.' -ForegroundColor Cyan
    return
}

if ($Mode -eq 'Load') {
    foreach ($legacyItem in $legacyProviders) {
        try {
            $plain = Unprotect-ApiSecret $legacyItem.Env
            [Environment]::SetEnvironmentVariable($legacyItem.Env, $plain, 'Process')
            Write-Host ("Loaded into this PowerShell session: {0}" -f $legacyItem.Env) -ForegroundColor Green
        }
        catch {
            Write-Host ("Unable to load: {0}" -f $legacyItem.Env) -ForegroundColor Yellow
        }
        finally { $plain = $null }
    }
    if (Test-Path -LiteralPath $accountIdFile) {
        $id = (Get-Content -LiteralPath $accountIdFile -Raw -Encoding UTF8).Trim()
        if ($id -match '^[a-fA-F0-9]{32}$') {
            [Environment]::SetEnvironmentVariable('CLOUDFLARE_ACCOUNT_ID', $id, 'Process')
            Write-Host 'Loaded into this PowerShell session: CLOUDFLARE_ACCOUNT_ID' -ForegroundColor Green
        }
    }
    Write-Host 'Environment variables last for the current PowerShell process and child processes.'
    return
}

if ($Mode -eq 'Probe') {
    Write-Host 'SalesAI / Seven-provider metadata-only probe (Legacy)' -ForegroundColor Cyan
    Write-Host 'No text-generation requests will be sent.'
    Write-Host ''

    $report = @()
    foreach ($legacyItem in $legacyProviders) {
        $status = 'ERROR'
        $detail = ''
        $plain = $null
        $headers = $null

        try {
            $plain = Unprotect-ApiSecret $legacyItem.Env
            $headers = @{ Accept = 'application/json' }
            if ($legacyItem.Name -eq 'Google Gemini') {
                $headers['x-goog-api-key'] = $plain
                $uri = 'https://generativelanguage.googleapis.com/v1beta/models?pageSize=100'
            }
            else {
                $headers['Authorization'] = "Bearer $plain"
                switch ($legacyItem.Name) {
                    'GroqCloud'  { $uri = 'https://api.groq.com/openai/v1/models' }
                    'OpenAI'     { $uri = 'https://api.openai.com/v1/models' }
                    'OpenRouter' { $uri = 'https://openrouter.ai/api/v1/key' }
                    'Cohere'     { $uri = 'https://api.cohere.com/v1/models?page_size=20' }
                    'NVIDIA NIM' { $uri = 'https://integrate.api.nvidia.com/v1/models' }
                    'Cloudflare' {
                        if (-not (Test-Path -LiteralPath $accountIdFile)) {
                            throw 'Cloudflare Account ID missing'
                        }
                        $id = (Get-Content -LiteralPath $accountIdFile -Raw -Encoding UTF8).Trim()
                        if ($id -notmatch '^[a-fA-F0-9]{32}$') {
                            throw 'Cloudflare Account ID invalid'
                        }
                        $uri = "https://api.cloudflare.com/client/v4/accounts/$id/ai/models/search?per_page=20"
                    }
                }
            }

            $response = Invoke-RestMethod -Uri $uri -Method Get -Headers $headers -TimeoutSec 30 -ErrorAction Stop
            $status = 'OK'

            switch ($legacyItem.Name) {
                'Google Gemini' { $detail = "Models returned: $(Get-ReturnedCount $response.models) (first page)" }
                'GroqCloud'     { $detail = "Models returned: $(Get-ReturnedCount $response.data)" }
                'OpenAI'        { $detail = "Models returned: $(Get-ReturnedCount $response.data)" }
                'OpenRouter'    {
                    if ($null -ne $response.data.is_free_tier) {
                        $detail = "Authenticated; free-tier key: $($response.data.is_free_tier)"
                    }
                    else { $detail = 'Authenticated; key metadata returned' }
                }
                'Cohere'        { $detail = "Models returned: $(Get-ReturnedCount $response.models) (first page)" }
                'NVIDIA NIM'    { $detail = "Catalog models returned: $(Get-ReturnedCount $response.data); access to inference not verified" }
                'Cloudflare'    {
                    if ($response.success -ne $true) {
                        $status = 'CHECK'
                        $detail = 'Cloudflare did not report success'
                    }
                    else { $detail = "Catalog entries: $(Get-ReturnedCount $response.result) (first page)" }
                }
            }
        }
        catch {
            $status = 'ERROR'
            if ($_.Exception.Response -and $null -ne $_.Exception.Response.StatusCode) {
                $code = [int]$_.Exception.Response.StatusCode
                $detail = "HTTP $code; check token, endpoint, account permissions or quota"
            }
            elseif ($_.Exception.Message -like 'Missing locally saved key:*') {
                $detail = 'Key not saved locally; rerun Setup'
            }
            elseif ($_.Exception.Message -like 'Cloudflare Account ID*') {
                $detail = 'Missing or invalid Cloudflare Account ID; rerun Setup'
            }
            else {
                $detail = 'Request failed; check network, TLS and local encrypted key'
            }
        }
        finally {
            $plain = $null
            $headers = $null
            $response = $null
        }

        $report += [pscustomobject]@{
            Provider = $legacyItem.Name
            Status = $status
            Detail = $detail
        }
    }

    $report | Format-Table -AutoSize -Wrap
    Write-Host ''
    Write-Host 'Metadata probes do NOT verify billing, free credits, or permission to generate text.' -ForegroundColor Yellow
    return
}

if ($Mode -eq 'SetupBrave') {
    New-Item -ItemType Directory -Force -Path $secretFolder | Out-Null
    Write-Host 'SalesAI / Secure Brave Search API Credential Setup' -ForegroundColor Cyan
    Write-Host 'Enter your Brave Search API key. Input will be hidden.'
    Write-Host 'Existing LLM provider credentials will not be modified.'
    Write-Host ''

    $braveVar = 'BRAVE_SEARCH_API_KEY'
    $braveFile = Get-SecretPath $braveVar
    $secure = Read-Host ("Brave Search API Key ({0})" -f $braveVar) -AsSecureString
    try {
        if ($null -eq $secure -or $secure.Length -eq 0) {
            if (Test-Path -LiteralPath $braveFile) {
                Write-Host 'PRESERVED: Existing Brave Search API Key kept unchanged (empty input).' -ForegroundColor Yellow
            }
            else {
                Write-Host 'SKIPPED: No Brave Search API Key provided and none previously saved.' -ForegroundColor Yellow
            }
            return
        }

        Protect-ApiSecret -variableName $braveVar -secure $secure
        Write-Host 'SAVED: BRAVE_SEARCH_API_KEY (DPAPI user-scoped encrypted)' -ForegroundColor Green
        Write-Host ("Secret file: {0}" -f $braveFile)
    }
    finally {
        if ($null -ne $secure) { $secure.Dispose() }
    }
    return
}

if ($Mode -eq 'LoadBrave') {
    $braveVar = 'BRAVE_SEARCH_API_KEY'
    $braveFile = Get-SecretPath $braveVar

    $existing = [Environment]::GetEnvironmentVariable($braveVar, 'Process')
    if ($existing -and $existing.Trim()) {
        Write-Host ("Preserving existing process-level {0}." -f $braveVar) -ForegroundColor Cyan
        return
    }

    if (-not (Test-Path -LiteralPath $braveFile)) {
        Write-Host ("Brave secret file not found: {0}" -f $braveFile) -ForegroundColor Yellow
        return
    }

    $plain = $null
    try {
        $plain = Unprotect-ApiSecret $braveVar
        if ($plain -and $plain.Trim()) {
            [Environment]::SetEnvironmentVariable($braveVar, $plain, 'Process')
            Write-Host ("Loaded into this PowerShell session: {0}" -f $braveVar) -ForegroundColor Green
        }
        else {
            Write-Host ("Failed: Decrypted secret for {0} is empty." -f $braveVar) -ForegroundColor Yellow
        }
    }
    catch {
        Write-Host ("Unable to decrypt or load: {0} ({1})" -f $braveVar, $_.Exception.Message) -ForegroundColor Yellow
    }
    finally {
        $plain = $null
    }
    return
}

if ($Mode -eq 'StatusBrave') {
    $braveVar = 'BRAVE_SEARCH_API_KEY'
    $braveFile = Get-SecretPath $braveVar

    Write-Host 'SalesAI / Brave Search Credential Diagnostic' -ForegroundColor Cyan
    Write-Host 'Verifying local DPAPI file status without making network calls or printing secret.'
    Write-Host ''

    $statusObj = [pscustomobject]@{
        Variable = $braveVar
        FilePath = $braveFile
        FileExists = $false
        Decryptable = $false
        Status = 'NOT_CONFIGURED'
        Detail = 'No encrypted DPAPI credential found on disk. Run -Mode SetupBrave or SetupOne -Provider Brave.'
    }

    if (Test-Path -LiteralPath $braveFile) {
        $statusObj.FileExists = $true
        $plain = $null
        try {
            $plain = Unprotect-ApiSecret $braveVar
            if ($plain -and $plain.Trim().Length -gt 0) {
                $statusObj.Decryptable = $true
                $statusObj.Status = 'STORED_AND_READABLE'
                $statusObj.Detail = 'Encrypted credential is valid and readable by the current Windows user (API authentication not verified).'
            }
            else {
                $statusObj.Status = 'EMPTY_PAYLOAD'
                $statusObj.Detail = 'Encrypted credential decrypted to empty string; rerun setup.'
            }
        }
        catch {
            $statusObj.Status = 'UNREADABLE_OR_CORRUPT'
            $statusObj.Detail = "Encrypted file exists but cannot be decrypted by current user: $($_.Exception.Message)"
        }
        finally {
            $plain = $null
        }
    }

    $statusObj | Format-List
    if ($statusObj.Status -eq 'STORED_AND_READABLE') {
        Write-Host 'Local encrypted secret is STORED_AND_READABLE.' -ForegroundColor Green
        Write-Host 'Note: This confirms local DPAPI decryption, not Brave Search API credit/authentication validity.' -ForegroundColor Gray
    }
    else {
        Write-Host ("Brave secret status: {0}" -f $statusObj.Status) -ForegroundColor Yellow
    }
    return
}

if ($Mode -eq 'LoadGemini') {
    $geminiVar = 'GEMINI_API_KEY'
    $geminiFile = Get-SecretPath $geminiVar

    $existing = [Environment]::GetEnvironmentVariable($geminiVar, 'Process')
    if ($existing -and $existing.Trim()) {
        Write-Host ('Preserving existing process-level {0}.' -f $geminiVar) -ForegroundColor Cyan
        return
    }

    if (-not (Test-Path -LiteralPath $geminiFile)) {
        Write-Host ('Gemini secret file not found: {0}' -f $geminiFile) -ForegroundColor Yellow
        return
    }

    $plain = $null
    try {
        $plain = Unprotect-ApiSecret $geminiVar
        if ($plain -and $plain.Trim()) {
            [Environment]::SetEnvironmentVariable($geminiVar, $plain, 'Process')
            Write-Host ('Loaded into this PowerShell session: {0}' -f $geminiVar) -ForegroundColor Green
        }
        else {
            Write-Host ('Failed: Decrypted secret for {0} is empty.' -f $geminiVar) -ForegroundColor Yellow
        }
    }
    catch {
        Write-Host ('Unable to decrypt or load: {0} ({1})' -f $geminiVar, $_.Exception.Message) -ForegroundColor Yellow
    }
    finally {
        $plain = $null
    }
    return
}
