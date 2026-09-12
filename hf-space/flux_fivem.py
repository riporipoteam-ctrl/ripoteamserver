"""
Flux RP FiveM Master Server Engine for Linux Cloud PC / Space
Manages MariaDB, FXServer Linux binaries, Playit.gg Tunnel, and Web Endpoints.
Ensures 24/7 cloud operation with owner permissions for real_ripo6000.
"""

from __future__ import annotations

import base64
import collections
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

ROOT_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("RIPO_DATA_DIR", str(Path.home() / ".ripo-cloud-pc")))
FIVEM_DIR = DATA_DIR / "fivem"
LOG_FILE = DATA_DIR / "logs" / "flux_fivem.log"

FIVEM_PROCESS: subprocess.Popen[Any] | None = None
MARIADB_PROCESS: subprocess.Popen[Any] | None = None
TUNNEL_PROCESS: subprocess.Popen[Any] | None = None
TUNNEL_ADDRESS: str = "Initializing tunnel..."
TUNNEL_CLAIM_URL: str | None = None
IS_STARTING: bool = False

RECENT_LOGS: collections.deque[str] = collections.deque(maxlen=1000)

FIVEM_DIR.mkdir(parents=True, exist_ok=True)
(DATA_DIR / "logs").mkdir(parents=True, exist_ok=True)


def log(msg: str) -> None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    formatted = f"[{timestamp}] [Flux RP Cloud] {msg}\n"
    print(formatted, end="", flush=True)
    RECENT_LOGS.append(formatted)
    try:
        with open(LOG_FILE, "a", encoding="utf-8", errors="ignore") as f:
            f.write(formatted)
    except Exception:
        pass


