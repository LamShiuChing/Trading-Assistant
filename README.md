# Trading Assistant

Local-only intraday trading assistant for IBKR. It runs as one process on `127.0.0.1:8765`, and the UI opens in a chromeless Edge window.
It defaults to **PAPER**. It never places trades on its own, except the end-of-day safety force-flat (phase 2).

Status: **phase 1b**. Done: login through IBC, connection state, PAPER/LIVE badge, launcher, the live chart, and the read-only portfolio.
Next: phase 2 (order ticket, risk manager, kill switch, force-flat).

## Chart
- Live 1-min IBKR bars, including pre-market and after-hours, over 3 days. 5m and 15m are built from the 1-min bars.
- **VWAP** restarts at each day's 09:30.
- **Levels:** PDH, PDL, and PDC from the prior day's 09:30–16:00. PMH and PML from today's 04:00–09:30.
- **Opening ranges:** OR5 (pink) and OR15 (blue) boxes. A box is dashed while its range is still forming.
- **Keys:** `/` searches for a symbol, and `1`, `5`, `F` switch timeframe. Click a symbol in the bottom tables to chart it.

## Setup (once)
1. Check the prerequisites: `powershell -ExecutionPolicy Bypass -File launcher\check-setup.ps1`.
   - **IB Gateway, offline installer.** IBC does not work with the self-updating build. Set `major_version` in `config.toml`
     to the installed folder under `C:\Jts\ibgateway\` (currently `1045`).
   - **IBC**: download `IBCWin-<ver>.zip` from https://github.com/IbcAlpha/IBC/releases. Right-click it, choose Properties,
     tick **Unblock**, then extract it to `C:\IBC`. Leave its `config.ini` and `StartGateway.bat` alone; the app writes its own settings.
     IBC renames `ibgateway.exe` to `ibgateway1.exe` so that Gateway can only be started through IBC. This is standard IBC behavior.
2. Build the UI: `npm --prefix frontend install` then `npm --prefix frontend run build`. The launcher also builds it if `dist/` is missing.
3. Create the shortcut: `powershell -ExecutionPolicy Bypass -File launcher\make-shortcut.ps1`.
   This puts **Trading Assistant** on your Desktop. To pin it, right-click the shortcut and choose **Pin to taskbar**.
   The running window gets its own taskbar button; that's an Edge app-window limitation.

## Daily use
- **Click the icon.** It starts the backend, waits for the health check, and opens the window. If the app is already running, a click focuses the window.
- **Log in.** Enter your username and password, pick PAPER or LIVE, then approve the 2FA alert in IBKR Mobile.
  - Progress shows as: Launching → Logging in → Waiting for 2FA → Connected → API ready.
  - If Gateway is already logged in, the login screen is skipped.
- **Errors:**
  - Wrong password.
  - 2FA timeout (about 3 min).
  - Too many attempts.
  - Session already open elsewhere. This one offers **Take over**, which logs out the other session.
- **LIVE** needs `allow_live = true` in `config.toml` *and* the LIVE toggle at login. After connecting, the app checks the account
  ID against the mode. On a mismatch it blocks all orders and shows a red banner.
- **Disconnects** (IBKR's daily restart, network) show a yellow banner while the app reconnects by itself. On every reconnect, open orders are
  re-read from IBKR before the app returns to "Connected". If Gateway exits (for example, the weekly Sunday re-auth), the login screen comes back.
- **Quit and closing the window** shut down the backend and Gateway, but only when IBKR confirms you're flat with no working orders.
  If you aren't, Quit lists what's open. Closing the window leaves the backend running in the background so force-flat still happens.
  Click the icon to reopen.

## Password handling
IBC can only read credentials from its `config.ini` or from command-line args, and command-line args are visible in Task Manager. So the app writes
`%LOCALAPPDATA%\TradingAssistant\run\ibc.ini` with an ACL that only your user can read. IBC reads this file once at startup and prints `End IBC Settings`.
The moment the app sees that line, it rewrites the file without the username and password. The other settings stay, because IBC's restart loop
reuses the file. The password never goes to logs or the database. **Remember password** is opt-in and uses Windows Credential Manager
(`keyring`, service name `TradingAssistant`). Use `POST /api/forget-password` to remove it.

## Files
- `config.toml`: non-secret settings (ports, IBC paths, `allow_live`).
- `%LOCALAPPDATA%\TradingAssistant\logs\`: `app.log`, `ibc.log` (IBC/Gateway output, used for login progress), and `backend.*.log`.

## Development
- Backend: `cd backend; uv run uvicorn app.main:app --port 8765 --reload`. Tests: `uv run pytest`.
- Frontend: `cd frontend; npm run dev`. Vite proxies `/api` and `/ws` to the backend.

Charts use TradingView Lightweight Charts™ (https://www.tradingview.com), which requires attribution. Keep the logo on the chart.
