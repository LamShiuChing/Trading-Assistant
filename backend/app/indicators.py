"""Indicator math shared by the screener and the signal engine (same formulas as the chart's TypeScript)."""
from datetime import datetime, time, timedelta


def rsi_series(closes: list[float], n: int = 14) -> list[float | None]:
    """Wilder's RSI; None until there are n changes."""
    out: list[float | None] = [None] * len(closes)
    gain = loss = 0.0
    for i in range(1, len(closes)):
        ch = closes[i] - closes[i - 1]
        g, l = max(ch, 0.0), max(-ch, 0.0)
        if i <= n:
            gain += g
            loss += l
            if i < n:
                continue
            gain /= n
            loss /= n
        else:
            gain = (gain * (n - 1) + g) / n
            loss = (loss * (n - 1) + l) / n
        out[i] = 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)
    return out


def sma_series(values: list[float | None], n: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    window: list[float] = []
    for i, v in enumerate(values):
        if v is None:
            continue
        window.append(v)
        if len(window) > n:
            window.pop(0)
        if len(window) == n:
            out[i] = sum(window) / n
    return out


def ema_series(values: list[float], n: int) -> list[float | None]:
    """EMA seeded with the SMA of the first n values (alpha = 2 / (n + 1))."""
    out: list[float | None] = [None] * len(values)
    if len(values) < n:
        return out
    e = sum(values[:n]) / n
    out[n - 1] = e
    k = 2 / (n + 1)
    for i in range(n, len(values)):
        e = values[i] * k + e * (1 - k)
        out[i] = e
    return out


def intraday_indicators(bars, now: datetime, price: float, atr: float | None = None) -> dict:
    """Screener-rule fields from 5-min bars (incl. pre-market) plus the live price.
    RSI/EMA use completed bars; VWAP distance, open, high/low of day and range position use the live price."""
    if not bars:
        return {}
    closes = [b.close for b in bars]
    r, e9, e20 = rsi_series(closes)[-1], ema_series(closes, 9)[-1], ema_series(closes, 20)[-1]
    today = [b for b in bars if b.date.date() == now.date()]
    rth = [b for b in today if b.date.time() >= time(9, 30)]
    vol = sum(b.volume for b in rth)
    vwap = sum((b.average if b.average > 0 else b.close) * b.volume for b in rth) / vol if vol else None
    hod = max([b.high for b in today] + [price])
    lod = min([b.low for b in today] + [price])
    out = {
        "rsi_5m": round(r, 1) if r is not None else None,
        "above_ema9": None if e9 is None else int(price > e9),
        "ema_stack": None if e9 is None or e20 is None else int(price > e9 > e20),
        "vwap_dist_pct": round((price / vwap - 1) * 100, 2) if vwap else None,
        "pct_from_open": round((price / rth[0].open - 1) * 100, 2) if rth else None,
        "pct_from_hod": round((price / hod - 1) * 100, 2),
        "range_pos": round((price - lod) / (hod - lod) * 100, 1) if hod > lod else None,
        "mins_since_open": min(390, max(0, (now.hour * 60 + now.minute) - 570)),
    }
    # Last completed 5-min bar's volume vs the average of the 20 before it (breakout-bar confirmation).
    done = [b for b in bars if b.date + timedelta(minutes=5) <= now]
    if len(done) >= 21:
        avg = sum(b.volume for b in done[-21:-1]) / 20
        out["bar_vol_ratio"] = round(done[-1].volume / avg, 2) if avg else None
    # ATR-scaled fields, comparable across a $15 and a $700 stock.
    if atr:
        out["hod_dist_atr"] = round((hod - price) / atr, 2)
        if vwap:
            out["vwap_dist_atr"] = round((price - vwap) / atr, 2)
        if rth:
            out["chg_open_atr"] = round((price - rth[0].open) / atr, 2)
            out["or5_width_atr"] = round((rth[0].high - rth[0].low) / atr, 2)  # first 5-min bar = 5-min OR
    return out
