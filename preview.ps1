# Local preview server for the Referral Tier dashboard.
# Run this any time to review the current local index.html + data/*.json
# in your browser BEFORE those changes get pushed to GitHub.
#
# Usage: double-click preview.bat, or run `./preview.ps1` from this folder.

$port = 8080
$root = $PSScriptRoot

Write-Host "Starting local preview server on http://localhost:$port ..." -ForegroundColor Cyan
Write-Host "(this window is the server log - close it, or press Ctrl+C inside it, to stop)" -ForegroundColor DarkGray

$argList = @('-NoExit', '-Command', "Set-Location '$root'; python -m http.server $port")
Start-Process powershell -ArgumentList $argList -WorkingDirectory $root
Start-Sleep -Seconds 1
Start-Process "http://localhost:$port"
