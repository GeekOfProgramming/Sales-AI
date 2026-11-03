# SalesAI metadata-only API-key validation; no chat completions, no LLM inference.
# Run in PowerShell:  .\SALESAI-PROBE-FOUR-PROVIDERS.ps1
$ErrorActionPreference = 'Stop'
$providers = @(
    @{ Name = 'Google Gemini'; Url = 'https://generativelanguage.googleapis.com/v1beta/openai/models'; Pattern = '^gemini-(2\.5-flash-lite|3\.8-flash)' },
    @{ Name = 'GroqCloud'; Url = 'https://api.groq.com/openai/v1/models'; Pattern = '^openai/gpt-oss-(20b|120b)$' },
    @{ Name = 'xAI Grok'; Url = 'https://api.x.ai/v1/models'; Pattern = '^grok-' },
    @{ Name = 'OpenAI'; Url = 'https://api.openai.com/v1/models'; Pattern = '^gpt-6-luna$' }
)
Write-Host 'Metadata-only probe. No text-generation requests are sent.'
foreach ($p in $providers) {
    Write-Host "`nProvider: $($p.Name)"
    $secureKey = Read-Host 'Enter API Key (hidden)' -AsSecureString
    $plainKey = [System.Net.NetworkCredential]::new('', $secureKey).Password
    $headers = @{ Authorization = "Bearer $plainKey" }
    try {
        $response = Invoke-RestMethod -Uri $p.Url -Method Get -Headers $headers -TimeoutSec 20 -ErrorAction Stop
        $allModels = @($response.data | Where-Object { $_ -and $_.id } | ForEach-Object { [string]$_.id })
        $matchingModels = @($allModels | Where-Object { $_ -match $p.Pattern } | Select-Object -First 5)
        Write-Host "Authentication/API endpoint: OK; model count: $($allModels.Count)"
        if ($matchingModels.Count -gt 0) {
            Write-Host ('Relevant model IDs: ' + ($matchingModels -join ', '))
        } else {
            Write-Host 'No expected model ID found in listing. Check provider dashboard/access.'
        }
    } catch {
        $statusCode = 'Unknown'
        try { $statusCode = [int]$_.Exception.Response.StatusCode } catch {}
        Write-Host "Authentication/API endpoint: FAILED; HTTP status: $statusCode"
        Write-Host 'Check key/project/model permissions, or connectivity. Raw error omitted for secret safety.'
    } finally {
        $headers.Clear()
        Remove-Variable secureKey, plainKey, headers -ErrorAction SilentlyContinue
    }
}
Write-Host "`nProbe complete. This did NOT validate free credits, billing, or permission to generate paid requests."
