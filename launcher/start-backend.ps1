# Starts the backend hidden. Used by the launcher and by the in-app Restart button.
# IB Gateway is a separate process, so restarting the backend keeps you logged in.
# -OldPid: the backend being replaced. We wait for it to exit (and force it after 15 s), because a
# process that closed its port but is still alive keeps its IBKR client id and blocks the new one.
param([int]$OldPid = 0)
$root = Split-Path $PSScriptRoot -Parent
$port = (Select-String -Path "$root\config.toml" -Pattern '^port\s*=\s*(\d+)').Matches[0].Groups[1].Value
$data = Join-Path $env:LOCALAPPDATA 'TradingAssistant'
New-Item -ItemType Directory -Force "$data\logs" | Out-Null

# On Restart, the old backend is still shutting down: wait for it to exit (force it after 15 s).
if ($OldPid) {
    Wait-Process -Id $OldPid -Timeout 15 -ErrorAction SilentlyContinue
    Stop-Process -Id $OldPid -Force -ErrorAction SilentlyContinue
}
# Never start a second listener on the port (Windows allows that because uvicorn sets SO_REUSEADDR).
$deadline = (Get-Date).AddSeconds(10)
while ((Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) -and (Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 300
}
if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) { exit 1 }

# Rebuild the UI if any source file is newer than the last build.
$dist = "$root\frontend\dist\index.html"
$newest = Get-ChildItem "$root\frontend\src", "$root\frontend\index.html" -Recurse -File | Sort-Object LastWriteTime | Select-Object -Last 1
if (-not (Test-Path $dist) -or $newest.LastWriteTime -gt (Get-Item $dist).LastWriteTime) {
    npm --prefix "$root\frontend" run build *> "$data\logs\frontend-build.log"
}

Start-Process uv -ArgumentList 'run', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', $port, '--timeout-graceful-shutdown', '5' `
    -WorkingDirectory "$root\backend" -WindowStyle Hidden `
    -RedirectStandardOutput "$data\logs\backend.out.log" -RedirectStandardError "$data\logs\backend.err.log"
