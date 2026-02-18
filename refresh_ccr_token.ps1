Write-Host "Checking for Qwen credentials..."
$credsPath = "$env:USERPROFILE\.qwen\oauth_creds.json"
$configPath = "$env:USERPROFILE\.claude-code-router\config.json"

if (-not (Test-Path $credsPath)) {
    Write-Warning "Qwen credentials not found. Please run 'qwen' to authenticate first."
    exit
}

$creds = Get-Content $credsPath | ConvertFrom-Json
$token = $creds.access_token

if (-not $token) {
    Write-Error "Token not found in credentials file."
    exit
}

Write-Host "Updating CCR config with new token..."
if (Test-Path $configPath) {
    $json = Get-Content $configPath -Raw | ConvertFrom-Json
    # Assuming the first provider is Qwen as per setup
    $json.Providers[0].api_key = $token
    $json | ConvertTo-Json -Depth 10 | Set-Content $configPath
    Write-Host "Config updated."
} else {
    Write-Error "CCR config not found at $configPath. Please run initial setup."
    exit
}

Write-Host "Restarting Claude Code Router..."
try {
    ccr restart
} catch {
    Write-Warning "Could not restart CCR automatically. Please run 'ccr restart'."
}

Write-Host "Token refresh complete! You can now use 'ccr code'."
