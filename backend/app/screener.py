"""Screener: IBKR scans -> enrich the top candidates -> filters + transparent score.

`score(features) -> (total, parts)` is the one function an ML model replaces later; everything it
sees is in `features`, and every row is published with its parts so the ranking is explainable.
"""
import asyncio
import json
import logging
import math
import time
from datetime import date, datetime
from statistics import mean

from ib_async import ScannerSubscription, TagValue

from . import rules as R
from .config import CFG, DATA
from .hub import publish
from .ib import screener_hist
from .indicators import intraday_indicators
from .risk import ET

log = logging.getLogger(__name__)
S = CFG["screener"]
LAST = DATA / "screener_last.json"  # last scan, shown after a restart or the close until the next scan


# ---------- pure feature / score functions (unit tested) ----------

def daily_stats(bars, today: date) -> dict | None:
    """ATR(14), prior close and 20-day average volume / $ volume from daily bars (today's partial bar ignored)."""
    hist = [b for b in bars if b.date < today]
    if len(hist) < 15:
        return None
    trs = [max(b.high - b.low, abs(b.high - p.close), abs(b.low - p.close)) for p, b in zip(hist, hist[1:])]
    last20 = hist[-20:]
    return {"atr": mean(trs[-14:]), "prev_close": hist[-1].close,
            "avg_vol": mean(b.volume for b in last20), "avg_usd_vol": mean(b.volume * b.close for b in last20)}


def rvol(bars, now: datetime) -> float | None:
    """Relative volume: today's volume so far vs the average of prior days' volume up to the same time of day
    (pre-market included, so before 09:30 this is pre-market RVOL)."""
    upto: dict[date, float] = {}
    for b in bars:
        if b.date.time() <= now.time():
            upto[b.date.date()] = upto.get(b.date.date(), 0) + b.volume
    today = upto.pop(now.date(), 0)
    prior = [v for v in upto.values() if v > 0]
    return round(today / mean(prior), 2) if prior else None


def check_filters(f: dict) -> list[str]:
    """The brief's filters. Each failure is listed so you can see why a name ranks low."""
    fails = []
    if f["price"] < S["min_price"]:
        fails.append(f"price < {S['min_price']}")
    if f["avg_usd_vol"] < S["min_avg_usd_volume"]:
        fails.append(f"$vol < {S['min_avg_usd_volume'] / 1e6:.0f}M")
    if f["gap_atr"] < S["min_gap_atr"]:
        fails.append(f"gap < {S['min_gap_atr']} ATR")
    if f["rvol"] is None or f["rvol"] < S["min_rvol"]:
        fails.append(f"RVOL < {S['min_rvol']}")
    if f["spread_pct"] is None or f["spread_pct"] > S["max_spread_pct"]:
        fails.append(f"spread > {S['max_spread_pct']}%")
    return fails


def score(f: dict) -> tuple[float, dict]:
    """Transparent 0-100-ish score. Parts: gap (up to 40), RVOL (up to 40), liquidity (up to 10),
    catalyst (10, from news in phase 3b), spread (penalty down to -20). Long only: gap downs score 0."""
    clamp = lambda x, lo, hi: max(lo, min(hi, x))
    parts = {
        "gap": 10 * clamp(f["gap_atr"], 0, 4),
        "rvol": 10 * clamp(math.log2(f["rvol"]), 0, 4) if f["rvol"] else 0.0,
        "liquidity": 5 * clamp(math.log10(max(f["avg_usd_vol"], 1) / 2e7), 0, 2),
        "catalyst": 10.0 if f.get("catalyst") else 0.0,
        "spread": -20 * clamp((f["spread_pct"] or 1) / 0.5, 0, 1),
    }
    parts = {k: round(v, 1) for k, v in parts.items()}
    return round(sum(parts.values()), 1), parts


# ---------- live screener ----------

