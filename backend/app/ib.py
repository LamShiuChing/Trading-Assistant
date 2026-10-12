"""IBKR API connection: connect, reconcile on every (re)connect, and publish the state.

States: disconnected -> connecting -> reconciling -> ready. A "degraded" state means
Gateway is up but has lost IB's servers (code 1100). New entries are allowed only in "ready".
Request pacing: ib_async throttles itself (Client.MaxRequests=45 per 1 s), under IBKR's 50/s.
"""
import asyncio
import logging

from ib_async import IB

from .config import CFG
from .hub import publish

log = logging.getLogger(__name__)
G = CFG["gateway"]
PORTS = {"paper": G["paper_port"], "live": G["live_port"]}


async def port_open(port: int) -> bool:
    try:
        _, w = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), 0.5)
        w.close()
        return True
    except (OSError, asyncio.TimeoutError):
        return False


class Broker:
    def __init__(self):
        self.ib = IB()
        self.state = "disconnected"
        self.mode: str | None = None
        self.account: str | None = None
        self.mismatch = False
        self.blocked_live = False
        self.ib.disconnectedEvent += lambda: self._set("disconnected")
        self.ib.errorEvent += self._on_error

    def snapshot(self) -> dict:
        return {"state": self.state, "mode": self.mode, "account": self.account,
                "mismatch": self.mismatch, "blocked_live": self.blocked_live}

    def _set(self, state: str) -> None:
        if state != self.state:
            log.info("connection %s -> %s", self.state, state)
        self.state = state
        publish("connection", self.snapshot())

    def _on_error(self, reqId, code, msg, contract) -> None:
        # https://www.interactivebrokers.com/docs/tws-api/doc/error-handling/system-message-codes
        if code == 1100:
            self._set("degraded")
        elif code in (1101, 1102) and self.ib.isConnected():
            self._set("ready")  # phase 1b: on 1101 re-subscribe market data

    async def run(self) -> None:
        while True:
            if not self.ib.isConnected():
                try:
                    await self._connect()
                except Exception as e:
                    log.warning("connect failed: %r", e)
                    self.ib.disconnect()
                    self._set("disconnected")
            await asyncio.sleep(2)

    async def _connect(self) -> None:
        allow_live = CFG["app"]["allow_live"]
        live_up = await port_open(PORTS["live"])
        if self.blocked_live != (live_up and not allow_live):
            self.blocked_live = live_up and not allow_live
            publish("connection", self.snapshot())
        for mode in ("paper", "live") if allow_live else ("paper",):
            if not (live_up if mode == "live" else await port_open(PORTS[mode])):
                continue
            self._set("connecting")
            # connectAsync also loads positions, open orders, executions and account updates.
            await self.ib.connectAsync("127.0.0.1", PORTS[mode], clientId=G["client_id"], timeout=10)
            self.mode = mode
            self._set("reconciling")
            await self.ib.reqAllOpenOrdersAsync()  # include orders placed from TWS/mobile/other clients
            self.account = self.ib.managedAccounts()[0]
            # Paper account IDs start with "D" (e.g. DU1234567). Unverified, so check on first paper login.
            self.mismatch = (mode == "paper") != self.account.startswith("D")
            self._set("ready")
            return

    def can_trade(self) -> bool:
        return self.state == "ready" and not self.mismatch

    def is_flat(self) -> bool | None:
        """True or False if confirmed by IBKR; None if we can't know because we're not connected."""
        if self.state not in ("ready", "degraded"):
            return None
        return not any(p.position for p in self.ib.positions()) and not self.ib.openTrades()
