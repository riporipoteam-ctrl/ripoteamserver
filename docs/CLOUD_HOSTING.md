# Flux RP — 24/7 Cloud Hosting Guide (Running with PC Off)

To allow your friends and the public to play on **Flux RP** 24 hours a day, 7 days a week — even when your home computer is shut down — the server must run on a cloud virtual server (VPS).

---

## Why a Cloud Server is Required for 24/7 Play
- A personal PC only hosts while powered on and connected to your home router.
- GitHub is a software repository; it does not host active game server hardware.
- A Cloud VPS runs in a datacenter with 99.9% uptime, dedicated fiber internet, and DDoS protection.

---

## 1. Getting a Cloud Server (Free & Budget Options)

1. **Oracle Cloud Always Free Tier (Recommended & 100% Free)**:
   - Provides an **ARM / Ampere VM with up to 4 Cores and 24 GB of RAM** completely free forever.
   - Ideal for hosting Flux RP 24/7 at zero monthly cost.
2. **Budget Linux VPS (Alternative)**:
   - **Hetzner Cloud** (~$4/month for 4GB RAM).
   - **OVH Cloud** (~$5/month with included game DDoS protection).
   - **Zap-Hosting** (Official FiveM partner with 1-click install).

---

## 2. 1-Command Setup on Any Linux Cloud Server

Once you have your cloud server (Ubuntu 22.04 or 24.04):

1. Connect to your VPS via SSH:
   ```bash
   ssh root@YOUR_SERVER_IP
   ```

2. Clone your GitHub repository:
   ```bash
   git clone https://github.com/riporipoteam-ctrl/ripoteamserver.git /home/fluxrp
   cd /home/fluxrp
   ```

3. Run the automated 24/7 setup script:
   ```bash
   chmod +x deploy/setup-vps.sh
   sudo ./deploy/setup-vps.sh
   ```

The script will automatically:
- Install MariaDB and import the complete `fluxrp` database.
- Download the Linux FXServer engine.
- Open game ports `30120` (UDP/TCP) and `40120` (txAdmin).
- Install a `systemd` watchdog service that starts the server automatically on boot and auto-restarts on any crashes.

---

## 3. Remote txAdmin Web Access

Once running on your cloud server, open your web browser to:
```text
http://YOUR_SERVER_IP:40120
```
This gives you full graphical web control over the server from any computer, tablet, or phone!
