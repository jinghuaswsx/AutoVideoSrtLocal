# Server Browser → Real Desktop Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move two systemd-managed Chromium services (`autovideosrt-browser`, `autovideosrt-mk-browser`) from Xvfb/x11vnc/noVNC stack onto the real KDE Plasma desktop that GDM3 auto-logs in for cjh on `:0`, so the user can see and intervene through Sunlogin.

**Architecture:**
- systemd units gain `User=cjh`, `After=graphical.target`, and `DISPLAY=:0` / `XAUTHORITY=/run/user/1000/gdm/Xauthority` / `XDG_RUNTIME_DIR=/run/user/1000` environment.
- `run_server_browser.sh` drops the Xvfb / openbox / x11vnc / websockify chain; only Chromium remains (X11 socket comes from the real session).
- env files, install scripts, and tunnel helpers shed VNC/noVNC ports and surfaces.
- Profile dirs are chowned to `cjh:cjh` so cjh-owned Chromium can read/write them.
- CDP port (9222 / 9223), profile path, and CDP API are unchanged — callers (`shopifyid_dianxiaomi_sync.py`, `mk_import.py`-adjacent flows) need zero code changes.

**Tech Stack:** systemd unit files, bash deploy scripts, /etc/default env files, PowerShell tunnel scripts, Markdown docs. Linux side: KDE Plasma X11 (already running), GDM3 auto-login (already configured by Codex).

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `deploy/server_browser/autovideosrt-browser.service` | modify | systemd unit for shared profile (店小秘 Shopify ID 同步) |
| `deploy/server_browser/autovideosrt-mk-browser.service` | modify | systemd unit for mk-selection profile (明空选品) |
| `deploy/server_browser/run_server_browser.sh` | modify | Chromium-only launcher (Xvfb/openbox/x11vnc/websockify removed) |
| `deploy/server_browser/install_server_browser.sh` | modify | Install script for shared service (apt deps trimmed, chown added) |
| `deploy/server_browser/install_mk_browser.sh` | modify | Install script for mk service (apt deps trimmed, chown added) |
| `tools/open_server_browser_tunnel.ps1` | modify | SSH tunnel — drop noVNC, keep CDP only |
| `tools/open_mk_server_browser_tunnel.ps1` | modify | SSH tunnel — drop noVNC, keep CDP only |
| `docs/server_browser_runtime.md` | modify | Doc rewrite — describe Sunlogin-based access |
| `docs/superpowers/specs/2026-04-28-server-browser-real-desktop-design.md` | unchanged | Design spec (already drafted) |

No new files. No tests added (this is infrastructure — verification is live deploy + 4-step probe).

---

## Task 1: Modify shared-profile systemd unit

**Files:**
- Modify: `deploy/server_browser/autovideosrt-browser.service`

- [ ] **Step 1: Replace unit body**

Replace entire content of `deploy/server_browser/autovideosrt-browser.service` with:

```ini
[Unit]
Description=AutoVideoSrt Shared Browser Runtime
After=graphical.target network-online.target
Wants=graphical.target network-online.target

[Service]
Type=simple
User=cjh
Group=cjh
WorkingDirectory=/opt/autovideosrt
Environment="PATH=/opt/autovideosrt/venv/bin:/usr/bin:/usr/local/bin:/bin"
Environment="PYTHONUNBUFFERED=1"
Environment="APP_DIR=/opt/autovideosrt"
Environment="VENV_DIR=/opt/autovideosrt/venv"
Environment="PLAYWRIGHT_BROWSERS_PATH=/opt/autovideosrt/.playwright-browsers"
Environment="DISPLAY=:0"
Environment="XAUTHORITY=/run/user/1000/gdm/Xauthority"
Environment="XDG_RUNTIME_DIR=/run/user/1000"
EnvironmentFile=-/etc/default/autovideosrt-browser
ExecStart=/opt/autovideosrt/deploy/server_browser/run_server_browser.sh
Restart=always
RestartSec=5
TimeoutStopSec=30

[Install]
WantedBy=graphical.target
```

- [ ] **Step 2: Lint check**

Run: `bash -n deploy/server_browser/autovideosrt-browser.service 2>&1 || true`

