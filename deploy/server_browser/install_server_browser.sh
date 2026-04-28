#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/autovideosrt}"
VENV_DIR="${VENV_DIR:-$APP_DIR/venv}"
PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-$APP_DIR/.playwright-browsers}"
SERVICE_NAME="${SERVICE_NAME:-autovideosrt-browser}"
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

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y \
  dbus-x11 \
  fonts-noto-cjk \
  fonts-liberation

source "$VENV_DIR/bin/activate"
python -m pip install -r requirements-browser.txt -i https://pypi.org/simple/
PLAYWRIGHT_BROWSERS_PATH="$PLAYWRIGHT_BROWSERS_PATH" python -m playwright install chromium

install -d -m 755 -o "$DESKTOP_USER" -g "$DESKTOP_GROUP" /data/autovideosrt/browser/profiles/shared
install -d -m 755 -o "$DESKTOP_USER" -g "$DESKTOP_GROUP" /data/autovideosrt/browser/runtime
install -d -m 755 -o "$DESKTOP_USER" -g "$DESKTOP_GROUP" /data/autovideosrt/browser/logs
install -d -m 755 "$PLAYWRIGHT_BROWSERS_PATH"

# Take ownership of any pre-existing root-owned content from the legacy Xvfb era.
chown -R "$DESKTOP_USER:$DESKTOP_GROUP" /data/autovideosrt/browser/profiles/shared
chown -R "$DESKTOP_USER:$DESKTOP_GROUP" /data/autovideosrt/browser/runtime
chown -R "$DESKTOP_USER:$DESKTOP_GROUP" /data/autovideosrt/browser/logs

# Always rewrite env file (legacy file from Xvfb era contains stale BROWSER_DISPLAY/VNC/NOVNC keys).
cat >"$ENV_FILE" <<'EOF'
BROWSER_PROFILE_DIR=/data/autovideosrt/browser/profiles/shared
BROWSER_RUNTIME_DIR=/data/autovideosrt/browser/runtime
BROWSER_LOG_DIR=/data/autovideosrt/browser/logs
BROWSER_START_URL=https://www.dianxiaomi.com/web/shopifyProduct/online
BROWSER_CDP_HOST=127.0.0.1
BROWSER_CDP_PORT=9222
BROWSER_WINDOW_SIZE=1440,900
EOF

install -m 644 "deploy/server_browser/autovideosrt-browser.service" "$SERVICE_FILE"
chmod 755 "deploy/server_browser/run_server_browser.sh"
chmod 755 "deploy/server_browser/install_server_browser.sh"
chmod 755 "deploy/server_browser/install_shopifyid_sync_timer.sh" 2>/dev/null || true

systemctl daemon-reload
systemctl enable --now "$SERVICE_NAME"

probe_until_ready() {
  local label="$1"
  local url="$2"
  local probe_cmd="$3"
  local attempt

  echo "[browser] waiting for ${label}: ${url}"
  for ((attempt = 1; attempt <= PROBE_RETRIES; attempt++)); do
    if eval "$probe_cmd" >/tmp/${SERVICE_NAME}-${label}.probe 2>&1; then
      cat /tmp/${SERVICE_NAME}-${label}.probe
      rm -f /tmp/${SERVICE_NAME}-${label}.probe
      return 0
    fi
    echo "  attempt ${attempt}/${PROBE_RETRIES} not ready yet"
    sleep "$PROBE_SLEEP_SECONDS"
  done

  echo "[browser] ${label} probe failed after ${PROBE_RETRIES} attempts" >&2
  cat /tmp/${SERVICE_NAME}-${label}.probe >&2 || true
  rm -f /tmp/${SERVICE_NAME}-${label}.probe
  return 1
}

echo "[browser] service status"
systemctl status "$SERVICE_NAME" --no-pager -l | sed -n '1,60p'

echo "[browser] CDP probe"
probe_until_ready \
  "cdp" \
  "http://127.0.0.1:9222/json/version" \
  "curl -fsS http://127.0.0.1:9222/json/version"
echo

echo "[browser] install done"
echo "View Chromium via Sunlogin (cjh desktop). CDP tunnel only:"
echo "  ssh -L 9222:127.0.0.1:9222 root@$(hostname -I | awk '{print $1}')"
