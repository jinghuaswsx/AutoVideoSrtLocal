@echo off
chcp 65001 >nul
cd /d %~dp0\..
python tools\shopifyid_dianxiaomi_sync.py %*
pause
