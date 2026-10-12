"""Push state to every connected UI over WebSocket. Latest value per type is kept for new clients."""
import asyncio
import json

clients: set = set()
latest: dict[str, object] = {}


def publish(type_: str, data) -> None:
    latest[type_] = data
    msg = json.dumps({"type": type_, "data": data}, default=str)
    for ws in list(clients):
        asyncio.get_running_loop().create_task(_send(ws, msg))


async def _send(ws, msg: str) -> None:
    try:
        await ws.send_text(msg)
    except Exception:
        clients.discard(ws)
