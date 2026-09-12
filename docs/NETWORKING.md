# Flux RP — Home Hosting & Networking Guide

This guide details how to host **Flux RP (Ripo Team Roleplay)** from your own Windows machine, connect locally, and open the server to friends across the internet.

---

## 1. Quick Connect URLs

- **Local Machine (You)**:
  - In FiveM client, press `F8` and type:
    ```
    connect localhost:30120
    ```
  - Or click the direct link: `fivem://connect/localhost:30120`

- **Local LAN (Devices in your home)**:
  - `connect 192.168.x.x:30120` (use your local IPv4 address)

- **External Players (Friends on the Internet)**:
  - `connect YOUR_PUBLIC_IP:30120`
  - Or through the FiveM Server List using your server tag `Flux RP`.

---

## 2. Windows Firewall Rules

To allow incoming game connections, run the following command in PowerShell as Administrator:

```powershell
# Open FiveM Game Server Ports (30120 TCP & UDP)
New-NetFirewallRule -DisplayName "FiveM FXServer Game Port (30120 TCP)" -Direction Inbound -LocalPort 30120 -Protocol TCP -Action Allow
New-NetFirewallRule -DisplayName "FiveM FXServer Game Port (30120 UDP)" -Direction Inbound -LocalPort 30120 -Protocol UDP -Action Allow

# Optional: Open txAdmin Web Panel for remote administration (40120 TCP)
New-NetFirewallRule -DisplayName "FiveM txAdmin Web Panel (40120 TCP)" -Direction Inbound -LocalPort 40120 -Protocol TCP -Action Allow
```

> **Security Warning**: NEVER expose port 3306 (MariaDB) to the public internet. It is strictly configured for `127.0.0.1` (localhost only).

---

## 3. Router Port Forwarding Setup

To let players outside your home connect to your computer:

1. Open your router administration panel (typically `192.168.1.1` or `192.168.0.1`).
2. Locate the **Port Forwarding** or **Virtual Server** section.
3. Add the following forwarding rules pointing to your computer's local IP:

| Rule Name | Protocol | External Port | Internal Port | Internal IP Address |
| :--- | :--- | :--- | :--- | :--- |
| **FiveM Game** | **UDP** | 30120 | 30120 | *Your Local PC IP* |
| **FiveM HTTP** | **TCP** | 30120 | 30120 | *Your Local PC IP* |

4. Save and reboot your router if required.

---

## 4. Finding Your Public IP Address

To give your friends your external connection IP:
1. Visit `https://api.ipify.org` or Google "What is my IP".
2. Share the direct connect format:
   `fivem://connect/YOUR_PUBLIC_IP:30120`

---

## 5. Dynamic IP & DDNS (Optional)

If your home ISP changes your public IP address periodically, you can set up a free Dynamic DNS provider (such as No-IP or DuckDNS). Friends can then connect via:
`connect yourname.duckdns.org:30120`