(Note: systemd unit files aren't bash; bash -n will report. Real validation happens on server in Task 8 via `systemd-analyze verify`.)

- [ ] **Step 3: Commit**

```bash
git add deploy/server_browser/autovideosrt-browser.service
git commit -m "refactor(server-browser): switch shared unit to cjh real desktop"
```

---

## Task 2: Modify mk-selection systemd unit

**Files:**
- Modify: `deploy/server_browser/autovideosrt-mk-browser.service`

- [ ] **Step 1: Replace unit body**

Replace entire content of `deploy/server_browser/autovideosrt-mk-browser.service` with:

```ini
[Unit]
Description=AutoVideoSrt MK Selection Isolated Browser Runtime
After=graphical.target network-online.target
Wants=graphical.target network-online.target

[Service]
Type=simple
User=cjh
Group=cjh
WorkingDirectory=/opt/autovideosrt
Environment="PATH=/opt/autovideosrt/venv/bin:/usr/bin:/usr/local/bin:/bin"
Environment="PYTHONUNBUFFERED=1"
Environment="APP_DIR=/opt/autovideosrt"
Environment="VENV_DIR=/opt/autovideosrt/venv"
Environment="PLAYWRIGHT_BROWSERS_PATH=/opt/autovideosrt/.playwright-browsers"
Environment="DISPLAY=:0"
Environment="XAUTHORITY=/run/user/1000/gdm/Xauthority"
Environment="XDG_RUNTIME_DIR=/run/user/1000"
EnvironmentFile=-/etc/default/autovideosrt-mk-browser
ExecStart=/opt/autovideosrt/deploy/server_browser/run_server_browser.sh
Restart=always
RestartSec=5
TimeoutStopSec=30

[Install]
WantedBy=graphical.target
```

- [ ] **Step 2: Commit**

```bash
git add deploy/server_browser/autovideosrt-mk-browser.service
git commit -m "refactor(server-browser): switch mk unit to cjh real desktop"
```

---

## Task 3: Slim run_server_browser.sh to Chromium-only

**Files:**
- Modify: `deploy/server_browser/run_server_browser.sh`

- [ ] **Step 1: Replace script body**

Replace entire content of `deploy/server_browser/run_server_browser.sh` with:

```bash
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
```

Notes for the engineer:
- `exec` replaces the shell with Chromium so systemd can track the right PID for `Restart=always`.
- Removed `--no-sandbox`, `--disable-gpu` — running as cjh on real X session, normal sandbox works.
- Removed `dbus-launch` — cjh's session already has a session bus via `/run/user/1000/bus`.
- Removed all Xvfb/openbox/x11vnc/websockify launches and the cleanup trap (no child processes to wait on now).
- DISPLAY guard prevents the script from silently running headless if env file/unit forgets to set DISPLAY.

- [ ] **Step 2: Local bash syntax check**

Run: `bash -n deploy/server_browser/run_server_browser.sh && echo OK`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add deploy/server_browser/run_server_browser.sh
git commit -m "refactor(server-browser): launcher only starts Chromium, X server is real desktop"
```

---

## Task 4: Slim install_server_browser.sh

**Files:**
- Modify: `deploy/server_browser/install_server_browser.sh`

- [ ] **Step 1: Replace install script body**

Replace entire content of `deploy/server_browser/install_server_browser.sh` with:

```bash
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
```

Notes:
- Removed apt installs of `xvfb x11vnc novnc websockify openbox`.
- Removed `BROWSER_DISPLAY/SCREEN_SIZE/VNC_HOST/VNC_PORT/NOVNC_HOST/NOVNC_PORT/XDG_RUNTIME_DIR` env keys (DISPLAY/XAUTHORITY/XDG_RUNTIME_DIR are now hardcoded in unit, not env file).
- Env file is rewritten unconditionally — legacy file with stale keys must be replaced.
- Profile/runtime/logs chowned to cjh:cjh; `install -d -o` sets owner on dir creation, then explicit `chown -R` re-owns any pre-existing legacy content.
- Removed noVNC probe; only CDP probe remains.
- Final hint message updated.

- [ ] **Step 2: Local bash syntax check**

Run: `bash -n deploy/server_browser/install_server_browser.sh && echo OK`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add deploy/server_browser/install_server_browser.sh
git commit -m "refactor(server-browser): drop Xvfb/x11vnc apt deps, chown profiles to cjh"
```

---

## Task 5: Slim install_mk_browser.sh

**Files:**
- Modify: `deploy/server_browser/install_mk_browser.sh`

- [ ] **Step 1: Replace install script body**

Replace entire content of `deploy/server_browser/install_mk_browser.sh` with:

```bash
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

install -d -m 755 -o "$DESKTOP_USER" -g "$DESKTOP_GROUP" /data/autovideosrt/browser/profiles/mk-selection
install -d -m 755 -o "$DESKTOP_USER" -g "$DESKTOP_GROUP" /data/autovideosrt/browser/runtime-mk-selection
install -d -m 755 -o "$DESKTOP_USER" -g "$DESKTOP_GROUP" /data/autovideosrt/browser/logs/mk-selection
install -d -m 755 "$PLAYWRIGHT_BROWSERS_PATH"

chown -R "$DESKTOP_USER:$DESKTOP_GROUP" /data/autovideosrt/browser/profiles/mk-selection
chown -R "$DESKTOP_USER:$DESKTOP_GROUP" /data/autovideosrt/browser/runtime-mk-selection
chown -R "$DESKTOP_USER:$DESKTOP_GROUP" /data/autovideosrt/browser/logs/mk-selection

cat >"$ENV_FILE" <<'EOF'
BROWSER_PROFILE_DIR=/data/autovideosrt/browser/profiles/mk-selection
BROWSER_RUNTIME_DIR=/data/autovideosrt/browser/runtime-mk-selection
BROWSER_LOG_DIR=/data/autovideosrt/browser/logs/mk-selection
BROWSER_START_URL=https://www.dianxiaomi.com/web/stat/salesStatistics
BROWSER_CDP_HOST=127.0.0.1
BROWSER_CDP_PORT=9223
BROWSER_WINDOW_SIZE=1440,900
EOF

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

echo "[mk-browser] install done"
echo "View Chromium via Sunlogin (cjh desktop). CDP tunnel only:"
echo "  ssh -L 9223:127.0.0.1:9223 root@$(hostname -I | awk '{print $1}')"
```

- [ ] **Step 2: Local bash syntax check**

Run: `bash -n deploy/server_browser/install_mk_browser.sh && echo OK`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add deploy/server_browser/install_mk_browser.sh
git commit -m "refactor(server-browser): drop Xvfb stack from mk install, chown to cjh"
```

---

## Task 6: Trim shared tunnel PowerShell script

**Files:**
- Modify: `tools/open_server_browser_tunnel.ps1`

- [ ] **Step 1: Replace script body**

Replace entire content of `tools/open_server_browser_tunnel.ps1` with:

```powershell
[CmdletBinding()]
param(
    [string]$ServerHost = "172.30.254.14",
    [string]$User = "root",
    [string]$KeyPath = "C:\Users\admin\.ssh\CC.pem",
    [int]$CdpPort = 9222
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $KeyPath)) {
    throw "SSH key was not found: $KeyPath"
}

