#!/usr/bin/env bash
set -euo pipefail

DXM_NAME="${DXM_NAME:?DXM_NAME is required}"
DISPLAY_NUM="${DXM_DISPLAY_NUM:?DXM_DISPLAY_NUM is required}"
DISPLAY=":${DISPLAY_NUM}"
PROFILE_DIR="${DXM_PROFILE_DIR:?DXM_PROFILE_DIR is required}"
RUNTIME_DIR="${DXM_RUNTIME_DIR:?DXM_RUNTIME_DIR is required}"
LOG_DIR="${DXM_LOG_DIR:?DXM_LOG_DIR is required}"
CDP_PORT="${DXM_CDP_PORT:?DXM_CDP_PORT is required}"
VNC_PORT="${DXM_VNC_PORT:?DXM_VNC_PORT is required}"
NOVNC_PORT="${DXM_NOVNC_PORT:?DXM_NOVNC_PORT is required}"
START_URL="${DXM_START_URL:?DXM_START_URL is required}"
WINDOW_SIZE="${DXM_WINDOW_SIZE:-1500,920}"
SCREEN_SIZE="${DXM_SCREEN_SIZE:-1600x1000x24}"
NOVNC_WEB_DIR="${DXM_NOVNC_WEB_DIR:-/usr/share/novnc}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NOVNC_RUNTIME_WEB_DIR="${RUNTIME_DIR}/novnc-web"
NOVNC_PASTE_BRIDGE_SRC="${SCRIPT_DIR}/novnc_paste_bridge.js"

mkdir -p "$PROFILE_DIR" "$RUNTIME_DIR" "$LOG_DIR"

prepare_novnc_web_dir() {
  local tmp_dir="${NOVNC_RUNTIME_WEB_DIR}.tmp.$$"
  rm -rf "$tmp_dir"
  mkdir -p "$tmp_dir"
  cp -a "${NOVNC_WEB_DIR}/." "$tmp_dir/"
  install -m 0644 "$NOVNC_PASTE_BRIDGE_SRC" "$tmp_dir/app/novnc_paste_bridge.js"
  if ! grep -q "novnc_paste_bridge.js" "$tmp_dir/vnc.html"; then
    if grep -q '<script type="module" crossorigin="anonymous" src="app/ui.js"></script>' "$tmp_dir/vnc.html"; then
      sed -i '/<script type="module" crossorigin="anonymous" src="app\/ui.js"><\/script>/a\    <script type="module" crossorigin="anonymous" src="app/novnc_paste_bridge.js"></script>' "$tmp_dir/vnc.html"
    else
      sed -i '/<\/head>/i\    <script type="module" crossorigin="anonymous" src="app/novnc_paste_bridge.js"></script>' "$tmp_dir/vnc.html"
    fi
  fi
  rm -rf "$NOVNC_RUNTIME_WEB_DIR"
  mv "$tmp_dir" "$NOVNC_RUNTIME_WEB_DIR"
}

cleanup() {
  pkill -P $$ 2>/dev/null || true
  rm -f "/tmp/.X${DISPLAY_NUM}-lock" "/tmp/.X11-unix/X${DISPLAY_NUM}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

pkill -f "Xvfb ${DISPLAY}" 2>/dev/null || true
pkill -f "x11vnc.*${DISPLAY}" 2>/dev/null || true
pkill -f "websockify.*${NOVNC_PORT}" 2>/dev/null || true
pkill -f "user-data-dir=${PROFILE_DIR}" 2>/dev/null || true
sleep 1
rm -f "/tmp/.X${DISPLAY_NUM}-lock" "/tmp/.X11-unix/X${DISPLAY_NUM}" 2>/dev/null || true
rm -f "$PROFILE_DIR/SingletonLock" "$PROFILE_DIR/SingletonSocket" "$PROFILE_DIR/SingletonCookie" 2>/dev/null || true

Xvfb "$DISPLAY" -screen 0 "$SCREEN_SIZE" -ac +extension RANDR >"$LOG_DIR/xvfb.log" 2>&1 &
echo $! >"$RUNTIME_DIR/xvfb.pid"
for _ in {1..50}; do
  [[ -S "/tmp/.X11-unix/X${DISPLAY_NUM}" ]] && break
  sleep 0.2
done
if [[ ! -S "/tmp/.X11-unix/X${DISPLAY_NUM}" ]]; then
  echo "${DXM_NAME} Xvfb display ${DISPLAY} did not become ready" >&2
  exit 1
fi

export DISPLAY
export HOME=/home/cjh
export XDG_RUNTIME_DIR=/run/user/1000
export DBUS_SESSION_BUS_ADDRESS=disabled:
openbox >"$LOG_DIR/openbox.log" 2>&1 &
echo $! >"$RUNTIME_DIR/openbox.pid"

prepare_novnc_web_dir

google-chrome-stable \
  --user-data-dir="$PROFILE_DIR" \
  --remote-debugging-address=127.0.0.1 \
  --remote-debugging-port="$CDP_PORT" \
  --no-first-run \
  --no-default-browser-check \
  --disable-dev-shm-usage \
  --password-store=basic \
  --window-size="$WINDOW_SIZE" \
  "$START_URL" >"$LOG_DIR/chrome.log" 2>&1 &
echo $! >"$RUNTIME_DIR/chrome.pid"

x11vnc -display "$DISPLAY" -listen 127.0.0.1 -rfbport "$VNC_PORT" -rfbportv6 0 -forever -shared -nopw -noxdamage -no6 -noipv6 >"$LOG_DIR/x11vnc.log" 2>&1 &
echo $! >"$RUNTIME_DIR/x11vnc.pid"
websockify --web="$NOVNC_RUNTIME_WEB_DIR" "0.0.0.0:${NOVNC_PORT}" "127.0.0.1:${VNC_PORT}" >"$LOG_DIR/websockify.log" 2>&1 &
echo $! >"$RUNTIME_DIR/websockify.pid"

for i in {1..60}; do
  if curl -fsS "http://127.0.0.1:${CDP_PORT}/json/version" >/dev/null 2>&1 && curl -fsS -o /dev/null "http://127.0.0.1:${NOVNC_PORT}/vnc.html"; then
    echo "${DXM_NAME} ready"
    echo "CDP=http://127.0.0.1:${CDP_PORT}"
    echo "noVNC=http://127.0.0.1:${NOVNC_PORT}/vnc.html?host=127.0.0.1&port=${NOVNC_PORT}&autoconnect=true&resize=remote"
    break
  fi
  sleep 1
  if [[ "$i" == 60 ]]; then
    echo "${DXM_NAME} failed to become ready" >&2
    exit 1
  fi
done

wait -n
