"""Chart data: 1-min bars from IBKR plus RTH VWAP, prior-day and pre-market levels, opening ranges.

Bar times are sent as "ET wall-clock" epoch seconds (UTC epoch + ET offset), because
Lightweight Charts has no time-zone support and shows timestamps as UTC.
"""
import logging
from bisect import bisect_right
from datetime import datetime, time, timedelta

from ib_async import Stock

from . import hub
from .hub import publish

log = logging.getLogger(__name__)
PRE_OPEN, RTH_OPEN, RTH_CLOSE = time(4, 0), time(9, 30), time(16, 0)
# ponytail: fixed 09:30-16:00 for history. liquidHours only lists today onward, so the day
# after a half-day gets a PDC/PDH from after-hours bars. Fix: keep a holiday calendar if it matters.


def chart_time(dt: datetime) -> int:
    return int(dt.timestamp() + dt.utcoffset().total_seconds())


def _rth(dt: datetime) -> bool:
    return RTH_OPEN <= dt.time() < RTH_CLOSE


def analyze(bars) -> tuple[list[dict], dict]:
    """bars: objects with tz-aware ET .date and open/high/low/close/volume/average.
    Returns chart rows (VWAP anchored at each day's 09:30, None outside RTH; RVOL for today) and today's levels."""
    rows, pv, vol, day = [], 0.0, 0.0, None
    rv = rvol_series(bars)
    for i, b in enumerate(bars):
        if b.date.date() != day:
            day, pv, vol = b.date.date(), 0.0, 0.0
        vwap = None
        if _rth(b.date):
            # IBKR's bar `average` is the bar's own VWAP; fall back to typical price if missing.
            price = b.average if b.average > 0 else (b.high + b.low + b.close) / 3
            pv += price * b.volume
            vol += b.volume
            vwap = round(pv / vol, 4) if vol else None
        rows.append({"time": chart_time(b.date), "open": b.open, "high": b.high, "low": b.low,
                     "close": b.close, "volume": b.volume, "vwap": vwap, "rvol": rv[i]})
    return rows, levels(bars)


def rvol_series(bars) -> list[float | None]:
    """Per bar: today's volume so far vs the average of prior days' volume up to the same minute
    (pre-market included; same definition as the screener's RVOL). None for earlier days."""
    if not bars:
        return []
    today = bars[-1].date.date()
    prior: dict = {}  # day -> ([minute of day], [cumulative volume])
    for b in bars:
        d = b.date.date()
        if d != today:
            mins, cum = prior.setdefault(d, ([], []))
            mins.append(b.date.hour * 60 + b.date.minute)
            cum.append((cum[-1] if cum else 0) + b.volume)
    out: list[float | None] = []
    so_far = 0.0
    for b in bars:
        if b.date.date() != today:
            out.append(None)
            continue
        so_far += b.volume
        m = b.date.hour * 60 + b.date.minute
        base = [cum[i] for mins, cum in prior.values() if (i := bisect_right(mins, m) - 1) >= 0 and cum[i] > 0]
        out.append(round(so_far / (sum(base) / len(base)), 2) if base else None)
    return out


