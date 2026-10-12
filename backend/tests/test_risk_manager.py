"""RiskManager with fake IBKR objects: daily-loss lockout persists, force-flat fires in its window only."""
import asyncio
import sqlite3
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app import db, risk as risk_mod
from app.risk import ET, RiskManager


class FakeIB:
    def __init__(self, positions=()):
        self._pos = list(positions)

    def portfolio(self, account=None):
        return [SimpleNamespace(position=q, contract=SimpleNamespace(symbol=s, conId=i), averageCost=10.0,
                                marketValue=q * 10.0) for i, (s, q) in enumerate(self._pos)]

    def positions(self):
        return [SimpleNamespace(position=q) for _, q in self._pos]

    def openTrades(self):
        return []


def make(day_pnl, positions=(("AAPL", 10),), now=None):
    broker = SimpleNamespace(ib=FakeIB(positions), account="DU1", state="ready", on_ready=[], can_trade=lambda: True)
    portfolio = SimpleNamespace(last={"summary": {"day_pnl": day_pnl}})
    rm = RiskManager(broker, portfolio)
    today = (now or datetime.now(ET)).date()
    rm.hours = {today: (datetime.combine(today, datetime.min.time(), ET).replace(hour=9, minute=30),
                        datetime.combine(today, datetime.min.time(), ET).replace(hour=16))}
    calls = []

    async def flatten_all(reason):
        calls.append(reason)
    rm.flatten_all = flatten_all
    return rm, calls


@pytest.fixture(autouse=True)
def temp_db(monkeypatch):
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(db.SCHEMA)
    monkeypatch.setattr(db, "conn", conn)
    monkeypatch.setattr(risk_mod, "publish", lambda *a: None)


def at(hour, minute):
    return datetime.now(ET).replace(hour=hour, minute=minute, second=0, microsecond=0)


def run_tick(rm, now, monkeypatch):
    class FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return now
    monkeypatch.setattr(risk_mod, "datetime", FakeDT)
    asyncio.run(rm._tick())


def test_lockout_triggers_and_persists(monkeypatch):
    rm, _ = make(day_pnl=-800)  # limit is 750
    run_tick(rm, at(11, 0), monkeypatch)
    day = at(11, 0).date().isoformat()
    assert "daily loss" in rm.locked(day)
    # A fresh manager (= app restart) with P/L back above the limit is still locked.
    rm2, _ = make(day_pnl=+100)
    run_tick(rm2, at(11, 5), monkeypatch)
    assert any("Locked for today" in b for b in rm2.state()["blockers"])


def test_no_lockout_above_limit(monkeypatch):
    rm, _ = make(day_pnl=-749)
    run_tick(rm, at(11, 0), monkeypatch)
    assert rm.locked(at(11, 0).date().isoformat()) is None


def test_cutoff_blocks_entries(monkeypatch):
    rm, _ = make(day_pnl=0)
    run_tick(rm, at(15, 31), monkeypatch)
    assert any("cutoff" in b for b in rm.state()["blockers"])


def test_force_flat_only_in_window(monkeypatch):
    rm, calls = make(day_pnl=0)
    run_tick(rm, at(15, 49), monkeypatch)
    assert calls == []
    run_tick(rm, at(15, 50), monkeypatch)
    assert calls == ["force-flat"]
    run_tick(rm, at(15, 50) + timedelta(seconds=10), monkeypatch)  # retries at most every 30 s
    assert calls == ["force-flat"]
    run_tick(rm, at(15, 50) + timedelta(seconds=40), monkeypatch)
    assert calls == ["force-flat", "force-flat"]


def test_force_flat_skips_when_flat(monkeypatch):
    rm, calls = make(day_pnl=0, positions=())
    run_tick(rm, at(15, 55), monkeypatch)
    assert calls == []
