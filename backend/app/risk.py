"""Portfolio risk rules, the session clock (from IBKR liquidHours), the daily loss lockout, and force-flat."""
import asyncio
import logging
import math
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from ib_async import Stock

from . import db
from .config import CFG
from .hub import publish
from .portfolio import stop_price

log = logging.getLogger(__name__)
R = CFG["risk"]
ET = ZoneInfo("America/New_York")


# ---------- pure helpers (unit tested) ----------

def position_size(risk_usd: float, entry: float, stop: float, max_value: float, buying_power: float | None) -> int:
    """Shares such that hitting the stop loses about risk_usd, capped by max position value and buying power."""
    per_share = abs(entry - stop)
    if per_share <= 0 or entry <= 0:
        return 0
    qty = math.floor(risk_usd / per_share)
    qty = min(qty, math.floor(max_value / entry))
    if buying_power is not None:
        qty = min(qty, math.floor(buying_power / entry))
    return max(qty, 0)


def target_price(method: str, long: bool, entry: float, stop: float, r_mult: float,
                 atr: float | None, levels: dict) -> float | None:
    """Take-profit price for a method. None means no fixed target (trailing)."""
    risk = abs(entry - stop)
    d = 1 if long else -1
    if method == "trail":
        return None
    if method == "atr" and atr:
        return round(entry + d * R["atr_target_mult"] * atr, 2)
    if method == "level":
        # Nearest key level at least 1R away in the trade's direction; else fall back to the default R.
        cands = [levels.get(k) for k in ("pdh", "pdl", "pmh", "pml")]
        for rng in (levels.get("or5"), levels.get("or15")):
            if rng:
                cands += [rng["high"], rng["low"]]
        ok = sorted(p for p in cands if p is not None and d * (p - entry) >= risk)
        if ok:
            return round(ok[0] if long else ok[-1], 2)
        r_mult = R["default_target_r"]
    return round(entry + d * r_mult * risk, 2)


def parse_liquid_hours(s: str, tz: ZoneInfo) -> dict[date, tuple[datetime, datetime]]:
    """'20261012:0930-20261012:1600;20261013:CLOSED;...' -> {date: (open, close)} (first open, last close)."""
    out: dict[date, tuple[datetime, datetime]] = {}
    for seg in s.replace(",", ";").split(";"):
        if "-" not in seg:
            continue  # CLOSED days
        a, b = seg.split("-")
        o = datetime.strptime(a, "%Y%m%d:%H%M").replace(tzinfo=tz)
        c = datetime.strptime(b, "%Y%m%d:%H%M").replace(tzinfo=tz)
        prev = out.get(o.date())
        out[o.date()] = (min(prev[0], o), max(prev[1], c)) if prev else (o, c)
    return out


# ---------- live risk manager ----------

