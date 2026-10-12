"""Read-only account view: summary, P/L, positions (with stop/target/R from working orders), orders, fills."""
import asyncio
import math

from .hub import publish

SUMMARY_TAGS = {"NetLiquidation": "net_liq", "TotalCashValue": "cash", "BuyingPower": "buying_power"}


def num(x):
    """IBKR uses NaN and UNSET_DOUBLE (~1.8e308) for 'no value'. JSON can't carry NaN."""
    return None if x is None or math.isnan(x) or abs(x) > 1e300 else x


def stop_price(o) -> float | None:
    """Current stop level of an exit order. For TRAIL, auxPrice is the trailing amount, not a price."""
    if o.orderType == "TRAIL":
        return num(o.trailStopPrice)
    return num(o.auxPrice) if o.orderType in ("STP", "STP LMT") else None


def fill_avg(fills: list[tuple[str, float, float]], position: float) -> float | None:
    """Average fill price (no commissions) of the current position, rebuilt from fills in time order.
    fills: (side 'BOT'/'SLD', shares, price). Adding to a position re-averages; reducing it keeps the average;
    crossing zero starts a new average at that fill. Returns None if the fills don't add up to the position
    (part of it was opened before these fills, e.g. on an earlier day): then IBKR's average cost is used."""
    qty, avg = 0.0, 0.0
    for side, shares, price in fills:
        d = shares if side == "BOT" else -shares
        if qty == 0 or (qty > 0) == (d > 0):            # open or add
            avg = (avg * abs(qty) + price * abs(d)) / (abs(qty) + abs(d))
            qty += d
        elif abs(d) <= abs(qty):                          # reduce (average unchanged)
            qty += d
            if qty == 0:
                avg = 0.0
        else:                                             # flip through zero
            qty += d
            avg = price
    return round(avg, 4) if qty and abs(qty - position) < 1e-9 else None


def current_r(qty: float, avg: float, last: float | None, stop: float | None) -> float | None:
    """Open profit in R, where 1R = distance from entry to the working stop."""
    if not (qty and last and stop) or avg == stop:
        return None
    return round((last - avg) / abs(avg - stop) * (1 if qty > 0 else -1), 2)


class Portfolio:
    def __init__(self, broker):
        self.broker = broker
        self.last: dict | None = None
        self._pending = False
        ib = broker.ib
        for ev in (ib.accountValueEvent, ib.pnlEvent, ib.updatePortfolioEvent, ib.positionEvent,
                   ib.openOrderEvent, ib.orderStatusEvent, ib.execDetailsEvent, ib.commissionReportEvent):
            ev += self._dirty
        broker.on_ready.append(self._on_ready)

    async def _on_ready(self) -> None:
        self.broker.ib.reqPnL(self.broker.account)
        self._dirty()

    def _dirty(self, *_):
        # Coalesce bursts of IBKR events into at most ~3 pushes per second.
        if not self._pending:
            self._pending = True
            asyncio.get_running_loop().call_later(0.3, self._publish)

    def _publish(self) -> None:
        self._pending = False
        ib, acct = self.broker.ib, self.broker.account
        if not acct:
            return
        summary = {SUMMARY_TAGS[v.tag]: num(float(v.value)) for v in ib.accountValues(acct)
                   if v.tag in SUMMARY_TAGS and v.currency not in ("", "BASE")}
        summary["currency"] = next((v.currency for v in ib.accountValues(acct) if v.tag == "NetLiquidation"), "")
        pnl = next(iter(ib.pnl(acct)), None)
        if pnl:
            summary.update(day_pnl=num(pnl.dailyPnL), unrealized=num(pnl.unrealizedPnL), realized=num(pnl.realizedPnL))

        trades = ib.openTrades()
        by_symbol: dict[int, list] = {}
        for f in sorted(ib.fills(), key=lambda f: f.execution.time):
            if f.execution.acctNumber == acct:
                by_symbol.setdefault(f.contract.conId, []).append((f.execution.side, f.execution.shares, f.execution.price))
        positions = []
        for p in ib.portfolio(acct):
            if not p.position:
                continue
            exits = [t.order for t in trades if t.contract.conId == p.contract.conId
                     and (t.order.action == "SELL") == (p.position > 0)]
            stop = next((stop_price(o) for o in exits if stop_price(o) is not None), None)
            target = next((num(o.lmtPrice) for o in exits if o.orderType == "LMT"), None)
            last = num(p.marketPrice)
            # Fill-based average (no commission) when today's fills explain the whole position; else IBKR's
            # averageCost, which includes commissions.
            avg = fill_avg(by_symbol.get(p.contract.conId, []), p.position)
            avg_src = "fills" if avg is not None else "ibkr"
            avg = avg if avg is not None else num(p.averageCost)
            positions.append({
                "symbol": p.contract.symbol, "qty": p.position, "avg": avg, "avg_src": avg_src,
                "avg_cost": num(p.averageCost), "last": last,
                "value": num(p.marketValue), "unrealized": num(p.unrealizedPNL), "realized": num(p.realizedPNL),
                "stop": stop, "target": target, "r": current_r(p.position, avg, last, stop),
            })

        orders = [{
            "id": t.order.orderId, "perm_id": t.order.permId, "symbol": t.contract.symbol,
            "action": t.order.action, "type": t.order.orderType, "qty": t.order.totalQuantity,
            "limit": num(t.order.lmtPrice) if "LMT" in t.order.orderType else None,
            "stop": stop_price(t.order),
            "tif": t.order.tif, "ref": t.order.orderRef,
            "status": t.orderStatus.status, "filled": t.orderStatus.filled, "parent": t.order.parentId,
        } for t in trades]

        fills = [{
            "time": f.execution.time.isoformat(), "symbol": f.contract.symbol, "side": f.execution.side,
            "qty": f.execution.shares, "price": f.execution.price,
            "commission": num(f.commissionReport.commission) if f.commissionReport else None,
        } for f in sorted(ib.fills(), key=lambda f: f.execution.time, reverse=True)]

        self.last = {"summary": summary, "positions": positions, "orders": orders, "fills": fills}
        publish("portfolio", self.last)
