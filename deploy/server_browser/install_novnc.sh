#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/autovideosrt}"
SERVICE_NAME="${SERVICE_NAME:-autovideosrt-novnc}"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

if [[ ! -d "$APP_DIR" ]]; then
  echo "APP_DIR does not exist: $APP_DIR" >&2
  exit 1
fi

cd "$APP_DIR"

export DEBIAN_FRONTEND=noninteractive
apt-get update
# novnc package brings the static HTML5 client at /usr/share/novnc;
# websockify is the WebSocket-to-VNC proxy. Both are in Ubuntu main.
apt-get install -y novnc websockify

if [[ ! -f "/usr/share/novnc/vnc.html" ]]; then
  echo "noVNC web assets not found at /usr/share/novnc/vnc.html" >&2
  exit 1
fi

# Stop any ad-hoc websockify instance that may have been started manually so
# the systemd unit can take ownership of port 6082 cleanly.
pkill -f 'websockify.*6082' 2>/dev/null || true
sleep 1

install -m 644 "deploy/server_browser/autovideosrt-novnc.service" "$SERVICE_FILE"
systemctl daemon-reload
systemctl enable --now "$SERVICE_NAME"

sleep 2

echo "[novnc] service status"
systemctl status "$SERVICE_NAME" --no-pager -l | sed -n '1,30p' || true

echo "[novnc] HTTP probe"
curl -fsS -o /dev/null -w "http_code=%{http_code}\n" "http://127.0.0.1:6082/vnc.html"

echo
echo "[novnc] install done"
echo "Local browser entry (LAN-internal):"
echo "  http://172.30.254.14:6082/vnc.html?host=172.30.254.14&port=6082&autoconnect=true&resize=remote"
