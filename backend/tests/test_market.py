from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.market import analyze, chart_time, sr_levels
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


def test_rvol_is_time_matched():
    bars = [
        bar(8, "09:30", 10, 10, 10, 10, 100, 10), bar(8, "09:31", 10, 10, 10, 10, 300, 10),  # prior day 1
        bar(9, "09:30", 10, 10, 10, 10, 200, 10), bar(9, "09:35", 10, 10, 10, 10, 900, 10),  # prior day 2
        bar(12, "09:30", 10, 10, 10, 10, 600, 10), bar(12, "09:31", 10, 10, 10, 10, 200, 10),
    ]
    rv = [r["rvol"] for r in analyze(bars)[0]]
    assert rv[:4] == [None] * 4                 # only today gets RVOL
    assert rv[4] == 600 / ((100 + 200) / 2)     # 4.0: same minute on prior days
    assert rv[5] == round(800 / ((400 + 200) / 2), 2)  # 2.67 -> day 2 had no 09:31 bar, so its 09:30 total counts


def test_sr_levels_cluster_repeated_pivots():
    # Price swings between ~10 and ~12 on 5-min bars: highs near 12 twice, lows near 10 twice.
    path = [10.05, 10.2, 10.5, 11, 11.5, 11.8, 12, 11.8, 11.5, 11, 10.5, 10.2, 10, 10.2, 10.5, 11, 11.5, 11.8,
            12.02, 11.8, 11.5, 11, 10.5, 10.2, 10.01, 10.2, 10.5, 11, 11.5, 11.8, 11]
    bars = []
    for i, p in enumerate(path):
        m = 30 + i * 5
        bars.append(bar(12, f"{9 + m // 60:02d}:{m % 60:02d}", p, p + 0.05, p - 0.05, p, 100, p))
    sr = sr_levels(bars, known=[])
    assert [(x["side"], x["touches"]) for x in sr] == [("R", 2), ("S", 2)]
    assert abs(sr[0]["price"] - 12.06) < 0.01 and abs(sr[1]["price"] - 9.955) < 0.01
    assert sr_levels(bars, known=[12.05]) == [sr[1]]   # duplicates a key level (e.g. PDH) -> dropped
