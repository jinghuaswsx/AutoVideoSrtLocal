# 服务端共享浏览器运行层

## 目标

给 Ubuntu Server 提供一套可复用的浏览器运行环境，用于：

- 店小秘后台抓取
- 明空网络登录态抓取
- Shopify 后台自动化
- 其他依赖浏览器登录态的模块

## 组成

- `Xvfb`：虚拟显示器
- `openbox`：轻量窗口管理器
- `x11vnc`：把虚拟桌面暴露为本地 VNC
- `websockify + noVNC`：把 VNC 转成浏览器可访问页面
- `Chromium (Playwright Chromium)`：共享浏览器
- `CDP`：给自动化模块连接浏览器

## 端口

全部只监听服务器本机：

- `127.0.0.1:6080`：noVNC
- `127.0.0.1:9222`：Chrome DevTools Protocol

外部访问通过 SSH 隧道完成，不直接暴露公网。

## 共享登录态

统一使用一个共享浏览器 profile：

- `/data/autovideosrt/browser/profiles/shared`

后续不同模块只要复用这一个 profile，即可共用已经登录好的站点状态。

## 安装

服务器上执行：

```bash
cd /opt/autovideosrt
bash deploy/server_browser/install_server_browser.sh
```

## 本地访问

Windows 本机可以执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools\open_server_browser_tunnel.ps1
```

然后在本地浏览器打开：

```text
http://127.0.0.1:6080/vnc.html
```

## 服务名

- `autovideosrt-browser.service`

## 复用方式

后续任何自动化脚本，只要连接：

```text
http://127.0.0.1:9222
```

并使用共享 profile 浏览器上下文，就可以复用同一套登录态。

## Shopify ID 回填定时任务

服务器上使用 systemd timer 运行 Shopify ID 回填：

```bash
cd /opt/autovideosrt
bash deploy/server_browser/install_shopifyid_sync_timer.sh
```

安装后会创建：

- `autovideosrt-shopifyid-sync.service`
- `autovideosrt-shopifyid-sync.timer`

定时任务每天 `12:10` 执行一次，实际命令为：

```bash
/opt/autovideosrt/venv/bin/python /opt/autovideosrt/tools/shopifyid_dianxiaomi_sync.py \
  --skip-login-prompt \
  --browser-mode server-cdp \
  --browser-cdp-url http://127.0.0.1:9222 \
  --db-mode local
```

它会复用 `/data/autovideosrt/browser/profiles/shared` 里的店小秘登录态，并通过 `/data/autovideosrt/browser/runtime/automation.lock` 串行执行，避免后续多个浏览器自动化模块同时操作同一个 Chrome。
