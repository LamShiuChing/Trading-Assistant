"""Order ticket: stage (preview with size, $ risk, R:R and every risk check) -> confirm -> bracket.

Bracket = entry LMT (parent) + STP exit + optional LMT target + optional MKT exit at flatten time
(IBKR-side force-flat backup). The exits share one OCA group, so one fill cancels the others.
Every order carries orderRef "TA<journal id>" so fills can be matched to the journal.
"""
import asyncio
import logging
import time
import uuid
from datetime import datetime, timedelta

from ib_async import LimitOrder, MarketOrder, Order, Stock

from . import db
from .config import CFG
from .hub import publish
from .market import atr
from .portfolio import num
from .risk import ET, R, position_size, target_price

log = logging.getLogger(__name__)
TP_METHODS = ("r", "atr", "level", "trail")
PREVIEW_TTL = 120  # s; a staged ticket must be confirmed within this


class Orders:
    def __init__(self, broker, risk, chart, portfolio):
        self.broker, self.risk, self.chart, self.portfolio = broker, risk, chart, portfolio
        self.previews: dict[str, tuple[float, dict]] = {}
        risk.flatten_all = self.flatten_all
        broker.ib.orderStatusEvent += self._on_status

    # ---------- stage ----------

    async def preview(self, symbol: str, side: str, entry: float, stop: float, tp_method: str,
                      target_r: float | None, target: float | None, qty_max: int | None = None) -> dict:
        ib = self.broker.ib
        long = side == "BUY"
        entry, stop = round(entry, 2), round(stop, 2)
        if (long and stop >= entry) or (not long and stop <= entry):
            raise ValueError("Stop must be below the entry for a long, above it for a short")
        contract = Stock(symbol, "SMART", "USD")
        await ib.qualifyContractsAsync(contract)
        if not contract.conId:
            raise ValueError(f"Unknown symbol {symbol}")

        on_chart = self.chart.symbol == symbol and self.chart.bars
        if target is not None:  # dragged on the chart
            method = "manual"
            if (long and target <= entry) or (not long and target >= entry):
                raise ValueError("Target must be on the profit side of the entry")
        else:
            method = tp_method
            if method in ("atr", "level") and not on_chart:
                raise ValueError("ATR and key-level targets need the symbol open on the chart")
            a = atr(self.chart.bars) if on_chart else None
            if method == "atr" and not a:
                raise ValueError("Not enough 5-min bars today for ATR(14)")
            levels = self.chart.levels if on_chart else {}
            target = target_price(method, long, entry, stop, target_r or R["default_target_r"], a, levels)

        summary = (self.portfolio.last or {}).get("summary", {})
        qty = position_size(R["risk_per_trade"], entry, stop, R["max_position_value"], summary.get("buying_power"))
        if qty_max:
            qty = min(qty, qty_max)
        risk_usd = round(qty * abs(entry - stop), 2)
        p = {
            "symbol": symbol, "conId": contract.conId, "side": side, "qty": qty,
            "entry": entry, "stop": stop, "target": target, "tp_method": method,
            "risk": risk_usd, "reward": round(qty * abs(target - entry), 2) if target else None,
            "rr": round(abs(target - entry) / abs(entry - stop), 2) if target else None,
            "trail": method == "trail",
            "blockers": await self.risk.check_entry(contract, qty, entry, stop),
        }
        pid = uuid.uuid4().hex[:12]
        self.previews[pid] = (time.monotonic(), p)
        return {"id": pid, **p}

    # ---------- confirm ----------

    async def confirm(self, pid: str) -> dict:
        created, p = self.previews.pop(pid, (0, None))
        if not p or time.monotonic() - created > PREVIEW_TTL:
            raise ValueError("This ticket expired. Stage it again.")
        ib = self.broker.ib
        contract = Stock(p["symbol"], "SMART", "USD")
        await ib.qualifyContractsAsync(contract)
        # Re-check: P/L, positions or the clock may have changed since staging.
        blockers = await self.risk.check_entry(contract, p["qty"], p["entry"], p["stop"])
        if blockers:
            raise ValueError("Blocked: " + "; ".join(blockers))

        now = datetime.now(ET)
        cur = db.conn.execute(
            "INSERT INTO trades (day, created, symbol, side, qty, entry, stop, target, tp_method, risk, status)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,'submitted')",
            (now.date().isoformat(), db.now(), p["symbol"], p["side"], p["qty"], p["entry"], p["stop"],
             p["target"], p["tp_method"], p["risk"]))
        ref = f"TA{cur.lastrowid}"
        exit_side = "SELL" if p["side"] == "BUY" else "BUY"
        oca = f"{ref}-exits"
        r_dist = abs(p["entry"] - p["stop"])

        parent = LimitOrder(p["side"], p["qty"], p["entry"], orderId=ib.client.getReqId(),
                            tif="DAY", orderRef=ref, transmit=False)
        exits_common = dict(parentId=parent.orderId, tif="DAY", orderRef=ref, ocaGroup=oca, ocaType=1, transmit=False)
        stop = Order(action=exit_side, orderType="STP", totalQuantity=p["qty"], auxPrice=p["stop"],
                     orderId=ib.client.getReqId(), **exits_common)
        if p["trail"]:
            # IBKR adjusted order: at +1R the stop becomes a trailing stop, starting at breakeven, trailing by 1R.
            # https://interactivebrokers.github.io/tws-api/adjustable_stops.html
            d = 1 if p["side"] == "BUY" else -1
            stop.triggerPrice = round(p["entry"] + d * r_dist, 2)
            stop.adjustedOrderType = "TRAIL"
            stop.adjustedStopPrice = p["entry"]
            stop.adjustedTrailingAmount = round(r_dist, 2)
            stop.adjustableTrailingUnit = 0  # amount, not percent
        children = [stop]
        if p["target"] is not None:
            children.append(LimitOrder(exit_side, p["qty"], p["target"], orderId=ib.client.getReqId(), **exits_common))
        sess = self.risk.session(now)
        if CFG["risk"]["server_side_flatten"] and sess:
            # Backup force-flat that IBKR runs itself if this app or Gateway is down. It fires 2 min AFTER the
            # app's own force-flat, which cancels it first, so the two can never both sell the same shares.
            backup_at = sess["flatten"] + timedelta(minutes=2)
            children.append(MarketOrder(exit_side, p["qty"], orderId=ib.client.getReqId(),
                                        goodAfterTime=backup_at.strftime("%Y%m%d %H:%M:%S US/Eastern"),
                                        **exits_common))
        children[-1].transmit = True  # sends the whole group at once

        trades = [ib.placeOrder(contract, o) for o in (parent, *children)]
        db.conn.execute("UPDATE trades SET parent_id=? WHERE id=?", (parent.orderId, cur.lastrowid))
        # Report only what IBKR confirms: wait briefly for each order to be acknowledged.
        for _ in range(20):
            if all(t.orderStatus.status not in ("PendingSubmit", "") for t in trades):
                break
            await asyncio.sleep(0.25)
        statuses = [{"id": t.order.orderId, "type": t.order.orderType + (" (trail at +1R)" if t.order.adjustedOrderType else ""),
                     "status": t.orderStatus.status} for t in trades]
        errors = [e.message for t in trades for e in t.log if e.errorCode and e.errorCode not in (399,)]
        log.info("bracket %s %s: %s %s", ref, p, statuses, errors)
        return {"ref": ref, "parent_id": parent.orderId, "statuses": statuses, "errors": errors}

    def _on_status(self, trade) -> None:
        o = trade.order
        if not o.orderRef.startswith("TA") or o.parentId != 0:
            return
        st = trade.orderStatus
        if st.filled > 0:
            db.conn.execute("UPDATE trades SET status='filled' WHERE parent_id=? AND status!='filled'", (o.orderId,))
        elif st.status in ("Cancelled", "ApiCancelled", "Inactive"):
            db.conn.execute("UPDATE trades SET status='cancelled' WHERE parent_id=? AND status='submitted'", (o.orderId,))

    # ---------- cancel / flatten / kill ----------

    def cancel(self, order_id: int) -> None:
        t = next((t for t in self.broker.ib.openTrades() if t.order.orderId == order_id), None)
        if not t:
            raise ValueError("Order not found among working orders")
        self.broker.ib.cancelOrder(t.order)  # cancelling an entry also cancels its exits

    def _close_order(self, qty: float, last: float | None) -> Order:
        last = num(last)
        action = "SELL" if qty > 0 else "BUY"
        s = self.risk.session()
        now = datetime.now(ET)
        if s and s["open"] <= now < s["close"] or not last:
            return MarketOrder(action, abs(qty), tif="DAY", orderRef="TA-flatten")
        # Outside regular hours IBKR rejects market orders: use a marketable limit that can fill extended-hours.
        px = round(last * (0.995 if action == "SELL" else 1.005), 2)
        return LimitOrder(action, abs(qty), px, tif="DAY", outsideRth=True, orderRef="TA-flatten")

    async def flatten_symbol(self, symbol: str) -> None:
        ib = self.broker.ib
        for t in ib.openTrades():
            if t.contract.symbol == symbol:
                ib.cancelOrder(t.order)
        await asyncio.sleep(0.5)
        for p in ib.portfolio(self.broker.account):
            if p.contract.symbol == symbol and p.position:
                c = Stock(symbol, "SMART", "USD")
                await ib.qualifyContractsAsync(c)
                ib.placeOrder(c, self._close_order(p.position, p.marketPrice))

    async def flatten_all(self, reason: str) -> dict:
        """Kill switch / force-flat: cancel every working order (all clients), then close every position.
        Reports what is still open after IBKR has had time to confirm."""
        ib = self.broker.ib
        log.warning("FLATTEN ALL (%s)", reason)
        ib.reqGlobalCancel()
        for _ in range(12):  # wait for cancels so they can't hit the closing orders
            if not ib.openTrades():
                break
            await asyncio.sleep(0.25)
        # Fresh positions from IBKR, not the cached copy: an exit may have just filled, and closing a stale
        # quantity would flip the position (sell shares we no longer hold = go short).
        last = {p.contract.conId: p.marketPrice for p in ib.portfolio(self.broker.account)}
        for p in await ib.reqPositionsAsync():
            if p.position and p.account == self.broker.account:
                c = Stock(p.contract.symbol, "SMART", "USD")
                await ib.qualifyContractsAsync(c)
                ib.placeOrder(c, self._close_order(p.position, last.get(p.contract.conId)))
        for _ in range(60):
            if not any(p.position for p in ib.positions()):
                break
            await asyncio.sleep(0.25)
        left = [f"{p.contract.symbol} {p.position:g}" for p in ib.positions() if p.position]
        working = [f"{t.contract.symbol} {t.order.action} {t.order.totalQuantity:g} {t.order.orderType}"
                   for t in ib.openTrades() if t.order.orderRef != "TA-flatten"]
        msg = f"{reason}: " + ("flat, no working orders" if not left and not working
                               else f"still open: {', '.join(left + working)}")
        publish("alert", {"level": "info" if not left else "error", "message": msg})
        return {"positions_left": left, "orders_left": working}
