# FLUX RP — Official Ripo Team GTA V FiveM Server

Welcome to the single source of truth for **Flux RP**, the premier GTA V FiveM Roleplay experience developed and maintained by **Ripo Team**.

---

## ⚡ Quick Start (Run Locally on Windows)

1. **Launch Server**:
   Double click `start-server.bat`.
   This will automatically initialize the local MariaDB database engine, verify tables, and launch FXServer.

2. **Connect to Game**:
   Open FiveM on your PC, press `F8`, and type:
   ```
   connect localhost:30120
   ```
   Or open your browser and click: `fivem://connect/localhost:30120`

3. **Stop Server**:
   Run `stop-server.bat` to gracefully shutdown the server and database.

---

## 👑 Owner & Admin Setup
- **Owner Account**: `real_ripo6000`
- Configured with `qbcore.god` permissions with full access to `/admin`, `/noclip`, `/car`, `/setjob`, and player management.

---

## 🎮 Included RP Systems
- **Character Life**: Multicharacter selection, complete clothing customization, barber shops, tattoo parlors, persistent hunger/thirst.
- **Economy & Banking**: Dynamic personal and shared bank accounts, ATM cards, paychecks, cash economy.
- **Inventory**: Slot-based visual inventory with weight limits, item durability, weapon attachments, gloveboxes, trunks, and stashes.
- **Emergency Services**: Comprehensive Police MDT, handcuffs, evidence collection, armory, jail system, EMS dispatch, triage, hospital revive.
- **Vehicles**: Dealerships, financing, realistic fueling, damage, persistence garages, mechanic repairs, and performance tuning.
- **Crime & Heists**: Bank robberies, jewelry store heists, house burglaries, drug cultivation, packaging, and black market dealers.
- **Custom UI**: Integrated modern HUD, radial menu, third-eye target interaction, and custom **Flux RP Loading Screen**.

---

## 🌐 Home Hosting & Port Forwarding
To allow external friends to join your server, review the [Networking Guide](docs/NETWORKING.md).
Ports to forward in your router:
- **30120 UDP & TCP** (Game Server)
- **40120 TCP** (Optional txAdmin Web Management)

---

## 💾 Backups
Run `backup-server.bat` at any time to export a timestamped SQL snapshot of your database and server configuration to the `backups/` directory.
