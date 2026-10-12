# Checks the prerequisites and prints what's missing. Changes nothing.
$root = Split-Path $PSScriptRoot -Parent
$cfg = Get-Content "$root\config.toml" -Raw
function Get-Setting($key) { [regex]::Match($cfg, "(?m)^$key\s*=\s*['""]?([^'""\r\n]+)").Groups[1].Value }
$ibc = Get-Setting 'ibc_path'
$tws = Get-Setting 'tws_path'
$ver = Get-Setting 'major_version'
$ok = $true

function Check($name, $pass, $help) {
    if ($pass) { Write-Host "[ OK ] $name" -ForegroundColor Green }
    else { Write-Host "[MISS] $name" -ForegroundColor Red; Write-Host "       $help"; $script:ok = $false }
}

Check "IB Gateway $ver (offline installer) at $tws\ibgateway\$ver" (Test-Path "$tws\ibgateway\$ver\jars") @"
Download the Windows *offline* (stable) installer: https://www.interactivebrokers.com/en/trading/ibgateway-latest.php
       IBC does NOT work with the self-updating installer. If you install another version, set major_version in config.toml
       (for example 10.45.x = "1045").
"@
Check "IBC at $ibc" (Test-Path "$ibc\scripts\StartIBC.bat") @"
Download IBCWin-<version>.zip from https://github.com/IbcAlpha/IBC/releases. Right-click the zip > Properties > Unblock,
       then extract it to $ibc. You don't need to edit its config.ini or StartGateway.bat; the app writes its own settings.
"@
Check "uv (Python manager)" (Get-Command uv -ErrorAction SilentlyContinue) "Install it from https://docs.astral.sh/uv/getting-started/installation/"
Check "Node.js (frontend build)" (Get-Command npm -ErrorAction SilentlyContinue) "Install it from https://nodejs.org"
Check "Edge or Chrome" ((Test-Path "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe") -or (Test-Path "$env:ProgramFiles\Google\Chrome\Application\chrome.exe")) "Install Microsoft Edge."
Check "Frontend built" (Test-Path "$root\frontend\dist\index.html") "Run: npm --prefix frontend install; npm --prefix frontend run build"

if ($ok) { Write-Host "`nAll set. Run launcher\make-shortcut.ps1 once, then pin the Desktop shortcut." -ForegroundColor Green }
