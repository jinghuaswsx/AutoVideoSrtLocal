@echo off
cd /d %~dp0\..
python tools\wedev_sync.py %*
pause
