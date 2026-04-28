#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/autovideosrt}"
VENV_DIR="${VENV_DIR:-$APP_DIR/venv}"
PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-$APP_DIR/.playwright-browsers}"
PROFILE_DIR="${BROWSER_PROFILE_DIR:-/data/autovideosrt/browser/profiles/shared}"
RUNTIME_DIR="${BROWSER_RUNTIME_DIR:-/data/autovideosrt/browser/runtime}"
LOG_DIR="${BROWSER_LOG_DIR:-/data/autovideosrt/browser/logs}"
START_URL="${BROWSER_START_URL:-https://www.dianxiaomi.com/web/shopifyProduct/online}"
CDP_HOST="${BROWSER_CDP_HOST:-127.0.0.1}"
CDP_PORT="${BROWSER_CDP_PORT:-9222}"
WINDOW_SIZE="${BROWSER_WINDOW_SIZE:-1440,900}"

mkdir -p "$PROFILE_DIR" "$RUNTIME_DIR" "$LOG_DIR"

export PLAYWRIGHT_BROWSERS_PATH

CHROME_LOG="$LOG_DIR/chromium.log"

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

if [[ -z "${DISPLAY:-}" ]]; then
  echo "DISPLAY is not set; cannot launch Chromium on real desktop." >&2
  exit 1
fi

exec "$CHROMIUM_BIN" \
  --user-data-dir="$PROFILE_DIR" \
  --remote-debugging-address="$CDP_HOST" \
  --remote-debugging-port="$CDP_PORT" \
  --no-first-run \
  --no-default-browser-check \
  --disable-dev-shm-usage \
  --password-store=basic \
  --window-size="$WINDOW_SIZE" \
  --start-maximized \
  "$START_URL" \
  >"$CHROME_LOG" 2>&1
