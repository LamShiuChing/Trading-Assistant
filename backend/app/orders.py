"""Order ticket: stage (preview with size, $ risk, R:R and every risk check) -> confirm -> bracket.

Rules: LONG ONLY (short selling is refused here, server-side). You buy and sell at MARKET:
  entry  = MKT buy (parent)
  stop   = STP sell (becomes a market sell when the stop price trades)
  target = LMT sell at the take-profit price
  backup = MKT sell after force-flat time (IBKR-side backup if this app is down)
  manual sells (Sell / Flatten buttons) = MKT, never more than you hold
The exits share one OCA group of type 2: a fill on one shrinks the others by the same amount
(a full fill cancels them). https://interactivebrokers.github.io/tws-api/oca.html

One stock = one position = one stop + one target. reconcile_exits keeps exactly one bracket's exits
(the newest) sized to the whole position: adding to a position merges into the new stop/target,
selling part of it elsewhere shrinks the exits, and a flat position cancels them. Exits never exceed
the position, so they can never open a short.
Every order carries orderRef "TA<journal id>" so fills can be matched to the journal.
"""
import asyncio
import logging
import math
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
PREVIEW_TTL = 120  # s; a staged ticket must be confirmed within this


class Orders:
    def __init__(self, broker, risk, chart, portfolio):
        self.broker, self.risk, self.chart, self.portfolio = broker, risk, chart, portfolio
        self.previews: dict[str, tuple[float, dict]] = {}
        risk.flatten_all = self.flatten_all
        ib = broker.ib
        ib.orderStatusEvent += self._on_status
        for ev in (ib.positionEvent, ib.execDetailsEvent, ib.orderStatusEvent):
            ev += self._schedule_reconcile
        self._reconcile_pending = False

    async def _last_price(self, contract) -> float:
        if self.chart.symbol == contract.symbol and self.chart.bars:
            return self.chart.bars[-1].close
        [t] = await self.broker.ib.reqTickersAsync(contract)
        px = t.marketPrice()
        if not px or math.isnan(px):
            raise ValueError(f"No live price for {contract.symbol}")
        return px

    def _held(self, con_id: int) -> tuple[float, float | None]:
        """(shares held, average price) for a symbol, from the portfolio snapshot."""
        for p in (self.portfolio.last or {}).get("positions", []):
            if p.get("conId") == con_id:
                return p["qty"], p["avg"]
        return 0.0, None

    # ---------- stage ----------

    async def preview(self, symbol: str, side: str, stop: float, tp_method: str, target_r: float | None,
                      target: float | None, qty_max: int | None = None, risk_usd: float | None = None) -> dict:
        if side != "BUY":
            raise ValueError("Short selling is disabled. This app only buys to open.")
        ib = self.broker.ib
        contract = Stock(symbol, "SMART", "USD")
        await ib.qualifyContractsAsync(contract)
        if not contract.conId:
            raise ValueError(f"Unknown symbol {symbol}")
        entry = round(await self._last_price(contract), 2)  # market order: estimated fill = last price
        stop = round(stop, 2)
        if stop >= entry:
            raise ValueError(f"Stop {stop:.2f} must be below the current price {entry:.2f}")

        on_chart = self.chart.symbol == symbol and self.chart.bars
        if target is not None:  # dragged on the chart
            method = "manual"
            if target <= entry:
                raise ValueError(f"Target {target:.2f} must be above the current price {entry:.2f}")
        else:
            method = tp_method
            if method in ("atr", "level") and not on_chart:
                raise ValueError("ATR and key-level targets need the symbol open on the chart")
            a = atr(self.chart.bars) if on_chart else None
            if method == "atr" and not a:
                raise ValueError("Not enough 5-min bars today for ATR(14)")
            levels = self.chart.levels if on_chart else {}
            target = target_price(method, True, entry, stop, target_r or R["default_target_r"], a, levels)

        risk_budget = risk_usd or R["risk_per_trade"]
        summary = (self.portfolio.last or {}).get("summary", {})
        auto = position_size(risk_budget, entry, stop, R["max_position_value"], summary.get("buying_power"))
        qty = min(auto, qty_max) if qty_max else auto
        per_share = entry - stop
        size_note = f"${risk_budget:,.0f} risk ÷ ${per_share:.2f} stop distance = {math.floor(risk_budget / per_share):,} sh"
        if auto < math.floor(risk_budget / per_share):
            size_note += f", capped to {auto:,} by max position ${R['max_position_value']:,} or buying power"
        if qty_max and qty_max < auto:
            size_note += f", you chose {qty:,}"

        held, held_avg = self._held(contract.conId)
        add = None
        if held > 0:
            total = held + qty
            new_avg = (held * (held_avg or entry) + qty * entry) / total
            add = {"held": held, "total": total, "new_avg": round(new_avg, 4),
                   "merged_risk": round((new_avg - stop) * total, 2),
                   "merged_reward": round((target - new_avg) * total, 2) if target else None}
        p = {
            "symbol": symbol, "conId": contract.conId, "side": "BUY", "qty": qty,
            "entry": entry, "stop": stop, "target": target, "tp_method": method,
            "risk": round(qty * per_share, 2), "reward": round(qty * (target - entry), 2) if target else None,
            "rr": round((target - entry) / per_share, 2) if target else None,
            "trail": method == "trail", "size_note": size_note, "add": add,
            "blockers": await self.risk.check_entry(contract, qty, entry, stop),
        }
        now = time.monotonic()
        self.previews = {k: v for k, v in self.previews.items() if now - v[0] < PREVIEW_TTL}
        pid = uuid.uuid4().hex[:12]
        self.previews[pid] = (now, p)
        return {"id": pid, **p}

    # ---------- confirm ----------

    async def confirm(self, pid: str) -> dict:
        created, p = self.previews.pop(pid, (0, None))
        if not p or time.monotonic() - created > PREVIEW_TTL:
            raise ValueError("This ticket expired. Stage it again.")
        if p["side"] != "BUY":
            raise ValueError("Short selling is disabled.")
        ib = self.broker.ib
        contract = Stock(p["symbol"], "SMART", "USD")
        await ib.qualifyContractsAsync(contract)
        # Re-check: P/L, positions, price or the clock may have changed since staging.
        entry_now = round(await self._last_price(contract), 2)
        if entry_now <= p["stop"]:
            raise ValueError(f"Price {entry_now:.2f} is now at or below the stop {p['stop']:.2f}. Stage again.")
        blockers = await self.risk.check_entry(contract, p["qty"], entry_now, p["stop"])
        if blockers:
            raise ValueError("Blocked: " + "; ".join(blockers))

        now = datetime.now(ET)
        cur = db.conn.execute(
            "INSERT INTO trades (day, created, symbol, side, qty, entry, stop, target, tp_method, risk, status)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,'submitted')",
            (now.date().isoformat(), db.now(), p["symbol"], "BUY", p["qty"], entry_now, p["stop"],
             p["target"], p["tp_method"], p["risk"]))
        ref = f"TA{cur.lastrowid}"
        r_dist = p["entry"] - p["stop"]

        parent = MarketOrder("BUY", p["qty"], orderId=ib.client.getReqId(), tif="DAY", orderRef=ref, transmit=False)
        # ocaType 2: remaining orders are proportionately reduced, with overfill protection ("block").
        exits_common = dict(parentId=parent.orderId, tif="DAY", orderRef=ref, ocaGroup=f"{ref}-exits", ocaType=2,
                            transmit=False)
        stop = Order(action="SELL", orderType="STP", totalQuantity=p["qty"], auxPrice=p["stop"],
                     orderId=ib.client.getReqId(), **exits_common)
        if p["trail"]:
            # IBKR adjusted order: at +1R the stop becomes a trailing stop, starting at breakeven, trailing by 1R.
            # https://interactivebrokers.github.io/tws-api/adjustable_stops.html
            stop.triggerPrice = round(p["entry"] + r_dist, 2)
            stop.adjustedOrderType = "TRAIL"
            stop.adjustedStopPrice = p["entry"]
            stop.adjustedTrailingAmount = round(r_dist, 2)
            stop.adjustableTrailingUnit = 0  # amount, not percent
        children = [stop]
        if p["target"] is not None:
            children.append(LimitOrder("SELL", p["qty"], p["target"], orderId=ib.client.getReqId(), **exits_common))
        sess = self.risk.session(now)
        if CFG["risk"]["server_side_flatten"] and sess:
            # Fires 2 min AFTER the app's own force-flat, which cancels it first, so both can never sell.
            backup_at = sess["flatten"] + timedelta(minutes=2)
            children.append(MarketOrder("SELL", p["qty"], orderId=ib.client.getReqId(),
                                        goodAfterTime=backup_at.strftime("%Y%m%d %H:%M:%S US/Eastern"), **exits_common))
        children[-1].transmit = True  # sends the whole group at once

        trades = [ib.placeOrder(contract, o) for o in (parent, *children)]
        db.conn.execute("UPDATE trades SET parent_id=? WHERE id=?", (parent.orderId, cur.lastrowid))
        for _ in range(20):  # report only what IBKR confirms
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

    # ---------- one stop + one target per position ----------

    def _schedule_reconcile(self, *_):
        if not self._reconcile_pending:
            self._reconcile_pending = True
            asyncio.get_running_loop().call_later(1.0, self.reconcile_exits)

    def reconcile_exits(self) -> None:
        self._reconcile_pending = False
        ib = self.broker.ib
        if self.broker.state != "ready":
            return
        trades = ib.openTrades()
        held = {p.contract.conId: p.position for p in ib.positions() if p.account == self.broker.account}
        for con_id in {t.contract.conId for t in trades}:
            for action, t, qty in plan_exits([t for t in trades if t.contract.conId == con_id], held.get(con_id, 0)):
                o = t.order
                if action == "cancel":
                    log.warning("exits %s: cancel %s %s", t.contract.symbol, o.orderType, o.orderRef)
                    ib.cancelOrder(o)
                else:
                    log.warning("exits %s: %s %s qty %g -> %g", t.contract.symbol, o.orderType, o.orderRef, o.totalQuantity, qty)
                    o.totalQuantity = qty
                    ib.placeOrder(t.contract, o)  # same orderId = modify

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

    async def sell(self, symbol: str, qty: float | None) -> float:
        """Market-sell part (qty) or all (None) of a long position. Never sells more than you hold.
        For a partial sale the stop/target are shrunk FIRST, so they can't exceed what's left even for a moment."""
        ib = self.broker.ib
        pos = sum(p.position for p in await ib.reqPositionsAsync()
                  if p.contract.symbol == symbol and p.account == self.broker.account)
        if pos <= 0:
            raise ValueError(f"No long position in {symbol} to sell")
        qty = pos if qty is None else min(qty, pos)
        if qty >= pos:
            await self.flatten_symbol(symbol)
            return pos
        trades = [t for t in ib.openTrades() if t.contract.symbol == symbol]
        for action, t, q in plan_exits(trades, pos - qty):
            if action == "cancel":
                ib.cancelOrder(t.order)
            else:
                t.order.totalQuantity = q
                ib.placeOrder(t.contract, t.order)
        await asyncio.sleep(0.5)
        c = Stock(symbol, "SMART", "USD")
        await ib.qualifyContractsAsync(c)
        last = next((p.marketPrice for p in ib.portfolio(self.broker.account) if p.contract.symbol == symbol), None)
        o = self._close_order(qty, last)
        o.orderRef = "TA-sell"
        ib.placeOrder(c, o)
        log.info("sell %s %g of %g", symbol, qty, pos)
        return qty

    async def flatten_symbol(self, symbol: str) -> None:
        ib = self.broker.ib
        for t in ib.openTrades():
            if t.contract.symbol == symbol:
                ib.cancelOrder(t.order)
        await asyncio.sleep(0.5)
        last = {p.contract.conId: p.marketPrice for p in ib.portfolio(self.broker.account)}
        for p in await ib.reqPositionsAsync():  # fresh quantity, so we never sell more than we hold
            if p.contract.symbol == symbol and p.position and p.account == self.broker.account:
                c = Stock(symbol, "SMART", "USD")
                await ib.qualifyContractsAsync(c)
                ib.placeOrder(c, self._close_order(p.position, last.get(p.contract.conId)))

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


