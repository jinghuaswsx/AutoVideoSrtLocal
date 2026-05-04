#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/autovideosrt}"
VENV_DIR="${VENV_DIR:-$APP_DIR/venv}"
PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-$APP_DIR/.playwright-browsers}"
SERVICE_NAME="${SERVICE_NAME:-autovideosrt-xmyc-browser}"
ENV_FILE="/etc/default/${SERVICE_NAME}"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
PROBE_RETRIES="${PROBE_RETRIES:-20}"
PROBE_SLEEP_SECONDS="${PROBE_SLEEP_SECONDS:-2}"
DESKTOP_USER="${DESKTOP_USER:-cjh}"
DESKTOP_GROUP="${DESKTOP_GROUP:-cjh}"

if [[ ! -d "$APP_DIR" ]]; then
  echo "APP_DIR does not exist: $APP_DIR" >&2
  exit 1
fi

cd "$APP_DIR"

install -d -m 755 -o "$DESKTOP_USER" -g "$DESKTOP_GROUP" /data/autovideosrt/browser/profiles/xmyc-storage
install -d -m 755 -o "$DESKTOP_USER" -g "$DESKTOP_GROUP" /data/autovideosrt/browser/runtime-xmyc-storage
install -d -m 755 -o "$DESKTOP_USER" -g "$DESKTOP_GROUP" /data/autovideosrt/browser/logs/xmyc-storage

chown -R "$DESKTOP_USER:$DESKTOP_GROUP" /data/autovideosrt/browser/profiles/xmyc-storage
chown -R "$DESKTOP_USER:$DESKTOP_GROUP" /data/autovideosrt/browser/runtime-xmyc-storage
chown -R "$DESKTOP_USER:$DESKTOP_GROUP" /data/autovideosrt/browser/logs/xmyc-storage

cat >"$ENV_FILE" <<'EOF'
BROWSER_PROFILE_DIR=/data/autovideosrt/browser/profiles/xmyc-storage
BROWSER_RUNTIME_DIR=/data/autovideosrt/browser/runtime-xmyc-storage
BROWSER_LOG_DIR=/data/autovideosrt/browser/logs/xmyc-storage
BROWSER_START_URL=https://www.xmyc.com/storage/index.htm?indexType=1
BROWSER_CDP_HOST=127.0.0.1
BROWSER_CDP_PORT=9224
BROWSER_WINDOW_SIZE=1440,900
EOF

install -m 644 "deploy/server_browser/autovideosrt-xmyc-browser.service" "$SERVICE_FILE"
chmod 755 "deploy/server_browser/run_server_browser.sh"
chmod 755 "deploy/server_browser/install_xmyc_browser.sh"

systemctl daemon-reload
systemctl enable --now "$SERVICE_NAME"

echo "[xmyc-browser] service status"
systemctl status "$SERVICE_NAME" --no-pager -l | sed -n '1,40p' || true

echo "[xmyc-browser] CDP probe"
for ((attempt = 1; attempt <= PROBE_RETRIES; attempt++)); do
  if curl -fsS http://127.0.0.1:9224/json/version >/dev/null 2>&1; then
    curl -s http://127.0.0.1:9224/json/version
    echo
    echo "[xmyc-browser] install done"
    exit 0
  fi
  echo "  attempt ${attempt}/${PROBE_RETRIES} not ready yet"
  sleep "$PROBE_SLEEP_SECONDS"
done

echo "[xmyc-browser] CDP probe failed after ${PROBE_RETRIES} attempts" >&2
exit 1
