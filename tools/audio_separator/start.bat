@echo off
REM Audio Separator API - Windows startup script (port 8081, prefix /separate)
REM Public URL goes through Caddy gateway at http://172.30.254.12/separate/*
cd /d G:\audio
echo Starting Audio Separator API on internal port 8081...
G:\audio\venv312\Scripts\python.exe G:\audio\api_server.py