def plan_exits(trades, position: float) -> list[tuple[str, object, float]]:
    """Pure, for ONE symbol. Returns actions [("cancel", trade, 0) | ("modify", trade, new_total_qty)] so that
    exactly one bracket's exits (the newest whose entry has filled) protect the whole long position:
      - older brackets' exits are cancelled (merge: the newest stop/target now covers everything),
      - the kept exits are resized to the position (shrink after a sale elsewhere, grow after an add),
      - no position (or a short) -> cancel all exits, since a sell would open or deepen a short.
    Brackets whose entry is still working are left alone; if an entry is part-filled, wait for it."""
    by_id = {t.order.orderId: t for t in trades}
    groups: dict[str, list] = {}
    for t in trades:
        o = t.order
        if not (o.orderRef.startswith("TA") and o.parentId and o.ocaGroup):
            continue
        parent = by_id.get(o.parentId)
        if parent is not None:  # entry still working
            if parent.orderStatus.filled > 0:
                return []        # part-filled entry: wait until it completes
            continue             # dormant exits of an unfilled entry
        groups.setdefault(o.ocaGroup, []).append(t)
    if not groups:
        return []
    order = sorted(groups, key=lambda g: int(g[2:].split("-")[0]))  # by journal number, oldest first
    actions = [("cancel", t, 0) for g in order[:-1] for t in groups[g]]
    keep = groups[order[-1]]
    if position <= 0:
        return actions + [("cancel", t, 0) for t in keep]
    for t in keep:
        want = t.orderStatus.filled + position
        if abs(t.order.totalQuantity - want) > 1e-9:
            actions.append(("modify", t, want))
    return actions
