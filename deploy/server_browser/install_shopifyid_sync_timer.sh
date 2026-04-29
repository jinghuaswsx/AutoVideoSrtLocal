#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/autovideosrt}"
SERVICE_NAME="${SERVICE_NAME:-autovideosrt-shopifyid-sync}"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
TIMER_FILE="/etc/systemd/system/${SERVICE_NAME}.timer"

if [[ ! -d "$APP_DIR" ]]; then
  echo "APP_DIR does not exist: $APP_DIR" >&2
  exit 1
fi

install -d -m 755 /data/autovideosrt/browser/runtime
chmod 755 "$APP_DIR/deploy/server_browser/with_browser_lock.sh"
install -m 644 "$APP_DIR/deploy/server_browser/${SERVICE_NAME}.service" "$SERVICE_FILE"
install -m 644 "$APP_DIR/deploy/server_browser/${SERVICE_NAME}.timer" "$TIMER_FILE"

systemctl daemon-reload
systemctl enable --now "${SERVICE_NAME}.timer"

echo "[shopifyid-sync] timer status"
systemctl list-timers "${SERVICE_NAME}.timer" --no-pager
echo
echo "[shopifyid-sync] install done"