def sr_levels(bars, known: list[float], k: int = 5, per_side: int = 3) -> list[dict]:
    """Support/resistance from swing pivots on 5-min bars (regular hours of every day, plus today's
    pre-market): a bar whose high (low) is the extreme of the k bars on each side (k=5: a 25-min swing).
    Pivots within 0.75x the average 5-min range are one level, at their mean price; a level needs 2+
    touches. Walking out from the last price, a level is kept only if it is 1.5x that range away from
    the key levels (`known`: PDH, PMH, ...) and from levels already kept, so lines don't stack up.
    Returns up to per_side each way: {"price", "touches", "side": "R" | "S"}."""
    if not bars:
        return []
    today = bars[-1].date.date()
    agg: dict = {}  # (day, 5-min slot) -> [high, low]
    for b in bars:
        if _rth(b.date) or (b.date.date() == today and PRE_OPEN <= b.date.time() < RTH_OPEN):
            key = (b.date.date(), (b.date.hour * 60 + b.date.minute) // 5)
            a = agg.setdefault(key, [b.high, b.low])
            a[0], a[1] = max(a[0], b.high), min(a[1], b.low)
    hl = [agg[key] for key in sorted(agg)]
    if len(hl) < 2 * k + 1:
        return []
    rng = sum(h - l for h, l in hl) / len(hl)
    piv = []
    for i in range(k, len(hl) - k):
        left, right = hl[i - k:i], hl[i + 1:i + k + 1]
        if hl[i][0] > max(h for h, _ in left) and hl[i][0] >= max(h for h, _ in right):
            piv.append(hl[i][0])
        if hl[i][1] < min(l for _, l in left) and hl[i][1] <= min(l for _, l in right):
            piv.append(hl[i][1])
    clusters: list[list[float]] = []
    for p in sorted(piv):
        if clusters and p - clusters[-1][0] <= 0.75 * rng:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    last = bars[-1].close
    found = [(round(sum(c) / len(c), 2), len(c)) for c in clusters if len(c) >= 2]
    out, taken = [], list(known)
    for side, cands in (("R", sorted(f for f in found if f[0] > last)),
                        ("S", sorted((f for f in found if f[0] <= last), reverse=True))):
        n = 0
        for price, touches in cands:
            if n < per_side and all(abs(price - x) > 1.5 * rng for x in taken):
                out.append({"price": price, "touches": touches, "side": side})
                taken.append(price)
                n += 1
    return out


def levels(bars) -> dict:
    if not bars:
        return {}
    today = bars[-1].date.date()
    prior_days = sorted({b.date.date() for b in bars if b.date.date() < today and _rth(b.date)})
    out: dict = {}
    if prior_days:
        prev = [b for b in bars if b.date.date() == prior_days[-1] and _rth(b.date)]
        out.update(pdh=max(b.high for b in prev), pdl=min(b.low for b in prev), pdc=prev[-1].close)
    pre = [b for b in bars if b.date.date() == today and PRE_OPEN <= b.date.time() < RTH_OPEN]
    if pre:
        out.update(pmh=max(b.high for b in pre), pml=min(b.low for b in pre))
    for minutes in (5, 15):
        start = datetime.combine(today, RTH_OPEN, tzinfo=bars[-1].date.tzinfo)
        end = start + timedelta(minutes=minutes)
        rng = [b for b in bars if start <= b.date < end]
        if rng:
            out[f"or{minutes}"] = {"high": max(b.high for b in rng), "low": min(b.low for b in rng),
                                   "start": chart_time(start), "end": chart_time(end),
                                   "complete": bars[-1].date >= end}
    out["sr"] = sr_levels(bars, [out[x] for x in ("pdh", "pdl", "pdc", "pmh", "pml") if x in out])
    return out


def atr(bars, minutes: int = 5, n: int = 14) -> float | None:
    """ATR(n) of today's regular-session bars, aggregated to `minutes`. None until there are n bars."""
    if not bars:
        return None
    today = bars[-1].date.date()
    agg: dict[int, list] = {}
    for b in bars:
        if b.date.date() == today and _rth(b.date):
            k = (b.date.hour * 60 + b.date.minute) // minutes
            if k in agg:
                a = agg[k]
                a[1], a[2], a[3] = max(a[1], b.high), min(a[2], b.low), b.close
            else:
                agg[k] = [b.open, b.high, b.low, b.close]
    rows = [agg[k] for k in sorted(agg)]
    if len(rows) < n + 1:
        return None
    trs = [max(h - l, abs(h - rows[i - 1][3]), abs(l - rows[i - 1][3])) for i, (_, h, l, _) in enumerate(rows) if i]
    return round(sum(trs[-n:]) / n, 4)


class ChartFeed:
    """One live 1-min subscription for the symbol on the chart. Resubscribes after reconnects."""

    def __init__(self, broker, symbol: str):
        self.broker, self.symbol, self.bars = broker, symbol, None
        self.levels: dict = {}
        self.con_id: int | None = None
        self.listeners: list = []  # fn(symbol, bars, has_new_bar), e.g. the signal engine
        broker.on_ready.append(self.resubscribe)

    async def resubscribe(self) -> None:
        self.bars = None  # the old subscription died with the connection
        await self.set_symbol(self.symbol)

    async def set_symbol(self, symbol: str) -> None:
        ib = self.broker.ib
        if self.bars is not None:
            ib.cancelHistoricalData(self.bars)
            self.bars = None
        self.symbol = symbol
        publish("chart", {"symbol": symbol, "loading": True})
        contract = Stock(symbol, "SMART", "USD")
        await ib.qualifyContractsAsync(contract)
        if not contract.conId:
            publish("chart", {"symbol": symbol, "error": f"Unknown symbol {symbol}"})
            return
        self.con_id = contract.conId
        # 5 days: 4 prior days for the RVOL baseline and support/resistance pivots.
        bars = await ib.reqHistoricalDataAsync(contract, "", "5 D", "1 min", "TRADES",
                                               useRTH=False, keepUpToDate=True)
        if symbol != self.symbol:  # user switched again while this request was in flight
            ib.cancelHistoricalData(bars)
            return
        if not bars:
            publish("chart", {"symbol": symbol, "error": f"No data for {symbol}. Details are in app.log."})
            return
        self.bars = bars
        bars.updateEvent += self._on_update
        rows, lv = analyze(bars)
        self.levels = lv
        publish("chart", {"symbol": symbol, "bars": rows, "levels": lv})

    def _on_update(self, bars, has_new_bar: bool) -> None:
        if bars is not self.bars:
            return
        for fn in self.listeners:
            fn(self.symbol, bars, has_new_bar)
        rows, lv = analyze(bars)
        self.levels = lv
        # On a new bar, the previous one was also finalised, so send both.
        publish("bar", {"symbol": self.symbol, "rows": rows[-2:], "levels": lv})
        # Keep the full snapshot current for UIs that connect later (not broadcast).
        hub.latest["chart"] = {"symbol": self.symbol, "bars": rows, "levels": lv}
