# One-click launcher: start the backend if needed, open the app window, and shut down cleanly when it closes.
# Started by the "Trading Assistant" shortcut (see make-shortcut.ps1).
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$port = (Select-String -Path "$root\config.toml" -Pattern '^port\s*=\s*(\d+)').Matches[0].Groups[1].Value
$url = "http://127.0.0.1:$port"
$data = Join-Path $env:LOCALAPPDATA 'TradingAssistant'
New-Item -ItemType Directory -Force "$data\logs" | Out-Null
$shell = New-Object -ComObject WScript.Shell

function Test-Up {
    try { (Invoke-WebRequest "$url/api/health" -UseBasicParsing -TimeoutSec 3).StatusCode -eq 200 } catch { $false }
}

# Windows lets two processes listen on the same port (uvicorn sets SO_REUSEADDR), so a slow health
# check must never start a second backend. If anything is listening, treat the backend as running.
$listening = [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)

if ($listening -or (Test-Up)) {
    # Already running. Focus the window if it's open. If it was closed while positions were open,
    # the backend kept running, so fall through and reopen the window.
    if ($shell.AppActivate('Trading Assistant')) { exit }
} else {
    & "$PSScriptRoot\start-backend.ps1"
    $deadline = (Get-Date).AddSeconds(60)  # includes a UI rebuild if sources changed
    while (-not (Test-Up)) {
        if ((Get-Date) -gt $deadline) {
            $shell.Popup("The backend didn't start. See $data\logs\backend.err.log", 0, 'Trading Assistant', 16) | Out-Null
            exit 1
        }
        Start-Sleep -Milliseconds 300
    }
}

$browser = @(
    "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
    "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

# A dedicated profile makes this its own browser process, so WaitForExit returns when the window closes.
$win = Start-Process $browser -PassThru -ArgumentList "--app=$url", "--user-data-dir=$data\browser", '--no-first-run', '--window-size=1600,950'
$win.WaitForExit()

# The backend shuts itself and Gateway down only if IBKR confirms you're flat with no working orders.
# Otherwise it keeps running so force-flat still happens. Click the icon to reopen the window.
try { Invoke-RestMethod -Method Post "$url/api/window-closed" -TimeoutSec 10 | Out-Null } catch {}