class Screener:
    def __init__(self, broker, risk):
        self.broker, self.risk = broker, risk
        self.daily: dict[tuple[int, date], dict | None] = {}
        self.intra: dict[int, tuple[float, float | None, list]] = {}  # conId -> (fetched, rvol, 5-min bars)
        self.busy = False
        self.rows: list[dict] = []
        self.news = None  # set by main: today's tagged catalyst feeds the score
        if LAST.exists():
            last = json.loads(LAST.read_text())
            self.rows, self.updated = last["rows"], last["updated"]
            publish("screener", last)

    def _filters(self) -> list[TagValue]:
        # Tag names verified against this Gateway's reqScannerParameters XML; avgUsdVolumeAbove is in dollars.
        return [TagValue("priceAbove", str(S["min_price"])),
                TagValue("marketCapAbove1e6", str(S["min_market_cap"] // 1_000_000)),
                TagValue("avgUsdVolumeAbove", str(S["min_avg_usd_volume"]))]

    async def run(self) -> None:
        while True:
            try:
                s = self.risk.session()
                now = datetime.now(ET)
                if self.broker.state == "ready" and s and now.hour >= 4 and now < s["close"]:
                    await self.scan()
            except Exception:
                log.exception("scan failed")
            # Until connected and the session clock is loaded, check again shortly instead of a full interval.
            await asyncio.sleep(S["interval_sec"] if self.broker.state == "ready" and self.risk.hours else 2)

    async def scan(self) -> None:
        if self.busy:
            return
        self.busy = True
        try:
            ib = self.broker.ib
            seen: dict[int, object] = {}
            for code in S["scan_codes"]:
                sub = ScannerSubscription(instrument="STK", locationCode="STK.US.MAJOR", scanCode=code,
                                          numberOfRows=S["rows_per_scan"])
                for d in await ib.reqScannerDataAsync(sub, [], self._filters()):
                    c = d.contractDetails.contract
                    seen.setdefault(c.conId, c)
            top = list(seen.values())[: S["max_candidates"]]
            tickers = await ib.reqTickersAsync(*top) if top else []
            rows = []
            for c, t in zip(top, tickers):
                f = await self._features(c, t)
                if f is None:
                    continue
                rows.append(f)
            self.publish(rows, datetime.now(ET).strftime("%m-%d %H:%M:%S"))
        finally:
            self.busy = False

    def rank(self, rows: list[dict]) -> list[dict]:
        """Built-in score + filters, then your rules (required rules add failures, score rules add points)."""
        rules = R.load()
        out = []
        for f in rows:
            f = {k: v for k, v in f.items() if k not in ("score", "parts", "fails", "rule_hits")}
            total, parts = score(f)
            rule_fails, pts, hits = R.apply(f, rules)
            if rules:
                parts["rules"] = pts
            out.append({**f, "score": round(total + pts, 1), "parts": parts,
                        "fails": check_filters(f) + rule_fails, "rule_hits": hits})
        out.sort(key=lambda r: (not r["fails"], r["score"]), reverse=True)
        return out

    def publish(self, rows: list[dict], updated: str) -> None:
        self.rows = self.rank(rows)
        self.updated = updated
        payload = {"updated": updated, "rows": self.rows}
        LAST.write_text(json.dumps(payload))
        publish("screener", payload)

    def rescore(self) -> None:
        """Re-rank the current list after a rule change, without rescanning."""
        self.publish(self.rows, getattr(self, "updated", ""))

    async def _features(self, c, t) -> dict | None:
        ib = self.broker.ib
        now = datetime.now(ET)
        key = (c.conId, now.date())
        if key not in self.daily:
            await screener_hist.acquire()
            bars = await ib.reqHistoricalDataAsync(c, "", "30 D", "1 day", "TRADES", useRTH=True)
            self.daily[key] = daily_stats(bars, now.date())
        d = self.daily[key]
        if not d:
            return None
        got = self.intra.get(c.conId)
        if not got or time.monotonic() - got[0] > 300:  # 5-min bars refreshed at most every 5 min per symbol
            await screener_hist.acquire()
            bars = await ib.reqHistoricalDataAsync(c, "", "5 D", "5 mins", "TRADES", useRTH=False)
            got = (time.monotonic(), rvol(bars, now), list(bars))
            self.intra[c.conId] = got
        price = t.marketPrice()
        if not price or math.isnan(price):
            return None
        bid, ask = t.bid, t.ask
        spread = round((ask - bid) / ((ask + bid) / 2) * 100, 3) if bid and ask and bid > 0 and ask >= bid else None
        return {
            "symbol": c.symbol, "conId": c.conId, "price": round(price, 2),
            "change_pct": round((price / d["prev_close"] - 1) * 100, 2),
            "gap_atr": round((price - d["prev_close"]) / d["atr"], 2) if d["atr"] else 0.0,
            "rvol": got[1], "avg_usd_vol": round(d["avg_usd_vol"]), "spread_pct": spread,
            "avg_usd_vol_m": round(d["avg_usd_vol"] / 1e6, 1),
            "industry": await self.risk.industry_of(c),
            "catalyst": (cat := self.news.catalyst_today(c.symbol) if self.news else None),
            "has_catalyst": int(bool(cat)),
            **intraday_indicators(got[2], now, price, d["atr"]),
        }
