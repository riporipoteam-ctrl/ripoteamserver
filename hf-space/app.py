from __future__ import annotations

import spaces
import asyncio
import json
import os
import secrets
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from desktop_http import install_desktop_routes
from gradio import Server
import psutil
from fastapi import Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

DISPLAY = os.environ.get("DISPLAY", ":99")
HOME = Path.home()
DATA_DIR = Path(os.environ.get("RIPO_DATA_DIR", str(HOME / ".ripo-cloud-pc")))
LOG_DIR = DATA_DIR / "logs"
VNC_PORT = int(os.environ.get("VNC_PORT", "5900"))
VNC_PASSWORD = os.environ.get("VNC_PASSWORD", "ripo-change-me")
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")

DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

PROCESSES: dict[str, subprocess.Popen[Any]] = {}
HERMES_PROCESS: subprocess.Popen[Any] | None = None
HERMES_INSTALL_THREAD: threading.Thread | None = None

HERMES_INSTALL_STATE: dict[str, Any] = {
    "running": False,
    "last_result": None,
    "started_at": None,
    "finished_at": None,
}


def environment() -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "DISPLAY": DISPLAY,
            "HOME": str(HOME),
            "USER": env.get("USER", HOME.name),
            "PATH": f"{HOME / '.local/bin'}:{env.get('PATH', '')}",
        }
    )
    return env


def log_path(name: str) -> Path:
    return LOG_DIR / f"{name}.log"


def spawn(name: str, command: list[str]) -> subprocess.Popen[Any]:
    existing = PROCESSES.get(name)
    if existing and existing.poll() is None:
        return existing

    output = log_path(name).open("ab", buffering=0)
    process = subprocess.Popen(
        command,
        stdout=output,
        stderr=subprocess.STDOUT,
        env=environment(),
        start_new_session=True,
    )
    PROCESSES[name] = process
    return process


def wait_for_display(timeout: float = 20.0) -> None:
    display_number = DISPLAY.removeprefix(":").split(".", maxsplit=1)[0]
    socket_path = Path(f"/tmp/.X11-unix/X{display_number}")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if socket_path.exists():
            return
        time.sleep(0.25)
    raise RuntimeError(f"X display {DISPLAY} did not become ready")


