@echo off
REM Audio Separator API - Windows startup script
cd /d G:\audio
echo Starting Audio Separator API on port 80...
G:\audio\venv312\Scripts\python.exe G:\audio\api_server.py
