@echo off
title Flux RP - Database and Config Backup
set ROOT=%~dp0
set BACKUP_DIR=%ROOT%backups
if not exist "%BACKUP_DIR%" mkdir "%BACKUP_DIR%"

for /f %%I in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set TIMESTAMP=%%I

echo [Flux RP] Creating backup at %TIMESTAMP%...
"%ROOT%tools\mariadb\bin\mariadb-dump.exe" -u root fluxrp > "%BACKUP_DIR%\fluxrp_%TIMESTAMP%.sql"
copy "%ROOT%server-data\server.cfg" "%BACKUP_DIR%\server_%TIMESTAMP%.cfg" >nul

echo [Flux RP] Backup completed successfully: %BACKUP_DIR%\fluxrp_%TIMESTAMP%.sql