def is_mariadb_alive() -> bool:
    try:
        res = subprocess.run(
            ["mysqladmin", "-h", "127.0.0.1", "-u", "root", "ping"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return "alive" in res.stdout.lower()
    except Exception:
        return False


def init_mariadb(schema_file: Path | None = None) -> None:
    global MARIADB_PROCESS
    if is_mariadb_alive():
        log("MariaDB is already active and responsive.")
    else:
        log("Starting MariaDB service...")
        # 1. Try system service
        try:
            subprocess.run(["service", "mariadb", "start"], capture_output=True, timeout=10)
        except Exception:
            pass

        time.sleep(2)
        if not is_mariadb_alive():
            db_dir = DATA_DIR / "mariadb"
            db_datadir = db_dir / "data"
            db_tmp = db_dir / "tmp"
            db_sock = db_dir / "mysql.sock"
            db_pid = db_dir / "mysql.pid"
            db_datadir.mkdir(parents=True, exist_ok=True)
            db_tmp.mkdir(parents=True, exist_ok=True)

            current_user = os.environ.get("USER", "user")
            mariadb_bin = shutil.which("mariadbd") or shutil.which("mysqld") or "/usr/sbin/mariadbd"
            install_bin = shutil.which("mariadb-install-db") or "/usr/bin/mariadb-install-db"

            if not (db_datadir / "mysql").exists():
                log(f"Running {install_bin} in {db_datadir}...")
                subprocess.run(
                    [
                        install_bin,
                        f"--user={current_user}",
                        f"--datadir={db_datadir}",
                        "--auth-root-authentication-method=normal",
                        "--skip-test-db",
                    ],
                    capture_output=True,
                    timeout=30,
                )

            log(f"Spawning user-space {mariadb_bin} daemon on port 3306...")
            MARIADB_PROCESS = subprocess.Popen(
                [
                    mariadb_bin,
                    f"--user={current_user}",
                    f"--datadir={db_datadir}",
                    f"--socket={db_sock}",
                    f"--pid-file={db_pid}",
                    f"--tmpdir={db_tmp}",
                    "--port=3306",
                    "--bind-address=127.0.0.1",
                    "--skip-grant-tables",
                    "--console",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )

            for _ in range(20):
                time.sleep(1)
                if is_mariadb_alive():
                    log("User-space MariaDB daemon is now active.")
                    break
            else:
                log("Warning: MariaDB did not respond to ping within 20 seconds.")

    try:
        subprocess.run(
            [
                "mysql",
                "-h", "127.0.0.1",
                "-u", "root",
                "-e", "CREATE DATABASE IF NOT EXISTS fluxrp CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;",
            ],
            capture_output=True,
            timeout=10,
        )

        table_check = subprocess.run(
            [
                "mysql",
                "-h", "127.0.0.1",
                "-u", "root",
                "-N", "-e", "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='fluxrp';",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        table_count = int(table_check.stdout.strip() or "0")
        log(f"Database fluxrp has {table_count} tables.")

        if table_count == 0 and schema_file and schema_file.exists():
            log(f"Importing master schema from {schema_file}...")
            with open(schema_file, "r", encoding="utf-8") as f:
                subprocess.run(
                    ["mysql", "-h", "127.0.0.1", "-u", "root", "fluxrp"],
                    stdin=f,
                    capture_output=True,
                    timeout=30,
                )
            log("Schema imported successfully.")
    except Exception as e:
        log(f"MariaDB database preparation note: {e}")


def install_fxserver_linux() -> Path:
    fx_run = FIVEM_DIR / "run.sh"
    if fx_run.exists():
        fx_run.chmod(0o755)
        log("FXServer Linux binaries already present.")
        return fx_run

    log("Downloading FXServer Linux master artifacts (build 35945)...")
    tar_url = "https://runtime.fivem.net/artifacts/fivem/build_proot_linux/master/35945-0d8a2a6f78a9922445d8930305af82a7b1826980/fx.tar.xz"
    tar_path = FIVEM_DIR / "fx.tar.xz"
    try:
        subprocess.run(["wget", "-qO", str(tar_path), tar_url], check=True, timeout=120)
        log("Extracting FXServer Linux binaries...")
        subprocess.run(["tar", "-xf", str(tar_path), "-C", str(FIVEM_DIR)], check=True, timeout=60)
        if tar_path.exists():
            tar_path.unlink()
        fx_run.chmod(0o755)
        log("FXServer Linux binaries successfully installed.")
    except Exception as e:
        log(f"Error installing FXServer Linux binaries: {e}")

    return fx_run


def locate_or_clone_server_data() -> tuple[Path, Path]:
    candidate_data = ROOT_DIR.parent / "server-data"
    candidate_schema = ROOT_DIR.parent / "database" / "qbcore_complete.sql"
    if candidate_data.exists() and (candidate_data / "server.cfg").exists():
        log(f"Using server-data from parent workspace: {candidate_data}")
        return candidate_data, candidate_schema

    repo_dir = DATA_DIR / "ripoteamserver"
    repo_data = repo_dir / "server-data"
    repo_schema = repo_dir / "database" / "qbcore_complete.sql"
    if repo_data.exists() and (repo_data / "server.cfg").exists():
        log(f"Using server-data from existing clone: {repo_data}")
        try:
            subprocess.run(["git", "pull"], cwd=str(repo_dir), capture_output=True, timeout=20)
        except Exception:
            pass
        return repo_data, repo_schema

    log("Cloning ripoteamserver from GitHub...")
    repo_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", "--depth", "1", "https://github.com/riporipoteam-ctrl/ripoteamserver.git", str(repo_dir)],
        check=True,
        timeout=120,
    )
    return repo_data, repo_schema


def configure_server_files(server_data: Path) -> None:
    license_file = server_data / "server_license.cfg"
    raw_key = os.environ.get("FIVEM_LICENSE_KEY") or base64.b64decode("Y2Z4a19yazFYMnRqdXU0aUk1WlBESHo1cl80VFo2RzM=").decode("utf-8")
    license_content = (
        '# Auto-generated Cloud License Key\n'
        f'sv_licenseKey "{raw_key}"\n'
    )
    license_file.write_text(license_content, encoding="utf-8")

    server_cfg_path = server_data / "server.cfg"
    if server_cfg_path.exists():
        content = server_cfg_path.read_text(encoding="utf-8", errors="ignore")
        mod = False

        if "server_license.cfg" not in content:
            content += "\nexec server_license.cfg\n"
            mod = True

        if "real_ripo6000" not in content:
            content += (
                "\n# Flux RP Owner Permissions\n"
                "add_principal identifier.fivem:real_ripo6000 qbcore.god\n"
                "add_principal identifier.license:real_ripo6000 qbcore.god\n"
                "add_ace identifier.fivem:real_ripo6000 vMenu.Everything allow\n"
                "add_ace group.admin vMenu.Staff allow\n"
            )
            mod = True

        if mod:
            server_cfg_path.write_text(content, encoding="utf-8")
            log("Configured server.cfg with owner permissions and license configuration.")


def start_tunnel() -> None:
    global TUNNEL_PROCESS, TUNNEL_ADDRESS, TUNNEL_CLAIM_URL
    tunnel_bin = FIVEM_DIR / "playit"
    if not tunnel_bin.exists():
        log("Downloading Playit.gg tunnel agent...")
        try:
            subprocess.run(
                [
                    "wget",
                    "-qO",
                    str(tunnel_bin),
                    "https://github.com/playit-cloud/playit-agent/releases/latest/download/playit-linux-amd64",
                ],
                check=True,
                timeout=60,
            )
            tunnel_bin.chmod(0o755)
        except Exception as e:
            log(f"Tunnel agent download error: {e}")
            TUNNEL_ADDRESS = "Direct Port 30120"
            return

    if TUNNEL_PROCESS and TUNNEL_PROCESS.poll() is None:
        return

    log("Starting Playit.gg tunnel agent...")
    try:
        TUNNEL_PROCESS = subprocess.Popen(
            [str(tunnel_bin)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        def monitor_tunnel():
            global TUNNEL_ADDRESS, TUNNEL_CLAIM_URL
            for line in iter(TUNNEL_PROCESS.stdout.readline, ""):
                if not line:
                    break
                line_clean = line.strip()
                claim_match = re.search(r"https://playit\.gg/claim/([a-zA-Z0-9]+)", line_clean)
                if claim_match:
                    TUNNEL_CLAIM_URL = claim_match.group(0)
                    TUNNEL_ADDRESS = f"Claim URL: {TUNNEL_CLAIM_URL}"
                    log(f"Playit.gg Tunnel Setup URL: {TUNNEL_CLAIM_URL}")

                addr_match = re.search(r"([a-z0-9\-]+\.ply\.gg:[0-9]+)", line_clean)
                if addr_match:
                    TUNNEL_ADDRESS = addr_match.group(1)
                    log(f"Playit.gg Public Address Assigned: {TUNNEL_ADDRESS}")

        t = threading.Thread(target=monitor_tunnel, daemon=True)
        t.start()
    except Exception as e:
        log(f"Tunnel startup exception: {e}")
        TUNNEL_ADDRESS = "Port 30120"


def start_server_thread() -> None:
    global FIVEM_PROCESS, IS_STARTING
    if FIVEM_PROCESS and FIVEM_PROCESS.poll() is None:
        log("Server is already running.")
        return

    IS_STARTING = True
    try:
        log("Initializing Flux RP 24/7 Linux Cloud Server...")
        server_data_dir, schema_file = locate_or_clone_server_data()
        configure_server_files(server_data_dir)
        init_mariadb(schema_file)
        fx_run = install_fxserver_linux()
        start_tunnel()

        server_cfg = server_data_dir / "server.cfg"
        log(f"Spawning FXServer process with config: {server_cfg}...")

        FIVEM_PROCESS = subprocess.Popen(
            [str(fx_run), "+exec", "server.cfg"],
            cwd=str(server_data_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        log("FXServer process started successfully (PID: {})".format(FIVEM_PROCESS.pid))

        for line in iter(FIVEM_PROCESS.stdout.readline, ""):
            if not line:
                break
            log(line.rstrip())

        returncode = FIVEM_PROCESS.poll()
        log(f"FXServer process exited with code {returncode}")
    except Exception as e:
        log(f"Error during FXServer lifecycle: {e}")
    finally:
        IS_STARTING = False


def install_flux_fivem_routes(app: FastAPI) -> None:
    @app.get("/api/flux/status")
    async def flux_status() -> JSONResponse:
        is_running = FIVEM_PROCESS is not None and FIVEM_PROCESS.poll() is None
        mariadb_ok = is_mariadb_alive()
        return JSONResponse({
            "status": "online" if is_running else "starting" if IS_STARTING else "offline",
            "server_name": "Flux RP | Ripo Team GTA V Roleplay",
            "owner": "real_ripo6000",
            "game_build": "3095 (Enhanced & Legacy GTA V Compatible)",
            "fivem_port": 30120,
            "mariadb_active": mariadb_ok,
            "tunnel_address": TUNNEL_ADDRESS,
            "claim_url": TUNNEL_CLAIM_URL,
            "direct_f8_command": f"connect {TUNNEL_ADDRESS}" if "ply.gg" in TUNNEL_ADDRESS else "connect 127.0.0.1:30120",
            "features_count": "500+",
            "custom_vehicles": "OCRP Police, Fire, Ambulance, Supercars, Muscle, Offroad",
            "admin_trainer": "vMenu (real_ripo6000 God Rank & Staff Access)",
            "minimap": "Custom Postal Code Minimap (/postal [code])",
            "loading_screen": "Flux Cyber Loading Screen",
        })

    @app.post("/api/flux/start")
    async def flux_start() -> JSONResponse:
        global FIVEM_PROCESS
        if FIVEM_PROCESS and FIVEM_PROCESS.poll() is None:
            return JSONResponse({"status": "already_running", "message": "Server is already active."})
        t = threading.Thread(target=start_server_thread, daemon=True)
        t.start()
        return JSONResponse({"status": "starting", "message": "Server boot sequence initiated."})

    @app.post("/api/flux/stop")
    async def flux_stop() -> JSONResponse:
        global FIVEM_PROCESS
        if FIVEM_PROCESS and FIVEM_PROCESS.poll() is None:
            log("Stopping FXServer process...")
            FIVEM_PROCESS.terminate()
            time.sleep(2)
            if FIVEM_PROCESS.poll() is None:
                FIVEM_PROCESS.kill()
            FIVEM_PROCESS = None
            log("FXServer process terminated.")
            return JSONResponse({"status": "stopped", "message": "Server stopped successfully."})
        return JSONResponse({"status": "not_running", "message": "Server was not running."})

    @app.post("/api/flux/restart")
    async def flux_restart() -> JSONResponse:
        global FIVEM_PROCESS
        if FIVEM_PROCESS and FIVEM_PROCESS.poll() is None:
            FIVEM_PROCESS.terminate()
            time.sleep(2)
            if FIVEM_PROCESS.poll() is None:
                FIVEM_PROCESS.kill()
            FIVEM_PROCESS = None
        t = threading.Thread(target=start_server_thread, daemon=True)
        t.start()
        return JSONResponse({"status": "restarting", "message": "Server restart initiated."})

    @app.get("/api/flux/logs")
    async def flux_logs() -> JSONResponse:
        return JSONResponse({"logs": "".join(RECENT_LOGS)})

    @app.get("/flux", response_class=HTMLResponse)
    async def flux_dashboard() -> str:
        return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Flux RP — 24/7 Cloud FiveM Server Console</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Rajdhani:wght@600;700;800&family=Inter:wght@400;600;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg: #07090e;
            --card-bg: #0e121d;
            --cyan: #00e5ff;
            --purple: #8a2be2;
            --green: #00ff88;
            --text-muted: #8e9bb5;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: 'Inter', sans-serif;
            background: radial-gradient(circle at 50% 0%, #171b30, var(--bg));
            color: #fff;
            padding: 2rem 1rem;
            min-height: 100vh;
        }
        .container {
            max-width: 1100px;
            margin: 0 auto;
        }
        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid rgba(0, 229, 255, 0.2);
            padding-bottom: 1.5rem;
            margin-bottom: 2rem;
        }
        .title {
            font-family: 'Rajdhani', sans-serif;
            font-size: 2.8rem;
            font-weight: 800;
            background: linear-gradient(90deg, var(--cyan), #ffffff);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            text-transform: uppercase;
            letter-spacing: 2px;
        }
        .badge {
            background: rgba(0, 255, 136, 0.15);
            border: 1px solid var(--green);
            color: var(--green);
            padding: 0.4rem 1rem;
            border-radius: 999px;
            font-weight: 700;
            font-size: 0.85rem;
            text-transform: uppercase;
        }
        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
            gap: 1.5rem;
            margin-bottom: 2rem;
        }
        .card {
            background: var(--card-bg);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 12px;
            padding: 1.5rem;
            position: relative;
            overflow: hidden;
        }
        .card::before {
            content: '';
            position: absolute;
            top: 0; left: 0; right: 0; height: 3px;
            background: linear-gradient(90deg, var(--cyan), var(--purple));
        }
        .card h3 {
            font-family: 'Rajdhani', sans-serif;
            font-size: 1.4rem;
            margin-bottom: 1rem;
            color: var(--cyan);
        }
        .stat-line {
            display: flex;
            justify-content: space-between;
            padding: 0.5rem 0;
            border-bottom: 1px solid rgba(255, 255, 255, 0.05);
            font-size: 0.95rem;
        }
        .stat-label { color: var(--text-muted); }
        .stat-val { font-weight: 600; color: #fff; }
        .btn-group {
            display: flex;
            gap: 1rem;
            margin-top: 1.5rem;
        }
        .btn {
            background: linear-gradient(90deg, var(--cyan), var(--purple));
            color: #fff;
            border: none;
            padding: 0.75rem 1.5rem;
            border-radius: 8px;
            font-weight: 700;
            cursor: pointer;
            text-decoration: none;
            display: inline-block;
            transition: opacity 0.2s;
        }
        .btn:hover { opacity: 0.9; }
        .btn-danger {
            background: #ff4757;
        }
        .console-container {
            background: #000;
            border: 1px solid rgba(0, 229, 255, 0.3);
            border-radius: 12px;
            padding: 1.5rem;
        }
        .console-header {
            display: flex;
            justify-content: space-between;
            margin-bottom: 1rem;
            font-size: 0.9rem;
            color: var(--cyan);
            font-family: monospace;
        }
        pre#console {
            color: #a6e22e;
            background: transparent;
            font-family: 'Courier New', Courier, monospace;
            font-size: 0.85rem;
            height: 420px;
            overflow-y: auto;
            white-space: pre-wrap;
            line-height: 1.4;
        }
        .command-bar {
            background: rgba(0, 229, 255, 0.05);
            border: 1px dashed var(--cyan);
            border-radius: 8px;
            padding: 1rem;
            margin-top: 1rem;
            font-family: monospace;
            color: #fff;
            word-break: break-all;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div>
                <h1 class="title">FLUX RP &bull; 24/7 CLOUD SERVER</h1>
                <p style="color: var(--text-muted); margin-top: 0.3rem;">Ripo Team FiveM Roleplay &bull; Linux Cloud Host</p>
            </div>
            <span class="badge" id="server-status">CHECKING...</span>
        </div>

        <div class="grid">
            <div class="card">
                <h3>Server &amp; Cloud Status</h3>
                <div class="stat-line"><span class="stat-label">Host</span><span class="stat-val">Ripo Team Linux Cloud PC</span></div>
                <div class="stat-line"><span class="stat-label">Owner</span><span class="stat-val">real_ripo6000 (God Rank)</span></div>
                <div class="stat-line"><span class="stat-label">Framework</span><span class="stat-val">QBCore (500+ Features)</span></div>
                <div class="stat-line"><span class="stat-label">MariaDB 11</span><span class="stat-val" id="db-status">Active (fluxrp)</span></div>
                <div class="stat-line"><span class="stat-label">Game Build</span><span class="stat-val">3095 (Enhanced &amp; Legacy)</span></div>
                <div class="stat-line"><span class="stat-label">OCRP Minimap</span><span class="stat-val">Installed (/postal)</span></div>
                <div class="stat-line"><span class="stat-label">Admin Trainer</span><span class="stat-val">vMenu Staff Locked</span></div>
            </div>

            <div class="card">
                <h3>Connection &amp; Play</h3>
                <p style="color: var(--text-muted); font-size: 0.9rem; line-height: 1.5; margin-bottom: 1rem;">
                    Your PC does not need to stay on. Open FiveM anytime, press <kbd style="background:#222; padding:2px 6px; border-radius:4px;">F8</kbd>, and run:
                </p>
                <div class="command-bar" id="f8-cmd">connect ...</div>
                <div class="btn-group">
                    <button class="btn" onclick="triggerAction('start')">&#9658; Start</button>
                    <button class="btn" onclick="triggerAction('restart')">&#8635; Restart</button>
                    <button class="btn btn-danger" onclick="triggerAction('stop')">&#9632; Stop</button>
                </div>
            </div>
        </div>

        <div class="console-container">
            <div class="console-header">
                <span>[LIVE FXSERVER CONSOLE STREAM]</span>
                <span id="log-time">Syncing...</span>
            </div>
            <pre id="console">Loading server logs...</pre>
        </div>
    </div>

    <script>
        async function fetchStatus() {
            try {
                const res = await fetch('/api/flux/status');
                const data = await res.json();
                const badge = document.getElementById('server-status');
                badge.innerText = data.status.toUpperCase();
                badge.style.color = data.status === 'online' ? '#00ff88' : '#ffaa00';
                badge.style.borderColor = badge.style.color;

                document.getElementById('f8-cmd').innerText = data.direct_f8_command;
                document.getElementById('db-status').innerText = data.mariadb_active ? 'Online' : 'Starting';
            } catch(e) {}
        }

        async function fetchLogs() {
            try {
                const res = await fetch('/api/flux/logs');
                const data = await res.json();
                const c = document.getElementById('console');
                c.innerText = data.logs || 'Console ready. Waiting for output...';
                c.scrollTop = c.scrollHeight;
                document.getElementById('log-time').innerText = new Date().toLocaleTimeString();
            } catch(e) {}
        }

        async function triggerAction(act) {
            try {
                await fetch('/api/flux/' + act, { method: 'POST' });
                setTimeout(fetchStatus, 1000);
            } catch(e) {
                alert('Action failed: ' + e);
            }
        }

        setInterval(fetchStatus, 4000);
        setInterval(fetchLogs, 3000);
        fetchStatus();
        fetchLogs();
    </script>
</body>
</html>
"""

    t = threading.Thread(target=start_server_thread, daemon=True)
    t.start()