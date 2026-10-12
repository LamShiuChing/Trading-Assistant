from datetime import datetime, timedelta
from types import SimpleNamespace as NS
from zoneinfo import ZoneInfo

import pytest

from app import rules as R
from app.indicators import ema_series, intraday_indicators

ET = ZoneInfo("America/New_York")
F = {"rvol": 6.0, "rsi_5m": 72.0, "price": 25.0, "ema_stack": 1, "vwap_dist_pct": None}


def test_require_and_score_rules():
    rules = R.validate([
        {"field": "rvol", "op": ">", "value": 5, "action": "score", "points": 10},
        {"field": "rsi_5m", "op": "<", "value": 70, "action": "require"},
        {"field": "ema_stack", "op": "=", "value": 1, "action": "score", "points": 5},
        {"field": "vwap_dist_pct", "op": ">", "value": 0, "action": "require"},
        {"field": "price", "op": ">", "value": 1000, "action": "score", "points": 50, "on": False},  # switched off
    ])
    fails, points, hits = R.apply(F, rules)
    assert fails == ["rule: rsi_5m < 70", "rule: vwap_dist_pct > 0 (no data)"]
    assert points == 15 and hits == ["rvol > 5 +10", "ema_stack = 1 +5"]


def test_validation_rejects_bad_rules():
    for bad in ({"field": "nope", "op": ">", "value": 1, "action": "require"},
                {"field": "rvol", "op": "=>", "value": 1, "action": "require"},
                {"field": "rvol", "op": ">", "value": "abc", "action": "require"},
                {"field": "rvol", "op": ">", "value": 1, "action": "score", "points": 500}):
        with pytest.raises((ValueError, KeyError)):
            R.validate([bad])


def test_ema_matches_definition():
    e = ema_series([1, 2, 3, 4, 5], 3)
    assert e[:2] == [None, None] and e[2] == 2.0 and e[3] == 3.0 and e[4] == 4.0  # seed SMA, alpha 0.5


def test_intraday_indicators():
    t0 = datetime(2026, 10, 12, 9, 0, tzinfo=ET)
    # pre-market 09:00-09:25 around 10, then RTH from 09:30 rising to 12
    bars = [NS(date=t0 + timedelta(minutes=5 * i), open=10, high=10.2, low=9.8, close=10, volume=100, average=10) for i in range(6)]
    bars += [NS(date=t0 + timedelta(minutes=30 + 5 * i), open=10 + i * 0.2, high=10.3 + i * 0.2, low=10 + i * 0.2 - 0.1,
                close=10.2 + i * 0.2, volume=1000, average=10.1 + i * 0.2) for i in range(10)]
    f = intraday_indicators(bars, datetime(2026, 10, 12, 10, 20, tzinfo=ET), price=11.5, atr=2.0)
    assert f["pct_from_open"] == 15.0                      # 11.5 vs the 09:30 open of 10
    assert f["pct_from_hod"] == round((11.5 / 12.1 - 1) * 100, 2)
    assert 0 <= f["range_pos"] <= 100 and f["vwap_dist_pct"] > 0
    assert f["above_ema9"] == 1 and f["rsi_5m"] > 70         # steady rise -> above EMA 9, high RSI
    assert f["ema_stack"] is None                            # only 16 bars: EMA 20 needs 20 -> no data, not a guess
    assert f["chg_open_atr"] == 0.75 and f["hod_dist_atr"] == 0.3   # (11.5-10)/2 ; (12.1-11.5)/2
    assert f["or5_width_atr"] == round((10.3 - 9.9) / 2, 2) and f["mins_since_open"] == 50
    assert "bar_vol_ratio" not in f                          # fewer than 21 completed bars: no ratio
