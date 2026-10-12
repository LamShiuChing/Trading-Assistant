"""Launch and log in to IB Gateway through IBC, and report login progress.

Password handling: IBC only accepts credentials from its config.ini or the command
line (visible in Task Manager). We write a temp ini that only this user can read.
IBC reads it once at startup and prints "End IBC Settings". As soon as we see that,
we rewrite the file without credentials. IBC's own restart loop reuses the same
path, and an auto-restart doesn't need the password.
"""
import asyncio
import logging
import os
import re
import subprocess
import threading
import time
from pathlib import Path

from .config import CFG, DATA
from .hub import publish
from .ib import port_open

log = logging.getLogger(__name__)
G = CFG["gateway"]
IBC = Path(G["ibc_path"])
START_IBC = IBC / "scripts" / "StartIBC.bat"
GATEWAY_JARS = Path(G["tws_path"]) / "ibgateway" / G["major_version"] / "jars"
INI = DATA / "run" / "ibc.ini"
IBC_LOG = DATA / "logs" / "ibc.log"
E_2FA_TIMEOUT = 1111  # StartIBC.bat: E_2FA_DIALOG_TIMED_OUT

LOGIN_TIMEOUT = 240  # s; longer than IBKR's ~3 min 2FA window. Catches dialogs we don't recognise.

# (substring in IBC output, login step or error code). Checked in order, after the settings block.
PROGRESS = [
    ("Existing session detected", "error:existing_session"),
    ("Too many failed login attempts", "error:too_many_attempts"),
    ("Unrecognized Username or Password", "error:bad_login"),  # Gateway 10.45 dialog title
    ("Login Failed", "error:bad_login"),
    ("Login Error dialog encountered", "error:bad_login"),
    ("SECOND_FACTOR_AUTH_LOGIN_TIMED_OUT", "error:2fa_timeout"),
    ("Second Factor Authentication", "waiting_2fa"),
    ("Login dialog WINDOW_OPENED", "logging_in"),
]
ERRORS = {
    "existing_session": "This IBKR user is already logged in somewhere else (TWS, mobile or web).",
    "too_many_attempts": "Too many failed logins. IBKR has locked logins for a few minutes.",
    "bad_login": "Login failed. Check the username and password.",
    "2fa_timeout": "2FA wasn't approved in time. Approve the IBKR Mobile alert faster and try again.",
    "exited": "Gateway has exited. Log in again. Details are in ibc.log.",
    "login_timeout": "Login didn't finish within 4 minutes.",
}
DIALOG = re.compile(r"detected dialog entitled: (.+?); event=Opened")


def classify(line: str) -> str | None:
    """Map one IBC log line (after the settings block) to a login step, 'error:<code>', or None."""
    return next((step for needle, step in PROGRESS if needle in line), None)

_proc: subprocess.Popen | None = None


def check_setup() -> dict:
    return {
        "ibc": START_IBC.exists(),
        "gateway": GATEWAY_JARS.exists(),
        "ibc_path": str(IBC),
        "gateway_path": str(GATEWAY_JARS.parent),
    }


def running() -> bool:
    return _proc is not None and _proc.poll() is None


def _ini(username: str, password: str, mode: str, takeover: bool) -> str:
    lines = {
        "IbLoginId": username,
        "IbPassword": password,
        "TradingMode": mode,
        # 'secondary': if a session exists elsewhere, ours gives up and we show an error.
        # The UI offers "take over", which relaunches with 'primaryoverride'.
        "ExistingSessionDetectedAction": "primaryoverride" if takeover else "secondary",
        "AcceptIncomingConnectionAction": "reject",  # 127.0.0.1 is trusted by default
        "ReloginAfterSecondFactorAuthenticationTimeout": "no",  # the UI reports the timeout instead
        "ReadOnlyApi": "no",
        "MinimizeMainWindow": "yes",
        "CommandServerPort": G["command_port"],
        "BindAddress": "127.0.0.1",
    }
    return "".join(f"{k}={v}\n" for k, v in lines.items())


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    # Strip inherited ACLs and grant only the current user. Do this before any secret is written.
    subprocess.run(
        ["icacls", str(path), "/inheritance:r", "/grant:r", f"{os.environ['USERNAME']}:F"],
        check=True, capture_output=True,
    )
    path.write_text(text, encoding="utf-8")


def _scrub() -> None:
    """Remove credentials from the ini and keep the other settings, so IBC restarts still work."""
    if INI.exists():
        keep = [l for l in INI.read_text(encoding="utf-8").splitlines(True)
                if not l.startswith(("IbLoginId=", "IbPassword="))]
        INI.write_text("".join(keep), encoding="utf-8")


