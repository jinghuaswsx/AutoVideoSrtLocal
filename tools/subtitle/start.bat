@echo off
REM Subtitle Remover API - Windows startup script
cd /d G:\subtitle
echo Starting Subtitle Remover API on port 82...
G:\subtitle\Python\python.exe G:\subtitle\api_server.py
