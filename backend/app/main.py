import asyncio
import json
import logging
import signal
import subprocess
from contextlib import asynccontextmanager
from typing import Literal

import keyring
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, SecretStr

from . import gateway, hub
from .config import CFG, DATA, ROOT
from .ib import PORTS, Broker, port_open
from .market import ChartFeed
from .orders import Orders
from .portfolio import Portfolio
from .risk import RiskManager

logging.basicConfig(
    filename=DATA / "logs" / "app.log", level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logging.getLogger("ib_async").setLevel(logging.WARNING)  # INFO logs every position/portfolio update
KEYRING_SERVICE = "TradingAssistant"
SETTINGS = DATA / "settings.json"  # non-secret UI prefs (last username)
broker = Broker()
portfolio = Portfolio(broker)


@asynccontextmanager
async def lifespan(app: FastAPI):
    hub.publish("connection", broker.snapshot())
    tasks = [asyncio.create_task(broker.run()), asyncio.create_task(risk.run())]
    yield
    for t in tasks:
        t.cancel()
    broker.ib.disconnect()


app = FastAPI(lifespan=lifespan)

# Only our own UI may talk to us. The Host check blocks DNS rebinding. The Origin check blocks
# cross-site form posts and WebSocket hijacking from other sites open in the browser.
# A request with no Origin header (the launcher's PowerShell calls) is allowed.
_PORT = CFG["app"]["port"]
ALLOWED_HOSTS = {f"127.0.0.1:{_PORT}", f"localhost:{_PORT}"}
ALLOWED_ORIGINS = {f"http://{h}" for h in ALLOWED_HOSTS} | {"http://localhost:5173", "http://127.0.0.1:5173"}  # + Vite dev


def _is_local(headers) -> bool:
    origin = headers.get("origin")
    return headers.get("host") in ALLOWED_HOSTS and (origin is None or origin in ALLOWED_ORIGINS)


@app.middleware("http")
async def local_only(request, call_next):
    if not _is_local(request.headers):
        return JSONResponse({"detail": "Forbidden"}, status_code=403)
    return await call_next(request)


def _settings() -> dict:
    return json.loads(SETTINGS.read_text()) if SETTINGS.exists() else {}


def _save_setting(**kw) -> None:
    SETTINGS.write_text(json.dumps({**_settings(), **kw}))


chart = ChartFeed(broker, _settings().get("symbol", "SPY"))
risk = RiskManager(broker, portfolio)
orders = Orders(broker, risk, chart, portfolio)


class TicketReq(BaseModel):
    symbol: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9.\- ]{0,11}$")
    side: Literal["BUY", "SELL"]
    entry: float = Field(gt=0)
    stop: float = Field(gt=0)
    tp_method: Literal["r", "atr", "level", "trail"] = "r"
    target_r: float | None = Field(default=None, gt=0, le=20)
    target: float | None = Field(default=None, gt=0)  # set when the target line was dragged on the chart
    qty: int | None = Field(default=None, gt=0)  # optional smaller size; never more than the risk-based size


def _need_connection():
    if not broker.can_trade():
        raise HTTPException(409, "Not connected to IBKR (or the account doesn't match the mode).")


@app.post("/api/ticket/preview")
async def ticket_preview(req: TicketReq):
    """Stage: size, $ risk, R:R and every risk check. Nothing is sent to IBKR."""
    _need_connection()
    try:
        return await orders.preview(req.symbol.upper(), req.side, req.entry, req.stop, req.tp_method, req.target_r, req.target, req.qty)
    except ValueError as e:
        raise HTTPException(400, str(e))


class ConfirmReq(BaseModel):
    id: str


