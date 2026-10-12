from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.market import analyze, chart_time
from app.portfolio import current_r

ET = ZoneInfo("US/Eastern")


def bar(day, hm, o, h, l, c, v, avg):
    hh, mm = map(int, hm.split(":"))
    return SimpleNamespace(date=datetime(2026, 10, day, hh, mm, tzinfo=ET),
                           open=o, high=h, low=l, close=c, volume=v, average=avg)


BARS = [
    bar(9, "09:30", 10, 12, 9, 11, 100, 10.5),   # prior day RTH
    bar(9, "15:59", 11, 13, 10, 12, 100, 12),    # prior day close bar -> PDC 12, PDH 13, PDL 9
    bar(9, "17:00", 12, 20, 1, 12, 100, 12),     # after hours: must NOT count toward PDH/PDL
    bar(12, "08:00", 12, 14, 11, 13, 50, 13),    # pre-market -> PMH 14, PML 11
    bar(12, "09:30", 13, 15, 12, 14, 100, 14),   # RTH: VWAP starts here
    bar(12, "09:34", 14, 16, 13, 15, 300, 15),   # still inside 5-min OR
    bar(12, "09:40", 15, 17, 14, 16, 100, 16),   # inside 15-min OR only
]


def test_levels_and_vwap():
    rows, lv = analyze(BARS)
    assert (lv["pdh"], lv["pdl"], lv["pdc"]) == (13, 9, 12)
    assert (lv["pmh"], lv["pml"]) == (14, 11)
    assert (lv["or5"]["high"], lv["or5"]["low"], lv["or5"]["complete"]) == (16, 12, True)
    assert (lv["or15"]["high"], lv["or15"]["low"], lv["or15"]["complete"]) == (17, 12, False)
    vw = [r["vwap"] for r in rows]
    assert vw[3] is None                       # pre-market: no VWAP
    assert vw[4] == 14                         # first RTH bar
    assert vw[5] == (14 * 100 + 15 * 300) / 400  # 14.75, volume-weighted
    assert vw[0] == 10.5                       # each day re-anchors at 09:30


def test_chart_time_is_et_wall_clock():
    # 09:30 ET shown as 09:30 on a UTC axis
    assert chart_time(datetime(2026, 10, 12, 9, 30, tzinfo=ET)) == datetime(2026, 10, 12, 9, 30, tzinfo=timezone.utc).timestamp()
    assert chart_time(datetime(2026, 12, 14, 9, 30, tzinfo=ET)) == datetime(2026, 12, 14, 9, 30, tzinfo=timezone.utc).timestamp()  # EST


def test_current_r():
    assert current_r(100, 10.0, 11.0, 9.5) == 2.0     # long: +1 vs 0.5 risk
    assert current_r(-100, 10.0, 9.0, 10.5) == 2.0    # short
    assert current_r(100, 10.0, 11.0, None) is None   # no stop -> no R
