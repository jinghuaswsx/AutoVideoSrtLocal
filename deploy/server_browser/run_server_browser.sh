#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/autovideosrt}"
VENV_DIR="${VENV_DIR:-$APP_DIR/venv}"
PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-$APP_DIR/.playwright-browsers}"
DISPLAY_NUM="${BROWSER_DISPLAY:-:20}"
SCREEN_SIZE="${BROWSER_SCREEN_SIZE:-1600x1000x24}"
PROFILE_DIR="${BROWSER_PROFILE_DIR:-/data/autovideosrt/browser/profiles/shared}"
RUNTIME_DIR="${BROWSER_RUNTIME_DIR:-/data/autovideosrt/browser/runtime}"
LOG_DIR="${BROWSER_LOG_DIR:-/data/autovideosrt/browser/logs}"
XDG_RUNTIME_ROOT="${BROWSER_XDG_RUNTIME_DIR:-/tmp/autovideosrt-browser-xdg}"
START_URL="${BROWSER_START_URL:-https://www.dianxiaomi.com/web/shopifyProduct/online}"
CDP_HOST="${BROWSER_CDP_HOST:-127.0.0.1}"
CDP_PORT="${BROWSER_CDP_PORT:-9222}"
VNC_HOST="${BROWSER_VNC_HOST:-127.0.0.1}"
VNC_PORT="${BROWSER_VNC_PORT:-5901}"
NOVNC_HOST="${BROWSER_NOVNC_HOST:-127.0.0.1}"
NOVNC_PORT="${BROWSER_NOVNC_PORT:-6080}"
WINDOW_SIZE="${BROWSER_WINDOW_SIZE:-1440,900}"

mkdir -p "$PROFILE_DIR" "$RUNTIME_DIR" "$LOG_DIR" "$XDG_RUNTIME_ROOT"
chmod 700 "$XDG_RUNTIME_ROOT"

export DISPLAY="$DISPLAY_NUM"
export XDG_RUNTIME_DIR="$XDG_RUNTIME_ROOT"
export PLAYWRIGHT_BROWSERS_PATH

XVFB_LOG="$LOG_DIR/xvfb.log"
OPENBOX_LOG="$LOG_DIR/openbox.log"
VNC_LOG="$LOG_DIR/x11vnc.log"
NOVNC_LOG="$LOG_DIR/novnc.log"
CHROME_LOG="$LOG_DIR/chromium.log"

XVFB_PID=""
OPENBOX_PID=""
VNC_PID=""
NOVNC_PID=""
CHROME_PID=""
DBUS_PID=""

cleanup() {
  for pid in "$CHROME_PID" "$NOVNC_PID" "$VNC_PID" "$OPENBOX_PID" "$XVFB_PID" "$DBUS_PID"; do
    if [[ -n "${pid:-}" ]] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
      wait "$pid" 2>/dev/null || true
    fi
  done
}
trap cleanup EXIT INT TERM

if command -v dbus-launch >/dev/null 2>&1; then
  eval "$(dbus-launch --sh-syntax)"
  DBUS_PID="${DBUS_SESSION_BUS_PID:-}"
fi

resolve_chromium_path() {
  if command -v google-chrome-stable >/dev/null 2>&1; then
    command -v google-chrome-stable
    return 0
  fi
  if command -v google-chrome >/dev/null 2>&1; then
    command -v google-chrome
    return 0
  fi
  if command -v chromium >/dev/null 2>&1; then
    command -v chromium
    return 0
  fi
  if command -v chromium-browser >/dev/null 2>&1; then
    command -v chromium-browser
    return 0
  fi

  "$VENV_DIR/bin/python" - <<'PY'
from playwright.sync_api import sync_playwright

with sync_playwright() as playwright:
    print(playwright.chromium.executable_path)
PY
}

CHROMIUM_BIN="$(resolve_chromium_path)"
if [[ -z "$CHROMIUM_BIN" || ! -x "$CHROMIUM_BIN" ]]; then
  echo "Chromium executable was not found: $CHROMIUM_BIN" >&2
  exit 1
fi

DISPLAY_LOCK="/tmp/.X${DISPLAY_NUM#:}-lock"
rm -f "$DISPLAY_LOCK"

Xvfb "$DISPLAY" -screen 0 "$SCREEN_SIZE" -nolisten tcp >"$XVFB_LOG" 2>&1 &
XVFB_PID=$!
sleep 1

openbox >"$OPENBOX_LOG" 2>&1 &
OPENBOX_PID=$!
sleep 1

x11vnc \
  -display "$DISPLAY" \
  -rfbport "$VNC_PORT" \
  -listen "$VNC_HOST" \
  -forever \
  -shared \
  -nopw \
  -xkb \
  >"$VNC_LOG" 2>&1 &
VNC_PID=$!
sleep 1

websockify \
  --web=/usr/share/novnc/ \
  "${NOVNC_HOST}:${NOVNC_PORT}" \
  "${VNC_HOST}:${VNC_PORT}" \
  >"$NOVNC_LOG" 2>&1 &
NOVNC_PID=$!
sleep 1

"$CHROMIUM_BIN" \
  --user-data-dir="$PROFILE_DIR" \
  --remote-debugging-address="$CDP_HOST" \
  --remote-debugging-port="$CDP_PORT" \
  --no-first-run \
  --no-default-browser-check \
  --disable-gpu \
  --disable-dev-shm-usage \
  --password-store=basic \
  --window-size="$WINDOW_SIZE" \
  --start-maximized \
  --no-sandbox \
  "$START_URL" \
  >"$CHROME_LOG" 2>&1 &
CHROME_PID=$!

wait "$CHROME_PID"