$sshExe = (Get-Command ssh.exe -ErrorAction Stop).Source
$args = @(
    "-i", $KeyPath,
    "-o", "StrictHostKeyChecking=accept-new",
    "-o", "ExitOnForwardFailure=yes",
    "-o", "ServerAliveInterval=60",
    "-N",
    "-T",
    "-L", "$CdpPort`:127.0.0.1:9222",
    "$User@$ServerHost"
)

Write-Host "Opening SSH tunnel for shared browser CDP..." -ForegroundColor Green
Write-Host "CDP URL: http://127.0.0.1:$CdpPort/json/version"
Write-Host "Use Sunlogin to view the actual browser window on the cjh desktop."
Write-Host "Keep this window open while using the remote browser."

& $sshExe @args
```

Notes:
- Removed `$NoVncPort`, `$NoOpenBrowser` params, the noVNC `-L` forward, and the auto-launch of `vnc.html`.

- [ ] **Step 2: Commit**

```bash
git add tools/open_server_browser_tunnel.ps1
git commit -m "refactor(server-browser): tunnel forwards CDP only, Sunlogin replaces noVNC"
```

---

## Task 7: Trim mk tunnel PowerShell script

**Files:**
- Modify: `tools/open_mk_server_browser_tunnel.ps1`

- [ ] **Step 1: Replace script body**

Replace entire content of `tools/open_mk_server_browser_tunnel.ps1` with:

```powershell
[CmdletBinding()]
param(
    [string]$ServerHost = "172.30.254.14",
    [string]$User = "root",
    [string]$KeyPath = "C:\Users\admin\.ssh\CC.pem",
    [int]$CdpPort = 9223
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $KeyPath)) {
    throw "SSH key was not found: $KeyPath"
}

