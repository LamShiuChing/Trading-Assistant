"""Chart data: 1-min bars from IBKR plus RTH VWAP, prior-day and pre-market levels, opening ranges.

Bar times are sent as "ET wall-clock" epoch seconds (UTC epoch + ET offset), because
Lightweight Charts has no time-zone support and shows timestamps as UTC.
"""
import logging
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
    Returns chart rows (VWAP anchored at each day's 09:30, None outside RTH) and today's levels."""
    rows, pv, vol, day = [], 0.0, 0.0, None
    for b in bars:
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
                     "close": b.close, "volume": b.volume, "vwap": vwap})
    return rows, levels(bars)


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
        bars = await ib.reqHistoricalDataAsync(contract, "", "3 D", "1 min", "TRADES",
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
        rows, lv = analyze(bars)
        self.levels = lv
        # On a new bar, the previous one was also finalised, so send both.
        publish("bar", {"symbol": self.symbol, "rows": rows[-2:], "levels": lv})
        # Keep the full snapshot current for UIs that connect later (not broadcast).
        hub.latest["chart"] = {"symbol": self.symbol, "bars": rows, "levels": lv}