def start_desktop() -> None:
    pass_file = DATA_DIR / "vnc.pass"
    subprocess.run(
        ["x11vnc", "-storepasswd", VNC_PASSWORD, str(pass_file)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=environment(),
    )

    spawn(
        "xvfb",
        [
            "Xvfb",
            DISPLAY,
            "-screen",
            "0",
            "1366x768x24",
            "-ac",
            "+extension",
            "GLX",
            "+render",
            "-noreset",
        ],
    )
    wait_for_display()
    spawn("openbox", ["dbus-launch", "--exit-with-session", "openbox-session"])
    spawn("pcmanfm", ["pcmanfm", "--desktop", "--profile", "LXDE"])
    spawn("lxpanel", ["lxpanel", "--profile", "LXDE"])
    spawn(
        "xterm",
        [
            "xterm",
            "-geometry",
            "112x34+24+24",
            "-fa",
            "DejaVu Sans Mono",
            "-fs",
            "11",
            "-title",
            "Ripo Team Terminal",
        ],
    )
    spawn(
        "x11vnc",
        [
            "x11vnc",
            "-display",
            DISPLAY,
            "-rfbport",
            str(VNC_PORT),
            "-rfbauth",
            str(pass_file),
            "-forever",
            "-shared",
            "-noxdamage",
            "-repeat",
            "-listen",
            "127.0.0.1",
        ],
    )


def hermes_binary() -> Path | None:
    candidates = [
        HOME / ".local/bin/hermes",
        HOME / ".hermes/hermes-agent/venv/bin/hermes",
        HOME / ".hermes/hermes-agent/.venv/bin/hermes",
    ]
    discovered = shutil.which("hermes", path=environment()["PATH"])
    if discovered:
        candidates.insert(0, Path(discovered))
    return next(
        (path for path in candidates if path.exists() and os.access(path, os.X_OK)),
        None,
    )


def install_hermes_worker() -> None:
    HERMES_INSTALL_STATE.update(
        running=True,
        last_result=None,
        started_at=time.time(),
        finished_at=None,
    )
    command = (
        "curl -fsSL https://hermes-agent.nousresearch.com/install.sh "
        "| bash -s -- --skip-browser --skip-setup"
    )
    try:
        with log_path("hermes-install").open("ab", buffering=0) as output:
            completed = subprocess.run(
                ["bash", "-lc", command],
                stdout=output,
                stderr=subprocess.STDOUT,
                env=environment(),
                timeout=1800,
                check=False,
            )
        HERMES_INSTALL_STATE["last_result"] = {
            "ok": completed.returncode == 0,
            "returncode": completed.returncode,
        }
    except Exception as exc:
        HERMES_INSTALL_STATE["last_result"] = {"ok": False, "error": str(exc)}
    finally:
        HERMES_INSTALL_STATE["running"] = False
        HERMES_INSTALL_STATE["finished_at"] = time.time()


def install_hermes() -> dict[str, Any]:
    global HERMES_INSTALL_THREAD
    if HERMES_INSTALL_STATE["running"]:
        return {"ok": True, "message": "Hermes installation is already running."}
    if hermes_binary():
        return {"ok": True, "message": "Hermes is already installed."}
    HERMES_INSTALL_THREAD = threading.Thread(target=install_hermes_worker, daemon=True)
    HERMES_INSTALL_THREAD.start()
    return {"ok": True, "message": "Hermes installation started. Watch the install log."}


def start_hermes_gateway() -> dict[str, Any]:
    global HERMES_PROCESS
    binary = hermes_binary()
    if not binary:
        return {"ok": False, "message": "Hermes is not installed yet."}
    if HERMES_PROCESS and HERMES_PROCESS.poll() is None:
        return {"ok": True, "message": "Hermes gateway is already running."}
    output = log_path("hermes-gateway").open("ab", buffering=0)
    HERMES_PROCESS = subprocess.Popen(
        [str(binary), "gateway"],
        stdout=output,
        stderr=subprocess.STDOUT,
        env=environment(),
        start_new_session=True,
    )
    return {"ok": True, "message": "Hermes gateway started."}


def stop_hermes_gateway() -> dict[str, Any]:
    global HERMES_PROCESS
    if not HERMES_PROCESS or HERMES_PROCESS.poll() is not None:
        return {"ok": True, "message": "Hermes gateway is not running."}
    try:
        os.killpg(HERMES_PROCESS.pid, signal.SIGTERM)
        HERMES_PROCESS.wait(timeout=10)
    except Exception:
        try:
            os.killpg(HERMES_PROCESS.pid, signal.SIGKILL)
        except Exception:
            pass
    HERMES_PROCESS = None
    return {"ok": True, "message": "Hermes gateway stopped."}


def read_log(name: str, max_bytes: int = 20_000) -> str:
    path = log_path(name)
    if not path.exists():
        return "No log output yet."
    return path.read_bytes()[-max_bytes:].decode("utf-8", errors="replace")


def status() -> dict[str, Any]:
    desktop = {
        name: {
            "pid": process.pid,
            "running": process.poll() is None,
            "returncode": process.poll(),
        }
        for name, process in PROCESSES.items()
    }
    return {
        "ok": True,
        "name": "Ripo Team Cloud PC",
        "platform": "Hugging Face Gradio Space",
        "architecture": os.uname().machine,
        "cpu_count": psutil.cpu_count(),
        "memory_total": psutil.virtual_memory().total,
        "memory_available": psutil.virtual_memory().available,
        "disk_total": psutil.disk_usage("/").total,
        "disk_free": psutil.disk_usage("/").free,
        "desktop": desktop,
        "security": {
            "custom_vnc_password": VNC_PASSWORD != "ripo-change-me",
            "admin_token_configured": bool(ADMIN_TOKEN),
        },
        "hermes": {
            "installed": hermes_binary() is not None,
            "running": bool(HERMES_PROCESS and HERMES_PROCESS.poll() is None),
            "install": HERMES_INSTALL_STATE,
        },
        "timestamp": time.time(),
    }


def authorize(token: str | None) -> None:
    if not ADMIN_TOKEN:
        raise HTTPException(status_code=503, detail="ADMIN_TOKEN is not configured in Space secrets.")
    if token is None or not secrets.compare_digest(token, ADMIN_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid admin token.")


app = Server()


@app.api(name="zero_gpu_runtime_probe")
@spaces.GPU(duration=1)
def zero_gpu_runtime_probe() -> str:
    return "ZeroGPU runtime available"


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://riporipoteam-ctrl.github.io",
        "https://echoxr-ripoteam-cloud-pc.hf.space",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["content-type", "x-admin-token"],
)


install_desktop_routes(app, password=VNC_PASSWORD, display=DISPLAY)


@app.get("/api/health")
async def health() -> JSONResponse:
    return JSONResponse(status())


@app.get("/api/logs/{name}")
async def logs(name: str, x_admin_token: str | None = Header(default=None)) -> JSONResponse:
    authorize(x_admin_token)
    allowed = {
        "xvfb",
        "openbox",
        "pcmanfm",
        "lxpanel",
        "xterm",
        "x11vnc",
        "hermes-install",
        "hermes-gateway",
    }
    if name not in allowed:
        raise HTTPException(status_code=404, detail="Unknown log name.")
    return JSONResponse({"ok": True, "name": name, "content": read_log(name)})


@app.post("/api/hermes/install")
async def hermes_install(x_admin_token: str | None = Header(default=None)) -> JSONResponse:
    authorize(x_admin_token)
    return JSONResponse(install_hermes())


@app.post("/api/hermes/start")
async def hermes_start(x_admin_token: str | None = Header(default=None)) -> JSONResponse:
    authorize(x_admin_token)
    result = start_hermes_gateway()
    return JSONResponse(result, status_code=200 if result["ok"] else 409)


@app.post("/api/hermes/stop")
async def hermes_stop(x_admin_token: str | None = Header(default=None)) -> JSONResponse:
    authorize(x_admin_token)
    return JSONResponse(stop_hermes_gateway())


@app.websocket("/websockify")
async def websockify_proxy(websocket: WebSocket) -> None:
    requested_protocols = [
        item.strip()
        for item in websocket.headers.get("sec-websocket-protocol", "").split(",")
        if item.strip()
    ]
    selected_protocol = "binary" if "binary" in requested_protocols else None
    await websocket.accept(subprotocol=selected_protocol)

    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", VNC_PORT)
    except OSError:
        await websocket.close(code=1013, reason="VNC server is not ready")
        return

    async def browser_to_vnc() -> None:
        try:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    break
                payload = message.get("bytes")
                if payload is None and message.get("text") is not None:
                    payload = message["text"].encode("utf-8")
                if payload:
                    writer.write(payload)
                    await writer.drain()
        except (WebSocketDisconnect, RuntimeError, ConnectionError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass

    async def vnc_to_browser() -> None:
        try:
            while True:
                payload = await reader.read(65_536)
                if not payload:
                    break
                await websocket.send_bytes(payload)
        except (WebSocketDisconnect, RuntimeError, ConnectionError):
            pass

    tasks = [asyncio.create_task(browser_to_vnc()), asyncio.create_task(vnc_to_browser())]
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    await asyncio.gather(*done, return_exceptions=True)
    await asyncio.gather(*pending, return_exceptions=True)
    try:
        await websocket.close()
    except RuntimeError:
        pass


def novnc_directory() -> Path:
    configured = os.environ.get("NOVNC_DIR")
    candidates = [
        Path(configured) if configured else None,
        Path("/usr/share/novnc"),
        Path("/usr/share/novnc/app"),
    ]
    for candidate in candidates:
        if candidate and (candidate / "vnc.html").exists():
            return candidate
    raise RuntimeError("Could not locate noVNC static files.")


app.mount("/novnc", StaticFiles(directory=str(novnc_directory()), html=True), name="novnc")

ROOT_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Ripo Team Cloud PC</title><style>:root{font-family:Inter,system-ui,sans-serif;color:#f7f8ff;background:#060812}*{box-sizing:border-box}body{margin:0;min-height:100vh;background:radial-gradient(circle at 20% 20%,#27348b55,transparent 34rem),#060812;display:grid;place-items:center}.card{width:min(820px,calc(100% - 28px));padding:34px;border:1px solid #ffffff1c;border-radius:26px;background:#0d1222d9;box-shadow:0 30px 100px #0007}.eyebrow{color:#8e9bc8;font-weight:800;letter-spacing:.14em;font-size:.74rem}h1{font-size:clamp(2.2rem,7vw,5rem);line-height:.96;letter-spacing:-.055em;margin:10px 0 18px}p{color:#b4bddc;line-height:1.7}.actions{display:flex;flex-wrap:wrap;gap:12px;margin-top:26px}a{padding:13px 17px;border-radius:13px;text-decoration:none;font-weight:850}.primary{background:#f5f7ff;color:#071020}.secondary{border:1px solid #ffffff24;color:#f5f7ff}</style></head><body><main class="card"><div class="eyebrow">RIPO TEAM INFRASTRUCTURE</div><h1>Cloud PC is online.</h1><p>This Space runs a Linux x86-64 desktop with a file manager, app panel, terminal, browser and Hermes controls.</p><div class="actions"><a class="primary" href="/desktop">Open HTTPS Linux desktop</a><a class="secondary" href="/gradio_api/info">API info</a><a class="secondary" href="/api/health">Health JSON</a></div></main></body></html>"""


@app.get("/", response_class=HTMLResponse)
async def root() -> str:
    return ROOT_HTML


if os.environ.get("RIPO_SKIP_DESKTOP") != "1":
    start_desktop()

app.launch(show_error=True)
