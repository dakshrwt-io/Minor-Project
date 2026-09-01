# Starts the gateway, warning when the configured provider's key is missing.
# The app loads a repository-root .env file at startup; real environment
# variables take precedence over it.
# Run from the repository root:  .\start_gateway.ps1
$ErrorActionPreference = "Stop"

$provider = if ($env:AGENT_MODEL_PROVIDER) { $env:AGENT_MODEL_PROVIDER } else { "anthropic" }
$keyVar = if ($provider -eq "deepseek") { "DEEPSEEK_API_KEY" } else { "ANTHROPIC_API_KEY" }

if ([Environment]::GetEnvironmentVariable($keyVar)) {
    python -m uvicorn app.main:app --reload
    exit $LASTEXITCODE
}

$dotEnvPath = Join-Path (Get-Location) ".env"
$keyInDotEnv = $false
if (Test-Path -LiteralPath $dotEnvPath) {
    $keyLine = Get-Content -LiteralPath $dotEnvPath |
        Where-Object { $_ -match "^\s*$keyVar\s*=" } |
        Select-Object -First 1
    $keyInDotEnv = [bool]$keyLine
}

if ($keyInDotEnv) {
    Write-Host "Found $keyVar in .env; the app will load it at startup." -ForegroundColor Green
    python -m uvicorn app.main:app --reload
    exit $LASTEXITCODE
}

Write-Host "WARNING: $keyVar not found in this terminal or in .env (provider: $provider)." -ForegroundColor Yellow
Write-Host "  Set it, for example:" -ForegroundColor Yellow
Write-Host "  `$env:$keyVar = `"sk-...`"" -ForegroundColor Cyan
Write-Host "  or add $keyVar to .env in the repository root, then re-run this script." -ForegroundColor Yellow
exit 1
