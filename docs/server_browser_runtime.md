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

定时任务每天 `12:10` 执行一次，实际命令为：

```bash
/opt/autovideosrt/venv/bin/python /opt/autovideosrt/tools/shopifyid_dianxiaomi_sync.py \
  --skip-login-prompt \
  --browser-mode server-cdp \
  --browser-cdp-url http://127.0.0.1:9222 \
  --db-mode local
```

它会复用 `/data/autovideosrt/browser/profiles/shared` 里的店小秘登录态，并通过 `/data/autovideosrt/browser/runtime/automation.lock` 串行执行，避免后续多个浏览器自动化模块同时操作同一个 Chrome。
