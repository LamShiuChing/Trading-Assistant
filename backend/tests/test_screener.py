from datetime import date, datetime, timedelta
from types import SimpleNamespace as NS
from zoneinfo import ZoneInfo

from app.screener import check_filters, daily_stats, rvol, score

ET = ZoneInfo("America/New_York")


def test_daily_stats_ignores_today_and_computes_atr():
    start = date(2026, 9, 1)
    bars = [NS(date=start + timedelta(days=i), high=11.0, low=9.0, close=10.0, volume=1_000_000) for i in range(20)]
    bars.append(NS(date=date(2026, 10, 12), high=50, low=1, close=40, volume=9))  # today's partial bar: ignored
    d = daily_stats(bars, date(2026, 10, 12))
    assert d["atr"] == 2.0 and d["prev_close"] == 10.0
    assert d["avg_vol"] == 1_000_000 and d["avg_usd_vol"] == 10_000_000
    assert daily_stats(bars[:10], date(2026, 10, 12)) is None  # not enough history


def bar(day, hh, mm, vol):
    return NS(date=datetime(2026, 10, day, hh, mm, tzinfo=ET), volume=vol)


def test_rvol_is_time_of_day_matched():
    bars = [bar(8, 9, 0, 100), bar(8, 12, 0, 900),   # prior day: 100 by 09:00
            bar(9, 9, 0, 300), bar(9, 12, 0, 900),   # prior day: 300 by 09:00
            bar(12, 8, 0, 400), bar(12, 9, 0, 400)]  # today: 800 by 09:00
    assert rvol(bars, datetime(2026, 10, 12, 9, 0, tzinfo=ET)) == 4.0  # 800 / avg(100, 300)
    assert rvol([bar(12, 8, 0, 5)], datetime(2026, 10, 12, 9, 0, tzinfo=ET)) is None


GOOD = {"price": 50.0, "avg_usd_vol": 40e6, "gap_atr": 2.0, "rvol": 5.0, "spread_pct": 0.05}


def test_filters():
    assert check_filters(GOOD) == []
    bad = {**GOOD, "price": 5.0, "rvol": None, "spread_pct": 0.8}
    assert check_filters(bad) == ["price < 10", "RVOL < 3.0", "spread > 0.3%"]


def test_score_parts():
    total, parts = score(GOOD)
    assert parts["gap"] == 20.0 and parts["rvol"] == round(10 * 2.321928, 1) and parts["liquidity"] == 1.5
    assert parts["spread"] == -2.0 and parts["catalyst"] == 0.0
    assert total == round(sum(parts.values()), 1)
    assert score({**GOOD, "gap_atr": -3.0})[1]["gap"] == 0.0  # long only: gap down earns nothing
    assert score({**GOOD, "catalyst": "earnings"})[1]["catalyst"] == 10.0
