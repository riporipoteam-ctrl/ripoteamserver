# Ripo Team RP — Audit and Deployment Status

## Audit snapshot

The repository is an existing **QBCore-based FiveM server**, not an empty template. It currently contains the QBCore framework and jobs, `oxmysql`, `pma-voice`, PolyZone, vMenu, default maps, a Flux RP loading screen, SQL schemas, and Windows/Linux helper scripts. The current configuration declares 94 resource manifests and starts the main character, inventory, economy, phone, vehicle, emergency-service, civilian-job, criminal-activity, and administration resources.

The owner name `real_ripo6000` already has QBCore and vMenu ACE entries. A FiveM username is not a reliable permanent authentication identifier, however. The production owner binding should be replaced or supplemented with the account's actual `license:` or `fivem:` identifier, copied from the FXServer connection log after the owner joins.

## What GitHub does and does not host

GitHub is the source-of-truth repository; it does **not** run FXServer, MariaDB, txAdmin, GTA V clients, router port forwarding, or a 24/7 game process. The supplied repository does not contain the FXServer runtime, MariaDB binaries, or a live host connection. The server therefore cannot be truthfully described as fully hosted until the Windows PC or a VPS has been configured and kept online.

## Required host actions

1. Install a current FXServer artifact from the official Cfx.re documentation into `server-artifacts/` locally. Do not commit binaries.
2. Install MariaDB locally, create the `fluxrp` database and a least-privilege database user, then import `database/qbcore_complete.sql`.
3. Copy `server-data/server_license.cfg.example` to `server-data/server_license.cfg` and place the Cfx.re key there. The destination file is ignored by Git.
4. Set `mysql_connection_string` in the local configuration to the host's actual database credentials. Do not expose port 3306 publicly.
5. For home hosting, forward TCP/UDP 30120 to the Windows host. Expose txAdmin only on a trusted management network or through a protected reverse proxy; never expose MariaDB.
6. Start the server with `start-server.bat`, then run `health-check.bat` and inspect the FXServer console for resource and database errors.

## Compatibility

A FiveM resource pack is generally shared between Legacy and Enhanced clients when every resource and asset supports the target artifact/game build. This repository can be prepared for dual-client testing, but compatibility cannot be certified from the sandbox because no Windows FXServer/client pair is available here. Test with both client types after the host is online, beginning with the default enforced build and then enabling only assets verified for Enhanced.

## Assets and licenses

Menyoo, NaturalVision Enhanced, Reshade presets, custom cars, custom interiors/MLOs, custom weapons, and custom audio are not interchangeable source-code dependencies. They must be obtained from their legitimate authors, installed on the host, and checked for FiveM/Legacy/Enhanced compatibility and redistribution rights. The repository intentionally does not install leaked, cracked, or unlicensed paid resources. Menyoo should not be treated as a production server administration system; use the already included vMenu/QBCore admin controls for server permissions and add any legitimate trainer only for authorized local testing.

## Scope status

The repository contains a broad foundation, but the request for 667+ end-to-end features is not complete merely because similarly named resources exist. Each subsystem still requires a Windows integration test, persistence test, abuse-case test, and performance review. No claim is made here that every requested feature is fully implemented.

## Safe secret handling

The provided registration key was not written to the repository, committed, or repeated in project files. Because it was pasted into chat, rotate it in the Cfx.re portal if there is any possibility that this conversation or its logs are accessible to anyone other than the server owner. Keep all live credentials in ignored local files or the host's secret manager.
