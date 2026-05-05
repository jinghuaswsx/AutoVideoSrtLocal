@echo off
REM GPU services gateway (Caddy reverse proxy on port 80)
REM Routes /separate/* -> :8081, /subtitle/* -> :8082, /vace/* -> :8083 (reserved)
cd /d G:\gateway
echo Starting Caddy gateway on port 80...
G:\gateway\caddy.exe run --config G:\gateway\Caddyfile