$sshExe = (Get-Command ssh.exe -ErrorAction Stop).Source
$args = @(
    "-i", $KeyPath,
    "-o", "StrictHostKeyChecking=accept-new",
    "-o", "ExitOnForwardFailure=yes",
    "-o", "ServerAliveInterval=60",
    "-N",
    "-T",
    "-L", "$CdpPort`:127.0.0.1:9223",
    "$User@$ServerHost"
)

Write-Host "Opening SSH tunnel for MK selection browser CDP..." -ForegroundColor Green
Write-Host "CDP URL: http://127.0.0.1:$CdpPort/json/version"
Write-Host "Use Sunlogin to view the actual browser window on the cjh desktop."
Write-Host "Keep this window open while using the remote browser."

& $sshExe @args
```

- [ ] **Step 2: Commit**

```bash
git add tools/open_mk_server_browser_tunnel.ps1
git commit -m "refactor(server-browser): mk tunnel forwards CDP only"
```

---

## Task 8: Rewrite docs/server_browser_runtime.md

**Files:**
- Modify: `docs/server_browser_runtime.md`

- [ ] **Step 1: Replace doc body**

Replace entire content of `docs/server_browser_runtime.md` with:

```markdown
# 服务端共享浏览器运行层

## 目标

给 Ubuntu Server 提供一套可复用的浏览器运行环境，用于：

- 店小秘后台抓取
- 明空网络登录态抓取
- Shopify 后台自动化
- 其他依赖浏览器登录态的模块

## 组成

- KDE Plasma X11 真桌面（GDM3 自动登录 cjh，由 Codex 维护）
- `Chromium`（Playwright Chromium）：跑在真桌面 `:0` 上，由 systemd 管理
- `CDP`：给自动化模块连接浏览器
- 向日葵远程桌面：用户介入入口（看页面、关弹窗、补登录）

## 端口

全部只监听服务器本机：

- `127.0.0.1:9222`：Shopify ID 同步使用的店小秘 Chromium CDP
- `127.0.0.1:9223`：明空选品店小秘 Chromium CDP

外部访问通过 SSH 隧道完成，不直接暴露公网。

## 共享登录态

统一使用一个共享浏览器 profile：

- `/data/autovideosrt/browser/profiles/shared`（owner：cjh）

后续不同模块只要复用这一个 profile，即可共用已经登录好的站点状态。

如果某个模块必须隔离账号、Cookie 或店铺上下文，应使用独立 profile。明空选品当前使用：

- `/data/autovideosrt/browser/profiles/mk-selection`（owner：cjh）

## 安装

服务器上执行：

```bash
cd /opt/autovideosrt
bash deploy/server_browser/install_server_browser.sh
bash deploy/server_browser/install_mk_browser.sh
```

两个脚本会：

- 装 `dbus-x11 fonts-noto-cjk fonts-liberation`（不再装 Xvfb / x11vnc / noVNC / openbox）
- 装 Playwright Chromium
- 把 profile / runtime / logs 目录 chown 给 cjh
- 写入 systemd unit + env file
- 启动 service 并 probe CDP 端口

## 远程查看浏览器

不再使用 noVNC。直接通过向日葵远程桌面连接 cjh 桌面，即可看到两个 Chromium 窗口（店小秘列表页 + 明空选品页）。

