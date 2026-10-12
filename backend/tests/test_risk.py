from datetime import datetime
from zoneinfo import ZoneInfo

from app.risk import parse_liquid_hours, target_price

ET = ZoneInfo("US/Eastern")


def test_targets():
    lv = {"pdh": 52.0, "pmh": 50.8, "or5": {"high": 50.4, "low": 49.8}}
    assert target_price("r", True, 50.0, 49.5, 2.0, None, {}) == 51.0
    assert target_price("r", False, 50.0, 50.5, 3.0, None, {}) == 48.5
    assert target_price("atr", True, 50.0, 49.5, 2.0, 0.4, {}) == 50.6        # 1.5 x ATR
    assert target_price("level", True, 50.0, 49.5, 2.0, None, lv) == 50.8     # nearest level >= 1R away (OR high 50.4 is too close)
    assert target_price("level", True, 50.0, 49.0, 2.0, None, lv) == 52.0     # 1R = $1: next level is PDH
    assert target_price("level", True, 50.0, 47.0, 2.0, None, lv) == 56.0     # none far enough: default 2R
    assert target_price("trail", True, 50.0, 49.5, 2.0, None, lv) is None


def test_liquid_hours_normal_half_day_and_closed():
    h = parse_liquid_hours("20261125:0930-20261125:1600;20261126:CLOSED;20261127:0930-20261127:1300", ET)
    assert h[datetime(2026, 11, 25).date()][1] == datetime(2026, 11, 25, 16, 0, tzinfo=ET)
    assert datetime(2026, 11, 26).date() not in h                          # Thanksgiving
    assert h[datetime(2026, 11, 27).date()][1].hour == 13                  # half-day: flatten at 12:50
