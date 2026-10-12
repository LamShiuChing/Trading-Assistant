"""Signal engine + forward tracking. Long only.

Detectors are plain functions `detect(rows, levels) -> (stop, note) | None`, evaluated on each COMPLETED
1-min bar of watched symbols (top screener names + the chart symbol). Every signal is logged with the
features at that moment, whether or not you take it. After the close its outcome is computed from that
day's 1-min bars: R after 5/15/30/60 min, MFE/MAE, and a simulated trade (stop vs 2R target, else the
close). The Signals tab shows today's list and win rate / average R / expectancy by setup, time of day
and catalyst, so the data shows which setups work instead of backtesting.
"""
import asyncio
import json
import logging
from datetime import date, datetime, time as dtime, timedelta

from ib_async import Stock

from . import db
from .config import CFG
from .hub import publish
from .ib import screener_hist
from .indicators import rsi_series, sma_series
from .market import analyze
from .risk import ET

log = logging.getLogger(__name__)

db.conn.executescript("""
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY,
    day TEXT NOT NULL,
    ts TEXT NOT NULL,            -- ET, the completed bar's time
    symbol TEXT NOT NULL,
    setup TEXT NOT NULL,
    entry REAL NOT NULL,         -- that bar's close
    stop REAL NOT NULL,
    features TEXT NOT NULL,      -- JSON at signal time
    outcome TEXT,                -- JSON after the close; NULL until finalized
    taken INTEGER                -- 1 if you entered this symbol within 10 min (from the trade journal)
);
CREATE INDEX IF NOT EXISTS signals_day ON signals (day);
""")

SECTOR_ETF = {  # IBKR contract-details industry -> SPDR sector ETF
    "Technology": "XLK", "Communications": "XLC", "Consumer, Cyclical": "XLY", "Energy": "XLE",
    # ponytail: IBKR's "Consumer, Non-cyclical" also holds healthcare; XLP is a rough proxy there.
    "Consumer, Non-cyclical": "XLP", "Industrial": "XLI", "Financial": "XLF",
    "Basic Materials": "XLB", "Utilities": "XLU",
}
COOLDOWN_MIN = 30   # same setup on the same symbol at most once per 30 min (ORB: once per day)
TARGET_R = 2.0      # the simulated trade's target


# ---------- pure helpers ----------

def minute(row: dict) -> int:
    """Minutes since midnight ET of a chart row (times are ET wall-clock epoch seconds)."""
    return (row["time"] % 86400) // 60