def launch(username: str, password: str, mode: str, takeover: bool = False) -> None:
    global _proc, _last_step, _last_dialog
    if running():
        raise RuntimeError("Gateway is already starting or running")
    _kill_stale()
    _last_step = _last_dialog = None
    _write_private(INI, _ini(username, password, mode, takeover))
    loop = asyncio.get_running_loop()
    loop.call_later(30, _scrub)  # hard backstop in case IBC never prints its settings block
    publish("login", {"step": "launching"})
    try:
        IBC_LOG.parent.mkdir(parents=True, exist_ok=True)
        start_pos = IBC_LOG.stat().st_size if IBC_LOG.exists() else 0
        # Output goes to a file, not a pipe, so Gateway survives a backend restart.
        with open(IBC_LOG, "ab") as out:
            _proc = subprocess.Popen(
                ["cmd", "/c", str(START_IBC), G["major_version"], "/G",
                 f"/TwsPath:{G['tws_path']}", f"/IbcPath:{IBC}", f"/Config:{INI}",
                 f"/Mode:{mode}", "/On2FATimeout:exit"],
                stdout=out, stderr=subprocess.STDOUT, cwd=str(IBC),
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
    except Exception:
        _scrub()
        raise
    threading.Thread(target=_follow, args=(_proc, start_pos, loop), daemon=True).start()
    loop.create_task(_watchdog(_proc, CFG["gateway"][f"{mode}_port"]))


async def _watchdog(proc: subprocess.Popen, port: int) -> None:
    """Fail the login if the API port isn't up in time (unrecognised dialog, hang, ...)."""
    deadline = time.monotonic() + LOGIN_TIMEOUT
    while proc.poll() is None:
        if await port_open(port):
            return
        if time.monotonic() > deadline:
            _on_step(proc, "error:login_timeout")
            return
        await asyncio.sleep(2)


def _kill_stale() -> None:
    """Kill an IBC/Gateway left over from an earlier login attempt (e.g. before a backend restart).
    Only called when no API port is up, so it never touches a logged-in Gateway."""
    subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $PID -and "
         "$_.CommandLine -match 'ibcalpha\\.ibc\\.IbcGateway|StartIBC\\.bat' } | "
         "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"],
        capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW,
    )


_last_dialog: str | None = None


def _follow(proc: subprocess.Popen, pos: int, loop: asyncio.AbstractEventLoop) -> None:
    """Tail ibc.log from where this launch started, and map lines to login steps."""
    global _last_dialog
    settings_done = False
    with open(IBC_LOG, "r", encoding="utf-8", errors="replace") as f:
        f.seek(pos)
        while True:
            line = f.readline()
            if not line:
                if proc.poll() is not None:
                    break
                time.sleep(0.2)
                continue
            if not settings_done:
                if "End IBC Settings" in line:
                    settings_done = True
                    loop.call_soon_threadsafe(_scrub)
                continue
            if m := DIALOG.search(line):
                _last_dialog = m.group(1)
            if step := classify(line):
                loop.call_soon_threadsafe(_on_step, proc, step)
    loop.call_soon_threadsafe(_on_exit, proc.returncode)


_last_step = None


def _on_step(proc: subprocess.Popen, step: str) -> None:
    global _last_step
    if step == _last_step:
        return
    _last_step = step
    if step.startswith("error:"):
        code = step[6:]
        message = ERRORS[code]
        if code == "login_timeout" and _last_dialog:
            message += f' Gateway was showing: "{_last_dialog}".'
        publish("login", {"step": "error", "code": code, "message": message})
        _kill(proc)  # IBC would otherwise cold-restart into a login dialog with no credentials
    else:
        publish("login", {"step": step})


def _on_exit(rc: int | None) -> None:
    global _last_step
    _scrub()
    log.info("IBC exited rc=%s", rc)
    if _last_step and _last_step.startswith("error:"):
        pass  # already reported
    elif rc == E_2FA_TIMEOUT:
        publish("login", {"step": "error", "code": "2fa_timeout", "message": ERRORS["2fa_timeout"]})
    else:
        publish("login", {"step": "error", "code": "exited", "message": ERRORS["exited"]})
    _last_step = None


def _kill(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)


async def stop() -> bool:
    """Ask IBC to shut Gateway down tidily through its command server."""
    try:
        r, w = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", G["command_port"]), 3)
        w.write(b"STOP\n")
        await w.drain()
        w.close()
        return True
    except Exception as e:
        log.warning("IBC STOP failed: %s", e)
        return False
