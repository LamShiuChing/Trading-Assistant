from datetime import datetime, timedelta
from types import SimpleNamespace as NS
from zoneinfo import ZoneInfo

from app.market import analyze
from app.signals import evaluate, outcome, rsi_series, sma_series

ET = ZoneInfo("US/Eastern")


def bar(t, o, h, l, c, v=1000):
    return NS(date=t, open=o, high=h, low=l, close=c, volume=v, average=(h + l + c) / 3)


def day_bars(today_closes):
    """A prior day (for levels) + today from 09:30, one bar per close (high/low +/- 0.05)."""
    prev = [bar(datetime(2026, 10, 9, 9, 30, tzinfo=ET) + timedelta(minutes=i), 99, 99.5, 98.5, 99) for i in range(30)]
    t0 = datetime(2026, 10, 12, 9, 30, tzinfo=ET)
    today = [bar(t0 + timedelta(minutes=i), c, c + 0.05, c - 0.05, c) for i, c in enumerate(today_closes)]
    return prev + today


def setups_at(bars):
    rows, lv = analyze(bars)
    return {name for name, *_ in evaluate(rows, lv)}


def test_orb5_fires_once_on_first_close_above_range():
    closes = [100.0, 100.4, 100.9, 100.2, 100.5] + [100.6] * 5 + [101.5, 101.6]
    bars = day_bars(closes)
    assert "ORB5" not in setups_at(bars[:-2])   # nothing broke out yet
    assert "ORB5" in setups_at(bars[:-1])       # 09:40 closes above the 5-min high (100.95)
    assert "ORB5" not in setups_at(bars)        # 09:41: not the first close above any more
    rows, lv = analyze(bars[:-1])
    stop = next(s for n, s, _ in evaluate(rows, lv) if n == "ORB5")
    assert stop == round(lv["or5"]["low"] - 0.01, 2)


def test_orb15_waits_for_its_range():
    closes = [100.0, 100.4, 100.9, 100.2, 100.5] + [100.6] * 5 + [101.5]
    assert "ORB15" not in setups_at(day_bars(closes))  # 09:40 is still inside the 15-min range


def test_tight_stops_are_widened_to_the_floor(monkeypatch):
    import app.signals as sig
    monkeypatch.setattr(sig, "DETECTORS", {"tight": lambda rows, lv: (rows[-1]["close"] - 0.01, "1-cent stop")})
    rows, lv = analyze(day_bars([100.0] * 10))
    [(name, stop, _)] = evaluate(rows, lv)
    assert stop == round(100.0 - 0.2, 2)  # widened from 99.99 to 0.2% below the entry


def test_outcome_target_stop_and_horizons():
    mk = lambda h, l, c: NS(high=h, low=l, close=c)
    # entry 10, stop 9 (1R = $1)
    win = outcome([mk(10.5, 9.8, 10.2)] * 4 + [mk(12.1, 10.0, 12.0)], 10.0, 9.0)
    assert win["result_r"] == 2.0 and win["r_5m"] == 2.0 and win["mfe_r"] == 2.1 and win["mae_r"] == -0.2
    loss = outcome([mk(10.3, 8.9, 9.0), mk(13, 9, 12)], 10.0, 9.0)
    assert loss["result_r"] == -1.0                      # stop first, even though price ran later
    both = outcome([mk(12.5, 8.5, 10)], 10.0, 9.0)
    assert both["result_r"] == -1.0                      # same bar touches both: count the stop
    flat = outcome([mk(10.4, 9.6, 10.3)] * 3, 10.0, 9.0)
    assert flat["result_r"] == 0.3 and flat["eod_r"] == 0.3   # neither hit: exit at the close


def test_rsi_and_ma_match_frontend_math():
    closes = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28]
    assert round(rsi_series(closes)[14], 2) == 70.46            # same value as the chart's TypeScript RSI
    assert sma_series([None, None, 1, 2, 3, 4, 5], 3) == [None, None, None, None, 2, 3, 4]
