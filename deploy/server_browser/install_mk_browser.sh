#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/autovideosrt}"
VENV_DIR="${VENV_DIR:-$APP_DIR/venv}"
PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-$APP_DIR/.playwright-browsers}"
SERVICE_NAME="${SERVICE_NAME:-autovideosrt-mk-browser}"
ENV_FILE="/etc/default/${SERVICE_NAME}"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
PROBE_RETRIES="${PROBE_RETRIES:-20}"
PROBE_SLEEP_SECONDS="${PROBE_SLEEP_SECONDS:-2}"

if [[ ! -d "$APP_DIR" ]]; then
  echo "APP_DIR does not exist: $APP_DIR" >&2
  exit 1
fi

cd "$APP_DIR"

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y \
  xvfb \
  x11vnc \
  novnc \
  websockify \
  openbox \
  dbus-x11 \
  fonts-noto-cjk \
  fonts-liberation

source "$VENV_DIR/bin/activate"
python -m pip install -r requirements-browser.txt -i https://pypi.org/simple/
PLAYWRIGHT_BROWSERS_PATH="$PLAYWRIGHT_BROWSERS_PATH" python -m playwright install chromium

install -d -m 755 /data/autovideosrt/browser/profiles/mk-selection
install -d -m 755 /data/autovideosrt/browser/runtime-mk-selection
install -d -m 755 /data/autovideosrt/browser/logs/mk-selection
install -d -m 755 "$PLAYWRIGHT_BROWSERS_PATH"

if [[ ! -f "$ENV_FILE" ]]; then
  cat >"$ENV_FILE" <<'EOF'
BROWSER_DISPLAY=:100
BROWSER_SCREEN_SIZE=1600x1000x24
BROWSER_PROFILE_DIR=/data/autovideosrt/browser/profiles/mk-selection
BROWSER_RUNTIME_DIR=/data/autovideosrt/browser/runtime-mk-selection
BROWSER_LOG_DIR=/data/autovideosrt/browser/logs/mk-selection
BROWSER_XDG_RUNTIME_DIR=/tmp/autovideosrt-mk-browser-xdg
BROWSER_START_URL=https://www.dianxiaomi.com/web/stat/salesStatistics
BROWSER_CDP_HOST=127.0.0.1
BROWSER_CDP_PORT=9223
BROWSER_VNC_HOST=127.0.0.1
BROWSER_VNC_PORT=5902
BROWSER_NOVNC_HOST=127.0.0.1
BROWSER_NOVNC_PORT=6081
BROWSER_WINDOW_SIZE=1440,900
EOF
fi

install -m 644 "deploy/server_browser/autovideosrt-mk-browser.service" "$SERVICE_FILE"
chmod 755 "deploy/server_browser/run_server_browser.sh"
chmod 755 "deploy/server_browser/install_mk_browser.sh"

systemctl daemon-reload
systemctl enable --now "$SERVICE_NAME"

probe_until_ready() {
  local label="$1"
  local url="$2"
  local probe_cmd="$3"
  local attempt

  echo "[mk-browser] waiting for ${label}: ${url}"
  for ((attempt = 1; attempt <= PROBE_RETRIES; attempt++)); do
    if eval "$probe_cmd" >/tmp/${SERVICE_NAME}-${label}.probe 2>&1; then
      cat /tmp/${SERVICE_NAME}-${label}.probe
      rm -f /tmp/${SERVICE_NAME}-${label}.probe
      return 0
    fi
    echo "  attempt ${attempt}/${PROBE_RETRIES} not ready yet"
    sleep "$PROBE_SLEEP_SECONDS"
  done

  echo "[mk-browser] ${label} probe failed after ${PROBE_RETRIES} attempts" >&2
  cat /tmp/${SERVICE_NAME}-${label}.probe >&2 || true
  rm -f /tmp/${SERVICE_NAME}-${label}.probe
  return 1
}

echo "[mk-browser] service status"
systemctl status "$SERVICE_NAME" --no-pager -l | sed -n '1,60p'

echo "[mk-browser] CDP probe"
probe_until_ready \
  "cdp" \
  "http://127.0.0.1:9223/json/version" \
  "curl -fsS http://127.0.0.1:9223/json/version"
echo

echo "[mk-browser] noVNC probe"
probe_until_ready \
  "novnc" \
  "http://127.0.0.1:6081/vnc.html" \
  "curl -I -fsS http://127.0.0.1:6081/vnc.html | sed -n '1,10p'"
echo

echo "[mk-browser] install done"
echo "Use SSH tunnel for noVNC: ssh -L 6081:127.0.0.1:6081 -L 9223:127.0.0.1:9223 root@$(hostname -I | awk '{print $1}')"
