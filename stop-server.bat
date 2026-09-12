@echo off
title Stopping Flux RP...
echo [Flux RP] Terminating FXServer and MariaDB gracefully...
taskkill /F /IM FXServer.exe >nul 2>&1
taskkill /F /IM mariadbd.exe >nul 2>&1
echo [Flux RP] Server processes stopped.
pause
