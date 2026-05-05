@echo off
REM Subtitle Remover API - Windows startup script (port 8082, prefix /subtitle)
REM Public URL goes through Caddy gateway at http://172.30.254.12/subtitle/*
cd /d G:\subtitle
echo Starting Subtitle Remover API on internal port 8082...
G:\subtitle\Python\python.exe G:\subtitle\api_server.py
