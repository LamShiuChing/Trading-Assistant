"""IBKR API connection: connect, reconcile on every (re)connect, and publish the state.

States: disconnected -> connecting -> reconciling -> ready. A "degraded" state means
Gateway is up but has lost IB's servers (code 1100). New entries are allowed only in "ready".
Request pacing: ib_async throttles itself (Client.MaxRequests=45 per 1 s), under IBKR's 50/s.
"""
import asyncio
import logging
import time
from collections import deque

from ib_async import IB

from .config import CFG
from .hub import publish

log = logging.getLogger(__name__)
G = CFG["gateway"]
PORTS = {"paper": G["paper_port"], "live": G["live_port"]}


class HistLimiter:
    """Sliding-window cap on historical-data requests. IBKR pacing rules are reported as ~60 requests per
    10 minutes (third-party sources; IBKR's page wasn't reachable), so stay well under that."""

    def __init__(self, max_requests: int, window_s: float):
        self.max, self.window, self.times = max_requests, window_s, deque()

    async def acquire(self) -> None:
        while True:
            now = time.monotonic()
            while self.times and now - self.times[0] > self.window:
                self.times.popleft()
            if len(self.times) < self.max:
                self.times.append(now)
                return
            await asyncio.sleep(self.window - (now - self.times[0]) + 0.5)


# The screener gets its own budget so it can never starve chart loads (which are rare, user-driven).
screener_hist = HistLimiter(40, 600)


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
        self.on_ready: list = []  # async callbacks run after every (re)connect + reconcile
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
        # 2100-2199 are informational ("market data farm connection is OK" etc.).
        log.log(logging.INFO if 2100 <= code < 2200 else logging.WARNING,
                "IB %s reqId=%s %s %s", code, reqId, msg, getattr(contract, "symbol", ""))
        if code == 1100:
            self._set("degraded")
        elif code == 1101 and self.ib.isConnected():
            self._set("ready")
            self._run_ready()  # market data subscriptions were lost: resubscribe
        elif code == 1102 and self.ib.isConnected():
            self._set("ready")

    def _run_ready(self) -> None:
        for cb in self.on_ready:
            asyncio.get_running_loop().create_task(cb())

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
            # Paper account IDs start with "D" (DU…). Confirmed on a real paper login 2026-10-12.
            self.mismatch = (mode == "paper") != self.account.startswith("D")
            self._set("ready")
            self._run_ready()
            return

    def can_trade(self) -> bool:
        return self.state == "ready" and not self.mismatch

    def is_flat(self) -> bool | None:
        """True or False if confirmed by IBKR; None if we can't know because we're not connected."""
        if self.state not in ("ready", "degraded"):
            return None
        return not any(p.position for p in self.ib.positions()) and not self.ib.openTrades()