需要纯 CDP 访问的本地脚本：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools\open_server_browser_tunnel.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File tools\open_mk_server_browser_tunnel.ps1
```

两个脚本都只转发 CDP 端口（9222 / 9223），不再转发 noVNC。

## 服务名

- `autovideosrt-browser.service`：店小秘 Shopify ID 同步使用的共享浏览器
- `autovideosrt-mk-browser.service`：明空选品独立浏览器

两者都是 `User=cjh`，`After=graphical.target`，依赖 cjh 真桌面登录后才启动。

## 复用方式

后续任何自动化脚本，只要连接：

```text
http://127.0.0.1:9222    # 店小秘共享 profile
http://127.0.0.1:9223    # 明空选品 profile
```

并使用对应 profile 浏览器上下文，就可以复用同一套登录态。

## Shopify ID 回填定时任务

服务器上使用 systemd timer 运行 Shopify ID 回填：

```bash
cd /opt/autovideosrt
bash deploy/server_browser/install_shopifyid_sync_timer.sh
```

安装后会创建：

- `autovideosrt-shopifyid-sync.service`
- `autovideosrt-shopifyid-sync.timer`

定时任务每天 `12:11` 执行一次，和 ROI 实时同步的 `:02/:22/:42` 触发点错开，实际命令为：

```bash
/opt/autovideosrt/venv/bin/python /opt/autovideosrt/tools/shopifyid_dianxiaomi_sync.py \
  --skip-login-prompt \
  --browser-mode server-cdp \
  --browser-cdp-url http://127.0.0.1:9222 \
  --db-mode local
```

它会复用 `/data/autovideosrt/browser/profiles/shared` 里的店小秘登录态，并通过 `/data/autovideosrt/browser/runtime/automation.lock` 串行执行，避免后续多个浏览器自动化模块同时操作同一个 Chrome。
```

- [ ] **Step 2: Commit**

```bash
git add docs/server_browser_runtime.md
git commit -m "docs(server-browser): rewrite for real-desktop architecture"
```

---

## Task 9: Server-side switchover and verification

**Files:**
- None modified locally. Server-side commands only.

This task happens after the worktree is merged to master and pushed to origin. Steps 1-3 below assume merge-and-push has completed.

- [ ] **Step 0: Pre-flight checks**

```bash
ssh -i ~/.ssh/CC.pem -o StrictHostKeyChecking=no root@172.30.254.14 \
  'cd /opt/autovideosrt && git status --short && echo "---" && \
   who | grep -E "cjh.*:0" && echo "---" && \
   ls -la /run/user/1000/gdm/Xauthority'
```

Expected:
- `git status --short` is empty (server has no uncommitted local changes)
- `who` shows `cjh ... :0 (...)` (cjh is logged into the real desktop)
- `Xauthority` file exists at `/run/user/1000/gdm/Xauthority`

If any check fails, stop and fix before continuing.

- [ ] **Step 1: Stop legacy services on server**

```bash
ssh -i ~/.ssh/CC.pem -o StrictHostKeyChecking=no root@172.30.254.14 \
  'systemctl stop autovideosrt-browser autovideosrt-mk-browser && \
   sleep 2 && \
   pgrep -af "Xvfb :2|x11vnc -display :2|websockify .*60[0-9][0-9]" || echo "[ok] legacy Xvfb/x11vnc(:20,:21)/websockify cleared"'
```

Expected: services stop cleanly, then `[ok] legacy Xvfb/x11vnc(:20,:21)/websockify cleared`. If `x11vnc :0 -auth /run/user/1000/gdm/Xauthority -rfbport 5908` (Codex) appears in any later check, that's correct — leave it (the regex above explicitly excludes `:0` and `5908`).

- [ ] **Step 2: Pull and reinstall**

```bash
ssh -i ~/.ssh/CC.pem -o StrictHostKeyChecking=no root@172.30.254.14 \
  'cd /opt/autovideosrt && git pull --ff-only && \
   bash deploy/server_browser/install_server_browser.sh && \
   bash deploy/server_browser/install_mk_browser.sh'
```

Expected: install scripts finish without error; CDP probes report Chromium versions.

- [ ] **Step 3: Verify systemd unit health**

```bash
ssh -i ~/.ssh/CC.pem -o StrictHostKeyChecking=no root@172.30.254.14 \
  'systemctl status autovideosrt-browser autovideosrt-mk-browser --no-pager -l | sed -n "1,40p"'
```

Expected: both `Active: active (running)`, no recent restart loops.

- [ ] **Step 4: Verify CDP endpoints**

```bash
ssh -i ~/.ssh/CC.pem -o StrictHostKeyChecking=no root@172.30.254.14 \
  'curl -fsS http://127.0.0.1:9222/json/version && echo "---9223---" && curl -fsS http://127.0.0.1:9223/json/version'
```

Expected: both return JSON with `Browser: Chrome/...`.

- [ ] **Step 5: Verify Chromium runs as cjh on display :0**