class RiskManager:
    def __init__(self, broker, portfolio):
        self.broker, self.portfolio = broker, portfolio
        self.hours: dict[date, tuple[datetime, datetime]] = {}
        self.industry: dict[int, str] = {}  # conId -> industry
        self.flatten_all = None  # set by orders.Orders (avoids a circular import)
        self._last_flatten: datetime | None = None
        broker.on_ready.append(self._load_session)

    async def _load_session(self) -> None:
        d = (await self.broker.ib.reqContractDetailsAsync(Stock("SPY", "SMART", "USD")))[0]
        self.hours = parse_liquid_hours(d.liquidHours, ZoneInfo(d.timeZoneId))
        log.info("session today: %s", self.session())

    async def industry_of(self, contract) -> str:
        if contract.conId not in self.industry:
            ds = await self.broker.ib.reqContractDetailsAsync(contract)
            self.industry[contract.conId] = (ds[0].industry if ds else "") or ""
        return self.industry[contract.conId]

    def session(self, now: datetime | None = None) -> dict | None:
        now = now or datetime.now(ET)
        s = self.hours.get(now.date())
        if not s:
            return None
        o, c = s
        return {"open": o, "close": c,
                "cutoff": c - timedelta(minutes=R["entry_cutoff_min"]),
                "flatten": c - timedelta(minutes=R["flatten_min"])}

    def locked(self, day: str) -> str | None:
        row = db.conn.execute("SELECT reason FROM lockouts WHERE day=?", (day,)).fetchone()
        return row["reason"] if row else None

    def _open_exposure(self) -> dict:
        """Positions and working entries, with $ risk at stop for each."""
        ib = self.broker.ib
        trades = ib.openTrades()
        items = []
        for p in ib.portfolio(self.broker.account):
            if not p.position:
                continue
            stop = next((stop_price(t.order) for t in trades if t.contract.conId == p.contract.conId
                         and stop_price(t.order) is not None), None)
            # ponytail: a position with no working stop is counted at one trade's risk. It's unprotected
            # and gets flagged in the UI; force-flat clears it by the close.
            risk = abs(p.averageCost - stop) * abs(p.position) if stop else R["risk_per_trade"]
            items.append({"symbol": p.contract.symbol, "conId": p.contract.conId, "value": abs(p.marketValue),
                          "risk": risk, "unprotected": stop is None})
        held = {i["conId"] for i in items}
        for t in trades:  # working entries (parents) not yet filled
            o = t.order
            if o.parentId == 0 and o.orderRef.startswith("TA") and t.contract.conId not in held:
                kids = [k.order for k in trades if k.order.parentId == o.orderId and k.order.orderType == "STP"]
                risk = abs(o.lmtPrice - kids[0].auxPrice) * o.totalQuantity if kids else R["risk_per_trade"]
                items.append({"symbol": t.contract.symbol, "conId": t.contract.conId,
                              "value": o.lmtPrice * o.totalQuantity, "risk": risk, "unprotected": False})
        return {"items": items, "risk": sum(i["risk"] for i in items)}

    def state(self) -> dict:
        now = datetime.now(ET)
        day = now.date().isoformat()
        s = self.session(now)
        summary = (self.portfolio.last or {}).get("summary", {})
        day_pnl = summary.get("day_pnl")
        exp = self._open_exposure() if self.broker.account else {"items": [], "risk": 0}
        trades_today = db.conn.execute("SELECT COUNT(*) FROM trades WHERE day=? AND status='filled'", (day,)).fetchone()[0]
        blockers = []
        if not self.broker.can_trade():
            blockers.append("Not connected (or account doesn't match the mode)")
        if lock := self.locked(day):
            blockers.append(f"Locked for today: {lock}")
        if not s:
            blockers.append("Market closed today")
        elif now < s["open"]:
            blockers.append(f"Before the open ({s['open']:%H:%M})")
        elif now >= s["cutoff"]:
            blockers.append(f"After the entry cutoff ({s['cutoff']:%H:%M})")
        if trades_today >= R["max_trades_per_day"]:
            blockers.append(f"Max trades per day reached ({trades_today})")
        return {
            "limits": {k: R[k] for k in ("risk_per_trade", "daily_max_loss", "max_positions", "max_open_risk",
                                         "max_trades_per_day", "max_sector_exposure", "max_position_value")},
            "used": {"day_pnl": day_pnl, "open_risk": round(exp["risk"], 2), "positions": len(exp["items"]),
                     "trades_today": trades_today},
            "unprotected": [i["symbol"] for i in exp["items"] if i["unprotected"]],
            "session": {k: v.strftime("%H:%M") for k, v in s.items()} if s else None,
            "blockers": blockers,
        }

    async def check_entry(self, contract, qty: int, entry: float, stop: float) -> list[str]:
        """Every rule a new entry must pass. Empty list means allowed."""
        st = self.state()
        out = list(st["blockers"])
        exp = self._open_exposure()
        risk = abs(entry - stop) * qty
        if qty <= 0:
            out.append("Size is 0 (stop too wide, or not enough buying power)")
        if not any(i["conId"] == contract.conId for i in exp["items"]) and len(exp["items"]) + 1 > R["max_positions"]:
            out.append(f"Max positions ({R['max_positions']}) reached")
        if exp["risk"] + risk > R["max_open_risk"] + 0.01:
            out.append(f"Open risk would be ${exp['risk'] + risk:,.0f} (max ${R['max_open_risk']:,})")
        ind = await self.industry_of(contract)
        if ind:
            same = 0.0
            for p in self.broker.ib.portfolio(self.broker.account):
                if p.position and await self.industry_of(p.contract) == ind:
                    same += abs(p.marketValue)
            if same + qty * entry > R["max_sector_exposure"]:
                out.append(f"{ind} exposure would be ${same + qty * entry:,.0f} (max ${R['max_sector_exposure']:,})")
        return out

    async def run(self) -> None:
        """Every 2 s: publish the risk state, enforce the daily loss lockout, and force-flat near the close."""
        while True:
            try:
                await self._tick()
            except Exception:
                log.exception("risk tick failed")
            await asyncio.sleep(2)

    async def _tick(self) -> None:
        if self.broker.state != "ready":
            return
        now = datetime.now(ET)
        day = now.date().isoformat()
        st = self.state()
        pnl = st["used"]["day_pnl"]
        if pnl is not None and pnl <= -R["daily_max_loss"] and not self.locked(day):
            reason = f"daily loss {pnl:,.2f} hit the -{R['daily_max_loss']:,} limit"
            db.conn.execute("INSERT OR IGNORE INTO lockouts VALUES (?,?,?)", (day, reason, db.now()))
            publish("alert", {"level": "error", "message": f"Entries locked for today: {reason}"})
            st = self.state()
        publish("risk", st)

        s = self.session(now)
        # Force-flat from flatten time until the end of after-hours (20:00), so nothing is held overnight,
        # even if we were disconnected at 15:50. Retries every 30 s while anything is left.
        if s and s["flatten"] <= now < s["close"].replace(hour=20, minute=0):
            ib = self.broker.ib
            busy = any(p.position for p in ib.positions()) or ib.openTrades()
            if busy and (not self._last_flatten or now - self._last_flatten > timedelta(seconds=30)):
                self._last_flatten = now
                publish("alert", {"level": "warn", "message": f"Force-flat ({s['flatten']:%H:%M}): cancelling all orders and closing all positions"})
                await self.flatten_all("force-flat")
