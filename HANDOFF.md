# Handoff — Trading Assistant (as of 2026-10-12)

Local, single-user intraday trading assistant for IBKR. Setup and running: see README.md.
Repo: https://github.com/LamShiuChing/Trading-Assistant (branch `main`, pushed, clean).

## State
Phases 1–4 are done and paper-verified, plus extras. Phase 5 (ML ranking) waits on weeks of logged signals.

| Area | Where | Status |
|---|---|---|
| Launcher (.lnk → PowerShell → Edge `--app`), restart, quit | `launcher/`, `main.py` | verified |
| In-app IBKR login via IBC (temp ini scrubbed, keyring opt-in) | `gateway.py`, `Login.tsx` | verified |
| Connection state machine, reconcile, pacing | `ib.py` | verified |
| Chart: candles, VWAP, OR5/OR15 boxes, PDH/PDL/PDC/PMH/PML, volume, RSI 14 + yellow MA 14, signal arrows, POS/STOP/TP lines + zones, draggable staged lines | `market.py`, `Chart.tsx` | verified |
| **New:** RVOL line (time-matched vs 4 prior days, log scale) + legend; auto S/R lines | `market.rvol_series`, `market.sr_levels` | unit-tested; after-hours only, **not yet seen during RTH** |
| Orders: long only (server-enforced), MKT entry, STP stop + LMT TP, OCA type 2, one merged bracket per symbol, manual size, Sell ½ / Sell all | `orders.py`, `Ticket.tsx`, `Portfolio.tsx` | verified, except Sell ½ click |
| Risk: daily loss lock (persisted), max positions/open risk/trades, entry cutoff 15:30, force-flat 15:50 + IBKR-side MKT backup at flatten+2min, kill switch | `risk.py` | verified, except live force-flat with an open position |
| Trailing stop after +1R (IBKR adjusted order) | `orders.py` | **unverified live** |
| Screener: IBKR scans, filters, transparent score, custom rules (Required / ±points) | `screener.py`, `rules.py`, `Rules.tsx` | verified |
| News: IBKR headlines, Claude Haiku 5.5 tagging (headlines treated as untrusted data) | `news.py` | verified |
| Signals: ORB5/ORB15/VWAP reclaim/VWAP hold/RSI×MA, forward outcomes, stats | `signals.py`, `Signals.tsx` | replay-verified; first live session is next RTH |

Tests: `cd backend && uv run python -m pytest -q` → 40 passed. (Plain `uv run pytest` fails to import `app`.)
Frontend: `cd frontend && npx tsc -b --noEmit && npm run build` (the launcher also rebuilds when sources are newer).

## How S/R is computed (`market.sr_levels`)
- Uses 5-min bars: regular hours of the last 5 days, plus today's pre-market.
- Pivots: k = 5 bars each side.
- Clustering: pivots within 0.75× the average 5-min range form one level, at their mean price.
- A level needs 2+ touches.
- Selection walks outward from the last price, keeping levels at least 1.5× the range away from key levels and from each other. Max 3 per side.
- Tuned on SPY/NVDA/TSLA/AMD. Earlier settings (k=3, no spacing) stacked 6 lines within $2 on SPY.
- The chart history request went from "3 D" to "5 D" of 1-min bars. Tested: about 4,800 bars in 0.6 s.

## Open items / ideas (not started unless noted)
1. **Verify live:**
   - RVOL/S-R during regular hours.
   - Trailing conversion at +1R.
   - Force-flat at 15:50 with an open position.
   - The Sell ½ button.
   - The first live signal day.
2. **Offered to the user, awaiting a yes:**
   - Per-trade journal stats: expectancy in R by setup, time of day and catalyst.
   - Opening-range RVOL field (`or_rvol`, Zarattini et al. definition).
   - "ORB5-paper" signal variant: 10% ATR stop, exit at the close.
   - On/off toggle for S/R lines.
   - Use S/R levels as candidates for the "next level" take-profit (`risk.target_price`).
   - A 3m timeframe button (one line in `TIMEFRAMES`).
3. **Gateway 10.45 is desupported on 2026-12-15.** Upgrade to 10.50+ (offline installer) and set `major_version` in config.toml.
4. **Pattern day trader rule:** the SEC approved removing it in April 2026. Brokers have until about Oct 2027, so check IBKR's status before small-account live trading.
5. **Pre-existing, not touched:** the RSI pane axis shows 0–120 labels (margin around the 0–100 autoscale).
6. The test screener rules "RVOL > 2 → +20" and "RSI 5m < 70 → Required" may still be saved. The user was told to delete them if unwanted.

## User preferences and constraints
- Long only. Market orders for the user's own buys and sells; the stop stays STP and the TP stays LMT.
- One stop + one TP per stock. Manual share size with presets.
- PAPER is the default. LIVE needs `allow_live=true` plus the toggle.
- Safety actions:
  - The **user** clicks the kill switch; never trigger it for them.
  - Never place or test orders while the user has a live position open.
  - Never add test rows to their signal stats.
- Secrets:
  - The password is never written to disk, logs or the DB.
  - The Anthropic key lives only in `.env`, read via `dotenv_values`. Never ask for it in chat.
- Skip SEC EDGAR (no email to the SEC).
- Commit and push after changes. Commit trailer: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- The user is a beginner/intermediate day trader. Explain plainly and be honest about evidence vs opinion (base rates: most retail day traders lose).

## Runtime notes
- Data folder: `%LOCALAPPDATA%\TradingAssistant\`. Contains journal.db, logs/, run/ibc.ini (temporary), settings.json, screener_last.json, screener_rules.json, and the browser profile.
- Backend: `127.0.0.1:8765`. Restart with `POST /api/restart`; it spawns a fresh backend that waits for the old PID.
- For ad-hoc IB scripts, use a different clientId (e.g. 93, readonly). The app uses 17 on paper port 4002.
