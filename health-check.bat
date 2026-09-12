@echo off
title Flux RP Server Health Check
echo ======================================================================
echo                     FLUX RP SYSTEM HEALTH MONITOR
echo ======================================================================
echo.
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$db = Test-NetConnection -ComputerName 127.0.0.1 -Port 3306 -InformationLevel Quiet; " ^
  "$fx = Test-NetConnection -ComputerName 127.0.0.1 -Port 30120 -InformationLevel Quiet; " ^
  "Write-Host 'MariaDB Port 3306: ' -NoNewline; if ($db) { Write-Host 'ONLINE' -ForegroundColor Green } else { Write-Host 'OFFLINE' -ForegroundColor Red }; " ^
  "Write-Host 'FXServer Port 30120: ' -NoNewline; if ($fx) { Write-Host 'ONLINE' -ForegroundColor Green } else { Write-Host 'OFFLINE' -ForegroundColor Red }; "
echo.
pause