```bash
ssh -i ~/.ssh/CC.pem -o StrictHostKeyChecking=no root@172.30.254.14 \
  'ps -eo user,pid,cmd | grep -E "(chrom|google-chrome)" | grep -v grep'
```

Expected: at least two `cjh ... chrome ... --user-data-dir=/data/autovideosrt/browser/profiles/{shared,mk-selection}` lines.

- [ ] **Step 6: User confirms via Sunlogin**

Ask the user to connect via Sunlogin to the cjh desktop and confirm:
- Two Chromium windows are visible
- One is on `dianxiaomi.com/web/shopifyProduct/online`
- One is on `dianxiaomi.com/web/stat/salesStatistics`

If the user can interact (close popups, etc.) the desktop integration works.

- [ ] **Step 7: End-to-end Shopify ID sync smoke test**

```bash
ssh -i ~/.ssh/CC.pem -o StrictHostKeyChecking=no root@172.30.254.14 \
  '/opt/autovideosrt/venv/bin/python /opt/autovideosrt/tools/shopifyid_dianxiaomi_sync.py \
     --skip-login-prompt \
     --browser-mode server-cdp \
     --browser-cdp-url http://127.0.0.1:9222 \
     --db-mode local'
```

Expected: ends with `同步完成：` summary block (店小秘在线商品总数, 命中 product_code, 已一致 等). If `RuntimeError: 未找到唯一的店小秘"同步产品"按钮` shows up — the same `count() != 1` race we already saw on 4-28 — re-run once. If it persists, that's the residual race noted as out-of-scope; capture the failure but don't block here.

- [ ] **Step 8: Final state check**

```bash
ssh -i ~/.ssh/CC.pem -o StrictHostKeyChecking=no root@172.30.254.14 \
  'pgrep -af "Xvfb |x11vnc -display :2|websockify" || echo "[ok] legacy stack cleared"'
```

Expected: `[ok] legacy stack cleared`. Codex's `x11vnc :0 ... -rfbport 5908` should NOT appear here (regex excludes display `:0` and ports other than `:20/:21`).

---

## Rollback Procedure

Use this only if Task 9 fails and the regression cannot be fixed in a follow-up commit on the same branch.

**If still on the worktree branch (no merge to master yet):**

1. On the server: `systemctl stop autovideosrt-browser autovideosrt-mk-browser`
2. Locally: `cd /g/Code/AutoVideoSrtLocal/.worktrees/server-browser-real-desktop && git reset --hard origin/master`
3. On the server: `cd /opt/autovideosrt && git pull --ff-only` (pulls back to whatever was master before this branch)
4. On the server: `bash deploy/server_browser/install_server_browser.sh && bash deploy/server_browser/install_mk_browser.sh` (reinstalls the legacy Xvfb stack via the old install scripts that come back with the pull)
5. Verify legacy services come back up: `systemctl status autovideosrt-browser autovideosrt-mk-browser`
6. profile path is unchanged → cookies/login state are preserved across the rollback

**If already merged to master:**

1. Locally:
   ```bash
   cd /g/Code/AutoVideoSrtLocal
   git revert --no-edit -m 1 <merge-commit-sha>
   git push origin master
   ```
2. On the server: `cd /opt/autovideosrt && git pull --ff-only && bash deploy/server_browser/install_server_browser.sh && bash deploy/server_browser/install_mk_browser.sh`
3. Same verification + profile preservation as above

---

## Merge & Cleanup (per CLAUDE.md branch discipline)

After Task 9 verifies on server:

- [ ] **Step 1: Switch to master worktree, merge, push**

```bash
cd /g/Code/AutoVideoSrtLocal
git merge --no-ff refactor/server-browser-real-desktop -m "refactor(server-browser): migrate to cjh real desktop"
git push origin master
```

- [ ] **Step 2: Cleanup worktree + branch**

```bash
git worktree remove .worktrees/server-browser-real-desktop
git branch -d refactor/server-browser-real-desktop
```

(Per CLAUDE.md, this cleanup is automatic — no need to ask the user.)

- [ ] **Step 3: Confirm**

```bash
git worktree list | grep server-browser-real-desktop || echo "[ok] worktree gone"
git branch | grep refactor/server-browser-real-desktop || echo "[ok] branch gone"
```

Expected: both `[ok] ...` lines.
