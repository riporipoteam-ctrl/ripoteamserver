@echo off
title Flux RP ? Ripo Team Master Server Host
color 0b

echo ======================================================================
echo                     FLUX RP - MASTER SERVER LAUNCHER
echo                     Owned & Operated by Ripo Team
echo ======================================================================
echo.

set ROOT=%~dp0
set MARIADB_DIR=%ROOT%tools\mariadb
set ARTIFACTS_DIR=%ROOT%server-artifacts
set DATA_DIR=%ROOT%server-data

:: Check if MariaDB is running
powershell -Command "$t = Test-NetConnection -ComputerName 127.0.0.1 -Port 3306 -InformationLevel Quiet; if (!$t) { exit 1 } else { exit 0 }"
if %ERRORLEVEL% NEQ 0 (
    echo [Flux RP] Starting portable MariaDB database engine...
    start /min "FluxRP-MariaDB" "%MARIADB_DIR%\bin\mariadbd.exe" --datadir="%MARIADB_DIR%\data" --port=3306
    timeout /t 3 /nobreak >nul
) else (
    echo [Flux RP] MariaDB database is already online.
)

:: Validate Database Connection
echo [Flux RP] Validating database table health...
"%MARIADB_DIR%\bin\mariadb.exe" -u root fluxrp -e "SELECT count(*) FROM players;" >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [Flux RP] Database schema is healthy.
) else (
    echo [Flux RP] Warning: Initializing schema if needed...
    "%MARIADB_DIR%\bin\mariadb.exe" -u root fluxrp -e "source %ROOT%database/qbcore_complete.sql" >nul 2>&1
)

:: Launch FiveM FXServer
echo [Flux RP] Launching FXServer on port 30120...
echo [Flux RP] Direct Connect: fivem://connect/localhost:30120
echo [Flux RP] Console will stream below. Press CTRL+C to stop.
echo ======================================================================
echo.

cd /d "%DATA_DIR%"
"%ARTIFACTS_DIR%\FXServer.exe" +exec server.cfg
