"""Read-only account view: summary, P/L, positions (with stop/target/R from working orders), orders, fills."""
import asyncio
import math

from .hub import publish

SUMMARY_TAGS = {"NetLiquidation": "net_liq", "TotalCashValue": "cash", "BuyingPower": "buying_power"}


def num(x):
    """IBKR uses NaN and UNSET_DOUBLE (~1.8e308) for 'no value'. JSON can't carry NaN."""
    return None if x is None or math.isnan(x) or abs(x) > 1e300 else x


def current_r(qty: float, avg: float, last: float | None, stop: float | None) -> float | None:
    """Open profit in R, where 1R = distance from entry to the working stop."""
    if not (qty and last and stop) or avg == stop:
        return None
    return round((last - avg) / abs(avg - stop) * (1 if qty > 0 else -1), 2)


class Portfolio:
    def __init__(self, broker):
        self.broker = broker
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
        positions = []
        for p in ib.portfolio(acct):
            if not p.position:
                continue
            exits = [t.order for t in trades if t.contract.conId == p.contract.conId
                     and (t.order.action == "SELL") == (p.position > 0)]
            stop = next((num(o.auxPrice) for o in exits if o.orderType in ("STP", "STP LMT", "TRAIL")), None)
            target = next((num(o.lmtPrice) for o in exits if o.orderType == "LMT"), None)
            last = num(p.marketPrice)
            positions.append({
                "symbol": p.contract.symbol, "qty": p.position, "avg": num(p.averageCost), "last": last,
                "value": num(p.marketValue), "unrealized": num(p.unrealizedPNL), "realized": num(p.realizedPNL),
                "stop": stop, "target": target, "r": current_r(p.position, p.averageCost, last, stop),
            })

        orders = [{
            "id": t.order.orderId, "perm_id": t.order.permId, "symbol": t.contract.symbol,
            "action": t.order.action, "type": t.order.orderType, "qty": t.order.totalQuantity,
            "limit": num(t.order.lmtPrice), "stop": num(t.order.auxPrice), "tif": t.order.tif,
            "status": t.orderStatus.status, "filled": t.orderStatus.filled, "parent": t.order.parentId,
        } for t in trades]

        fills = [{
            "time": f.execution.time.isoformat(), "symbol": f.contract.symbol, "side": f.execution.side,
            "qty": f.execution.shares, "price": f.execution.price,
            "commission": num(f.commissionReport.commission) if f.commissionReport else None,
        } for f in sorted(ib.fills(), key=lambda f: f.execution.time, reverse=True)]

        publish("portfolio", {"summary": summary, "positions": positions, "orders": orders, "fills": fills})
