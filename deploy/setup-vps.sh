#!/usr/bin/env bash
# ==============================================================================
# FLUX RP ? 24/7 1-COMMAND CLOUD VPS AUTO-DEPLOYMENT SCRIPT
# Runs on Ubuntu 22.04 / 24.04 / Debian / Oracle Linux
# ==============================================================================
set -e

echo "=== [Flux RP] Initializing 24/7 Cloud Server Setup ==="
REPO_DIR=$(pwd)
if [ ! -f "./server-data/server_license.cfg" ]; then
  echo "ERROR: server-data/server_license.cfg is missing. Copy server_license.cfg.example and add your Cfx.re key locally before starting." >&2
  exit 1
fi
if grep -q 'CHANGE_ME' ./server-data/server.cfg; then
  echo "ERROR: Configure the MariaDB connection string in server-data/server.cfg before starting." >&2
  exit 1
fi

# 1. Update and install dependencies
sudo apt-get update && sudo apt-get install -y git curl wget xz-utils mariadb-server ufw

# 2. Configure Firewall (Open FiveM & txAdmin ports)
echo "=== [Flux RP] Configuring Cloud Firewall ==="
sudo ufw allow 30120/tcp
sudo ufw allow 30120/udp
sudo ufw allow 40120/tcp
sudo ufw allow 22/tcp
sudo ufw --force enable

# 3. Setup MariaDB
echo "=== [Flux RP] Initializing Database ==="
sudo systemctl enable mariadb
sudo systemctl start mariadb
sudo mysql -e "CREATE DATABASE IF NOT EXISTS fluxrp CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
sudo mysql fluxrp < ./database/qbcore_complete.sql || true

# 4. Download FXServer Linux Artifacts
echo "=== [Flux RP] Downloading FXServer Linux Engine ==="
mkdir -p /opt/cfx-server
cd /opt/cfx-server
wget -qO server.tar.xz https://runtime.fivem.net/artifacts/fivem/build_proot_linux/master/35945-0d8a2a6f78a9922445d8930305af82a7b1826980/fx.tar.xz
tar xf server.tar.xz
rm server.tar.xz

# 5. Create Systemd Service for 24/7 Auto-Start & Crash Recovery
echo "=== [Flux RP] Creating 24/7 Systemd Background Service ==="
sudo tee /etc/systemd/system/fluxrp.service > /dev/null <<EOF
[Unit]
Description=Flux RP FiveM Master Server (24/7 Auto-Start)
After=network.target mariadb.service

[Service]
Type=simple
User=root
WorkingDirectory=${REPO_DIR}/server-data
ExecStart=/opt/cfx-server/run.sh +exec server.cfg
Restart=always
RestartSec=5s

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable fluxrp.service
sudo systemctl start fluxrp.service

echo "=== [Flux RP] 24/7 Cloud Host is Online! ==="
echo "Status: sudo systemctl status fluxrp.service"
echo "Logs:   journalctl -u fluxrp.service -f"
