#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/autovideosrt}"
SERVICE_NAME="${SERVICE_NAME:-autovideosrt-tabcut-daily-selection}"
DESKTOP_USER="${DESKTOP_USER:-cjh}"
DESKTOP_GROUP="${DESKTOP_GROUP:-cjh}"
OUTPUT_DIR="${TABCUT_OUTPUT_DIR:-/data/autovideosrt/tabcut/daily}"
ENV_FILE="/etc/default/${SERVICE_NAME}"

if [[ ! -d "$APP_DIR" ]]; then
  echo "APP_DIR does not exist: $APP_DIR" >&2
  exit 1
fi

install -d -m 755 -o "$DESKTOP_USER" -g "$DESKTOP_GROUP" "$OUTPUT_DIR"

if [[ ! -f "$ENV_FILE" ]]; then
  cat >"$ENV_FILE" <<EOF
TABCUT_CDP_URL=http://127.0.0.1:9227
TABCUT_OUTPUT_DIR=$OUTPUT_DIR
EOF
fi

install -m 644 "$APP_DIR/deploy/server_browser/${SERVICE_NAME}.service" "/etc/systemd/system/${SERVICE_NAME}.service"
install -m 644 "$APP_DIR/deploy/server_browser/${SERVICE_NAME}.timer" "/etc/systemd/system/${SERVICE_NAME}.timer"
chmod 755 "$APP_DIR/deploy/server_browser/install_tabcut_daily_selection_timer.sh"

systemctl disable --now tabcut-daily-selection.timer tabcut-daily-selection.service 2>/dev/null || true
rm -f /etc/systemd/system/tabcut-daily-selection.timer /etc/systemd/system/tabcut-daily-selection.service

systemctl daemon-reload
systemctl enable --now autovideosrt-tabcut-vnc.service
systemctl enable --now "${SERVICE_NAME}.timer"

systemctl list-timers "${SERVICE_NAME}.timer" --no-pager