@app.post("/api/ticket/confirm")
async def ticket_confirm(req: ConfirmReq):
    """Confirm a staged ticket: re-checks the rules, then sends the bracket."""
    _need_connection()
    try:
        return await orders.confirm(req.id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/orders/{order_id}/cancel")
async def cancel_order(order_id: int):
    _need_connection()
    try:
        orders.cancel(order_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {"ok": True}


class FlattenReq(BaseModel):
    symbol: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9.\- ]{0,11}$")


@app.post("/api/flatten")
async def flatten(req: FlattenReq):
    _need_connection()
    await orders.flatten_symbol(req.symbol.upper())
    return {"ok": True}


@app.post("/api/kill")
async def kill_switch():
    """Cancel every working order and close every position. Works even if the account/mode check failed."""
    if broker.state not in ("ready", "degraded"):
        raise HTTPException(409, "Not connected to IBKR: can't cancel or flatten. Use TWS or IBKR Mobile.")
    return await orders.flatten_all("Kill switch")


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/setup")
def setup():
    username = _settings().get("username", "")
    saved = bool(username) and keyring.get_password(KEYRING_SERVICE, username) is not None
    return {**gateway.check_setup(), "allow_live": CFG["app"]["allow_live"],
            "username": username, "saved_password": saved}


class Login(BaseModel):
    username: str
    password: SecretStr | None = None  # None means: use the password saved in the OS keychain
    mode: Literal["paper", "live"] = "paper"
    remember: bool = False
    takeover: bool = False


@app.post("/api/login")
async def login(req: Login):
    if req.mode == "live" and not CFG["app"]["allow_live"]:
        raise HTTPException(403, "LIVE is disabled. Set allow_live = true in config.toml first.")
    if broker.state != "disconnected" or gateway.running() or any([await port_open(p) for p in PORTS.values()]):
        raise HTTPException(409, "A Gateway is already running or starting.")
    pw = req.password.get_secret_value() if req.password else keyring.get_password(KEYRING_SERVICE, req.username)
    if not pw:
        raise HTTPException(400, "Password required.")
    if req.remember:
        keyring.set_password(KEYRING_SERVICE, req.username, pw)
    _save_setting(username=req.username)
    gateway.launch(req.username, pw, req.mode, req.takeover)
    return {"ok": True}


@app.post("/api/forget-password")
def forget_password():
    username = _settings().get("username")
    if username and keyring.get_password(KEYRING_SERVICE, username) is not None:
        keyring.delete_password(KEYRING_SERVICE, username)
    return {"ok": True}


class ChartReq(BaseModel):
    symbol: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9.\- ]{0,11}$")


@app.post("/api/chart")
async def set_chart(req: ChartReq):
    if broker.state not in ("ready", "degraded"):
        raise HTTPException(409, "Not connected to IBKR.")
    symbol = req.symbol.upper().strip()
    _save_setting(symbol=symbol)
    await chart.set_symbol(symbol)
    return {"ok": True}


@app.get("/api/symbols")
async def symbols(q: str):
    """US stock matches for the symbol search box."""
    if broker.state not in ("ready", "degraded") or not q.strip():
        return []
    found = await broker.ib.reqMatchingSymbolsAsync(q.strip()) or []
    return [{"symbol": d.contract.symbol, "exchange": d.contract.primaryExchange,
             "name": getattr(d.contract, "description", "")}
            for d in found if d.contract and d.contract.secType == "STK" and d.contract.currency == "USD"]


async def _quit_blockers() -> list[str]:
    flat = broker.is_flat()
    if flat is None:
        if any([await port_open(p) for p in PORTS.values()]):
            return ["Gateway is up but not connected, so positions and orders can't be confirmed."]
        return []
    if flat:
        return []
    return [f"{p.contract.symbol}: {p.position:g} shares" for p in broker.ib.positions() if p.position] + \
           [f"Working order: {t.contract.symbol} {t.order.action} {t.order.totalQuantity:g}" for t in broker.ib.openTrades()]


@app.post("/api/quit")
async def quit_app():
    """Shut down backend + Gateway, but only when flat with no working orders (confirmed by IBKR)."""
    blockers = await _quit_blockers()
    if blockers:
        raise HTTPException(409, {"blockers": blockers})
    await gateway.stop()
    asyncio.get_running_loop().call_later(0.5, signal.raise_signal, signal.SIGINT)
    return {"ok": True}


@app.post("/api/restart")
async def restart():
    """Restart the backend (and rebuild the UI if its source changed). Gateway keeps running, so no re-login.
    A detached helper waits for this process to free the port, then starts a fresh one. `uv run` puts us in a
    Windows job object that kills all children when it exits, so the helper must break away from the job."""
    subprocess.Popen(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
         "-File", str(ROOT / "launcher" / "start-backend.ps1")],
        creationflags=subprocess.CREATE_BREAKAWAY_FROM_JOB | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW,
        close_fds=True,
    )
    asyncio.get_running_loop().call_later(0.3, signal.raise_signal, signal.SIGINT)
    return {"ok": True}


@app.post("/api/window-closed")
async def window_closed():
    """Called by the launcher when the app window closes. Stays running (to enforce force-flat) if not flat."""
    try:
        return await quit_app()
    except HTTPException:
        return {"ok": False, "staying": True}


@app.websocket("/ws")
async def ws(sock: WebSocket):
    if not _is_local(sock.headers):  # HTTP middleware doesn't run for WebSockets
        await sock.close(code=1008)
        return
    await sock.accept()
    hub.clients.add(sock)
    for t, d in list(hub.latest.items()):
        await sock.send_text(json.dumps({"type": t, "data": d}, default=str))
    try:
        while True:
            await sock.receive_text()
    except WebSocketDisconnect:
        hub.clients.discard(sock)


DIST = ROOT / "frontend" / "dist"
if DIST.exists():
    app.mount("/", StaticFiles(directory=DIST, html=True), name="ui")
