# Flux RP — Developer & Architecture Documentation

## Framework Architecture
Flux RP is built on the high-performance **QBCore** modular framework, backed by:
- **oxmysql** (asynchronous MySQL client)
- **pma-voice** (high-fidelity 3D positional audio and radio)
- **PolyZone & qb-target** (third-eye interactive raycasting)

### Directory Map
```
flux rp/
├── server-artifacts/       # FiveM FXServer Windows binaries
├── server-data/            # Runtime server directory
│   ├── server.cfg          # Master server configuration & startup order
│   └── resources/
│       ├── [cfx-default]   # Core CitizenFX game management resources
│       ├── [standalone]    # oxmysql, menuv, bob74_ipl, safecracker, PolyZone
│       ├── [voice]         # pma-voice & qb-radio
│       ├── [defaultmaps]   # Pillbox Hospital, Dealership, Prison MLOs
│       ├── [flux]          # Custom Flux RP loading screen & branding
│       └── [qb]            # Core, jobs, vehicles, inventory, banking, police, etc.
├── database/               # Complete SQL schema & table migrations
├── tools/mariadb/          # Zero-install portable MariaDB SQL server
├── scripts/                # Setup & install automation scripts
├── docs/                   # Networking, home hosting, and developer guides
├── start-server.bat        # 1-Click Server Launcher
├── stop-server.bat         # 1-Click Graceful Shutdown
├── backup-server.bat       # Database & config backup utility
└── health-check.bat        # Diagnostic connectivity checker
```

---

## Staff & Owner Permissions

The owner account **`real_ripo6000`** is registered with `qbcore.god` rank in:
- `server-data/server.cfg`: ACE principal binding.
- `server-data/resources/[qb]/qb-core/server/events.lua`: Automatic runtime verification on connect.

### Key Admin Commands
- `/admin`: Opens the comprehensive QBCore Administration GUI.
- `/noclip`: Toggle noclip flying mode.
- `/car [spawncode]`: Spawn any vehicle instantly.
- `/dv`: Delete current or targeted vehicle.
- `/revive [id]`: Revive a downed player or yourself.
- `/giveitem [id] [item] [amount]`: Give items directly.
- `/setjob [id] [job] [grade]`: Assign any job (police, ems, mechanic, etc.).
- `/ban [id] [time] [reason]`: Issue temporary or permanent bans.