def today_rth(rows: list[dict]) -> list[dict]:
    """Rows of the last row's day, 09:30-16:00."""
    day = rows[-1]["time"] // 86400
    return [r for r in rows if r["time"] // 86400 == day and 570 <= minute(r) < 960]


# ---------- detectors: (rows, levels) -> (stop, note) | None, judged on rows[-1] (a completed bar) ----------

def _orb(n: int):
    def detect(rows: list[dict], lv: dict):
        rng = lv.get(f"or{n}")
        day = today_rth(rows)
        if not rng or not rng["complete"] or len(day) < 2:
            return None
        r = day[-1]
        if not (570 + n <= minute(r) < 690) or r["vwap"] is None or r["close"] <= r["vwap"]:
            return None  # after the range, before 11:30, above VWAP
        after = [x for x in day if x["time"] >= rng["end"]]
        # first close above the range high today (earlier ones would have fired already)
        if r["close"] > rng["high"] and all(x["close"] <= rng["high"] for x in after[:-1]):
            return round(rng["low"] - 0.01, 2), f"OR{n} {rng['low']:.2f}-{rng['high']:.2f}"
        return None
    return detect


def vwap_reclaim(rows, lv):
    day = today_rth(rows)
    if len(day) < 15:
        return None
    r, prev5 = day[-1], day[-6:-1]
    if not (585 <= minute(r) <= 930) or r["vwap"] is None:
        return None
    below = [x for x in prev5 if x["vwap"] is not None and x["close"] < x["vwap"]]
    if r["close"] > r["vwap"] and day[-2]["close"] < (day[-2]["vwap"] or 0) and len(below) >= 3:
        return round(min(x["low"] for x in day[-10:]) - 0.01, 2), "closed back above VWAP"
    return None


def vwap_hold(rows, lv):
    day = today_rth(rows)
    if len(day) < 31:
        return None
    r, trend = day[-1], day[-16:-1]
    if not (600 <= minute(r) <= 930) or r["vwap"] is None:
        return None
    if all(x["vwap"] is not None and x["close"] >= x["vwap"] for x in trend) \
            and r["low"] <= r["vwap"] * 1.002 and r["close"] > r["vwap"] and r["close"] > r["open"]:
        return round(min(x["low"] for x in day[-3:]) - 0.01, 2), "pullback held VWAP"
    return None


def rsi_ma_cross(rows, lv):
    day = today_rth(rows)
    if len(day) < 5:
        return None
    r = day[-1]
    if not (585 <= minute(r) <= 930) or r["vwap"] is None or r["close"] <= r["vwap"]:
        return None
    rs = rsi_series([x["close"] for x in rows])
    ma = sma_series(rs)
    if None in (rs[-1], rs[-2], ma[-1], ma[-2]):
        return None
    if rs[-1] > ma[-1] and rs[-2] <= ma[-2]:
        return round(min(x["low"] for x in day[-5:]) - 0.01, 2), f"RSI {rs[-1]:.0f} crossed its MA"
    return None


DETECTORS = {"ORB5": _orb(5), "ORB15": _orb(15), "VWAP reclaim": vwap_reclaim,
             "VWAP hold": vwap_hold, "RSI x MA": rsi_ma_cross}


MIN_RISK_PCT = 0.2  # a stop closer than 0.2% of price is noise: R-stats on such stops mean nothing


def evaluate(rows: list[dict], levels: dict) -> list[tuple[str, float, str]]:
    """All setups that fire on the last completed row. Long only: the stop must be below the entry.
    Stops tighter than MIN_RISK_PCT are widened to it (min $0.02), so outcomes in R stay comparable."""
    out = []
    entry = rows[-1]["close"]
    floor = round(entry - max(entry * MIN_RISK_PCT / 100, 0.02), 2)
    for name, detect in DETECTORS.items():
        hit = detect(rows, levels)
        if hit and hit[0] < entry:
            out.append((name, min(hit[0], floor), hit[1]))
    return out


def outcome(after: list, entry: float, stop: float) -> dict:
    """Outcome from the 1-min bars AFTER the signal bar (same day, regular session). The simulated trade:
    stop hit first -> -1R (a bar touching both counts as the stop: conservative), +2R target hit -> +2R,
    otherwise the close. Also R at +5/15/30/60 min, and MFE/MAE in R."""
    risk = entry - stop
    res: dict = {}
    mfe = mae = 0.0
    result = None
    for k, b in enumerate(after, start=1):
        mfe = max(mfe, (b.high - entry) / risk)
        mae = min(mae, (b.low - entry) / risk)
        if result is None:
            if b.low <= stop:
                result = -1.0
            elif b.high >= entry + TARGET_R * risk:
                result = TARGET_R
        if k in (5, 15, 30, 60):
            res[f"r_{k}m"] = round((b.close - entry) / risk, 2)
    eod = round((after[-1].close - entry) / risk, 2) if after else 0.0
    res.update(mfe_r=round(mfe, 2), mae_r=round(mae, 2), eod_r=eod,
               result_r=result if result is not None else eod)
    return res


def bucket(ts: str) -> str:
    t = datetime.fromisoformat(ts).time()
    if t < dtime(10, 0):
        return "09:30-10:00"
    if t < dtime(11, 0):
        return "10:00-11:00"
    if t < dtime(14, 0):
        return "11:00-14:00"
    return "14:00-16:00"


# ---------- live engine ----------

class SignalEngine:
    def __init__(self, broker, screener, chart, news, risk, max_symbols: int = 5):
        self.broker, self.screener, self.chart, self.news, self.risk = broker, screener, chart, news, risk
        self.max_symbols = max_symbols
        self.feeds: dict[str, object] = {}   # symbol -> BarDataList (keepUpToDate)
        self.last_close: dict[str, float] = {}
        chart.listeners.append(self.on_bars)  # the chart symbol is evaluated from the chart's own feed
        broker.on_ready.append(self._on_ready)

    async def _on_ready(self) -> None:
        self.feeds.clear()  # subscriptions died with the connection; run() resubscribes

    def watched(self) -> list[str]:
        rows = self.screener.rows or []
        best = [r["symbol"] for r in rows if not r["fails"]] + [r["symbol"] for r in rows if r["fails"]]
        return [s for s in best if s != self.chart.symbol][: self.max_symbols]

    async def run(self) -> None:
        while True:
            try:
                if self.broker.state == "ready":
                    await self._sync_feeds()
                    await self.finalize()
                    self.publish()
            except Exception:
                log.exception("signal engine cycle failed")
            await asyncio.sleep(60 if self.broker.state == "ready" else 2)  # start as soon as connected

    async def _sync_feeds(self) -> None:
        """Live 1-min feeds for the watched symbols, only 09:25-16:00 (signals fire 09:30-16:00)."""
        ib = self.broker.ib
        now = datetime.now(ET)
        s = self.risk.session(now)
        live = bool(s) and s["open"] - timedelta(minutes=5) <= now < s["close"]
        want = set(self.watched()) if live else set()
        for sym in list(self.feeds):
            if sym not in want:
                ib.cancelHistoricalData(self.feeds.pop(sym))
        for sym in want - set(self.feeds):
            c = Stock(sym, "SMART", "USD")
            await ib.qualifyContractsAsync(c)
            await screener_hist.acquire()
            bars = await ib.reqHistoricalDataAsync(c, "", "2 D", "1 min", "TRADES", useRTH=False, keepUpToDate=True)
            if bars:
                self.feeds[sym] = bars
                bars.updateEvent += (lambda b, new, s=sym: self.on_bars(s, b, new))

    def on_bars(self, symbol: str, bars, has_new_bar: bool) -> None:
        if not bars:
            return
        self.last_close[symbol] = bars[-1].close
        if not has_new_bar or len(bars) < 2:
            return
        done = bars[:-1]  # the last bar is still forming
        t = done[-1].date
        if not (dtime(9, 30) <= t.time() < dtime(16, 0)):
            return
        rows, levels = analyze(done)
        for setup, stop, note in evaluate(rows, levels):
            asyncio.get_running_loop().create_task(self._record(symbol, setup, rows[-1], levels, stop, note, t))

    async def _record(self, symbol, setup, row, levels, stop, note, t: datetime) -> None:
        day = t.date().isoformat()
        prev = db.conn.execute("SELECT ts FROM signals WHERE symbol=? AND setup=? AND day=? ORDER BY ts DESC LIMIT 1",
                               (symbol, setup, day)).fetchone()
        if prev and (setup.startswith("ORB") or t - datetime.fromisoformat(prev["ts"]) < timedelta(minutes=COOLDOWN_MIN)):
            return
        entry = row["close"]
        feats = await self._features(symbol, row, levels, entry, stop, t)
        feats["note"] = note
        db.conn.execute("INSERT INTO signals (day, ts, symbol, setup, entry, stop, features) VALUES (?,?,?,?,?,?,?)",
                        (day, t.isoformat(), symbol, setup, entry, stop, json.dumps(feats)))
        log.info("signal %s %s entry %.2f stop %.2f %s", symbol, setup, entry, stop, note)
        publish("alert", {"level": "info", "message": f"Signal: {setup} {symbol} @ {entry:.2f}, stop {stop:.2f} ({note})"})
        self.publish()

    async def _features(self, symbol, row, levels, entry, stop, t) -> dict:
        sc = next((r for r in (self.screener.rows or []) if r["symbol"] == symbol), {})
        pdc = levels.get("pdc")
        chg = (entry / pdc - 1) * 100 if pdc else None
        f = {
            "bucket": bucket(t.isoformat()), "risk_pct": round((entry - stop) / entry * 100, 3),
            "rvol": sc.get("rvol"), "gap_atr": sc.get("gap_atr"), "change_pct": round(chg, 2) if chg is not None else None,
            "catalyst": self.news.catalyst_today(symbol) if self.news else None,
            "industry": sc.get("industry") or "", "vwap_dist_pct": round((entry / row["vwap"] - 1) * 100, 3) if row["vwap"] else None,
        }
        # Relative strength: today's % change vs QQQ and the sector ETF (snapshot quotes at signal time).
        etfs = ["QQQ"] + ([SECTOR_ETF[f["industry"]]] if f["industry"] in SECTOR_ETF else [])
        try:
            cs = [Stock(e, "SMART", "USD") for e in etfs]
            await self.broker.ib.qualifyContractsAsync(*cs)
            for e, tk in zip(etfs, await self.broker.ib.reqTickersAsync(*cs)):
                px, close = tk.marketPrice(), tk.close
                if chg is not None and px and close and px == px and close == close:
                    f["rs_qqq" if e == "QQQ" else "rs_sector"] = round(chg - (px / close - 1) * 100, 2)
        except Exception as e:
            log.warning("RS snapshot failed: %s", e)
        return f

    # ---------- outcomes ----------

    async def finalize(self) -> None:
        """Compute outcomes for finished days (and today after 16:01) from that day's 1-min bars."""
        now = datetime.now(ET)
        cutoff = now.date() if now.time() >= dtime(16, 1) else now.date() - timedelta(days=1)
        pending = db.conn.execute("SELECT DISTINCT symbol, day FROM signals WHERE outcome IS NULL AND day <= ?",
                                  (cutoff.isoformat(),)).fetchall()
        for p in pending:
            c = Stock(p["symbol"], "SMART", "USD")
            await self.broker.ib.qualifyContractsAsync(c)
            end = datetime.combine(date.fromisoformat(p["day"]), dtime(16, 5), ET)
            await screener_hist.acquire()
            bars = await self.broker.ib.reqHistoricalDataAsync(c, end, "1 D", "1 min", "TRADES", useRTH=True)
            if not bars:
                continue
            for s in db.conn.execute("SELECT * FROM signals WHERE symbol=? AND day=? AND outcome IS NULL",
                                     (p["symbol"], p["day"])).fetchall():
                ts = datetime.fromisoformat(s["ts"])
                after = [b for b in bars if b.date > ts]
                taken = db.conn.execute(
                    "SELECT 1 FROM trades WHERE symbol=? AND created BETWEEN ? AND ? AND status='filled'",
                    (s["symbol"], ts.replace(tzinfo=None).isoformat(),
                     (ts + timedelta(minutes=10)).replace(tzinfo=None).isoformat())).fetchone()
                db.conn.execute("UPDATE signals SET outcome=?, taken=? WHERE id=?",
                                (json.dumps(outcome(after, s["entry"], s["stop"])), 1 if taken else 0, s["id"]))

    # ---------- publishing ----------

    def publish(self) -> None:
        today = datetime.now(ET).date().isoformat()
        rows = db.conn.execute("SELECT * FROM signals WHERE day=? ORDER BY ts DESC", (today,)).fetchall()
        items = []
        for r in rows:
            last = self.last_close.get(r["symbol"])
            risk = r["entry"] - r["stop"]
            items.append({"id": r["id"], "time": r["ts"][11:16], "symbol": r["symbol"], "setup": r["setup"],
                          "entry": r["entry"], "stop": r["stop"], "features": json.loads(r["features"]),
                          "now_r": round((last - r["entry"]) / risk, 2) if last and risk > 0 else None,
                          "outcome": json.loads(r["outcome"]) if r["outcome"] else None})
        publish("signals", {"today": items, "stats": stats(), "watching": self.watched() + [self.chart.symbol]})


def stats() -> dict:
    """Win rate, average R (= expectancy per signal) and MFE/MAE, grouped by setup, time bucket and catalyst."""
    rows = db.conn.execute("SELECT setup, ts, features, outcome, taken FROM signals WHERE outcome IS NOT NULL").fetchall()
    groups: dict[str, dict[str, list]] = {"setup": {}, "time": {}, "catalyst": {}}
    for r in rows:
        o, f = json.loads(r["outcome"]), json.loads(r["features"])
        for g, key in (("setup", r["setup"]), ("time", bucket(r["ts"])), ("catalyst", f.get("catalyst") or "none")):
            groups[g].setdefault(key, []).append(o)

    def summarize(os: list[dict]) -> dict:
        res = [o["result_r"] for o in os]
        return {"n": len(os), "win_pct": round(100 * sum(1 for x in res if x > 0) / len(res), 1),
                "avg_r": round(sum(res) / len(res), 2),
                "avg_mfe": round(sum(o["mfe_r"] for o in os) / len(os), 2),
                "avg_mae": round(sum(o["mae_r"] for o in os) / len(os), 2)}

    return {"total": len(rows), **{g: {k: summarize(v) for k, v in sorted(d.items())} for g, d in groups.items()}}
