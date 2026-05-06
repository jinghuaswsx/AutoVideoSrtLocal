# 服务端共享浏览器运行层

## 目标

给 Ubuntu Server 提供一套可复用的浏览器运行环境，用于：

- 店小秘后台抓取
- 明空网络登录态抓取
- Shopify 后台自动化
- 小秘云仓 (xmyc.com) 仓库 SKU 抓取（采购价 / 库存）
- 其他依赖浏览器登录态的模块

## 组成

- KDE Plasma X11 真桌面（GDM3 自动登录 cjh，由 Codex 维护）
- `Chromium`（Playwright Chromium）：优先跑在真桌面 `:0` 上，由 systemd 管理；X11 失效时默认切到 headless CDP 兜底
- `CDP`：给自动化模块连接浏览器
- 向日葵远程桌面：用户介入入口（看页面、关弹窗、补登录）

## 端口

全部只监听服务器本机：

- `127.0.0.1:9222`：Shopify ID 同步使用的店小秘 Chromium CDP
- `127.0.0.1:9223`：明空选品店小秘 Chromium CDP
- `127.0.0.1:9224`：小秘云仓 (xmyc.com) Chromium CDP
- `0.0.0.0:6082`：noVNC web 入口（websockify → `[::1]:5900` 上的 cjh:0 桌面 x11vnc）

CDP 端口仅监听本机；noVNC 监听 `0.0.0.0:6082` 以便内网浏览器直接访问 cjh 桌面（LocalServer 无公网接口）。后续若需要暴露到公网，必须在 noVNC 之前加 token 鉴权。

## 共享登录态

统一使用一个共享浏览器 profile：

- `/data/autovideosrt/browser/profiles/shared`（owner：cjh）

后续不同模块只要复用这一个 profile，即可共用已经登录好的站点状态。

如果某个模块必须隔离账号、Cookie 或店铺上下文，应使用独立 profile。当前已隔离的 profile：

- `/data/autovideosrt/browser/profiles/mk-selection`（owner：cjh）—— 明空选品
- `/data/autovideosrt/browser/profiles/xmyc-storage`（owner：cjh）—— 小秘云仓

## 安装

服务器上执行：

```bash
cd /opt/autovideosrt
bash deploy/server_browser/install_server_browser.sh
bash deploy/server_browser/install_mk_browser.sh
bash deploy/server_browser/install_xmyc_browser.sh
bash deploy/server_browser/install_novnc.sh
```

两个脚本会：

- 装 `dbus-x11 fonts-noto-cjk fonts-liberation`（不再装 Xvfb / x11vnc / noVNC / openbox）
- 装 Playwright Chromium
- 把 profile / runtime / logs 目录 chown 给 cjh
- 写入 systemd unit + env file
- 启动 service 并 probe CDP 端口

## 远程查看浏览器

两条路：

1. **noVNC（推荐，浏览器直连）**：内网浏览器打开
   ```
   http://172.30.254.14:6082/vnc.html?host=172.30.254.14&port=6082&autoconnect=true&resize=remote
   ```
   会直接连进 cjh 桌面，看到三个 Chromium 窗口（店小秘列表 / 明空选品 / 小秘云仓）。
2. **向日葵远程桌面**：直接进 cjh 桌面（如果 `runsunloginclient.service` 起着）。

需要纯 CDP 访问的本地脚本：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools\open_server_browser_tunnel.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File tools\open_mk_server_browser_tunnel.ps1
```

## 服务名

- `autovideosrt-browser.service`：店小秘 Shopify ID 同步使用的共享浏览器（CDP 9222）
- `autovideosrt-mk-browser.service`：明空选品独立浏览器（CDP 9223）
- `autovideosrt-xmyc-browser.service`：小秘云仓独立浏览器（CDP 9224）
- `autovideosrt-novnc.service`：noVNC web 代理（websockify 0.0.0.0:6082 → [::1]:5900）

前三个 `User=cjh`，`After=graphical.target`，优先依赖 cjh 真桌面登录后启动。noVNC 是 `User=root`，`After=graphical.target`（5900 上的 x11vnc 是 cjh 桌面里跑的，所以也要等桌面起来）。

## X11 失效时的 CDP 兜底

`deploy/server_browser/run_server_browser.sh` 启动 Chromium 前必须检查 `DISPLAY` 对应的 `/tmp/.X11-unix/X*` socket 是否存在。

- socket 存在：按真桌面模式启动，保留 `--start-maximized`，noVNC / 向日葵能看到浏览器窗口。
- socket 缺失：默认用 `--headless=new` 启动 Chromium，只保证 CDP 端口可用，让订单同步、广告同步、Shopify ID 回填、小秘云仓抓取等浏览器自动化定时任务继续跑。
- headless 兜底不提供可视桌面窗口；需要人工补登录、关闭弹窗或通过 noVNC 检查页面时，仍必须修复 GDM / X11 真桌面。
- 如需严格要求真桌面，可在对应 env file 里设置 `BROWSER_HEADLESS_FALLBACK=0`，此时 socket 缺失会直接失败并交给 systemd 记录错误。

## 复用方式

后续任何自动化脚本，只要连接：

```text
http://127.0.0.1:9222    # 店小秘共享 profile
http://127.0.0.1:9223    # 明空选品 profile
http://127.0.0.1:9224    # 小秘云仓 profile
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

## 小秘云仓采购价定时任务

服务器上使用 systemd timer 运行小秘云仓 SKU 抓取 + 自动匹配：

```bash
cd /opt/autovideosrt
bash deploy/server_browser/install_xmyc_storage_sync_timer.sh
```

安装后会创建：

- `autovideosrt-xmyc-storage-sync.service`
- `autovideosrt-xmyc-storage-sync.timer`

定时任务每天 `12:33` 执行一次（避开 ROI 实时同步 `:02/:22/:42` 与 Shopify ID 同步 `12:11`），实际命令为：

```bash
/opt/autovideosrt/venv/bin/python /opt/autovideosrt/tools/xmyc_storage_sync.py \
  --cdp-url http://127.0.0.1:9224
```

它会复用 `/data/autovideosrt/browser/profiles/xmyc-storage` 里的小秘云仓登录态，把全量 SKU + 单价缓存到 `xmyc_storage_skus`，再按 `dianxiaomi_order_lines.product_display_sku` 自动匹配到 `media_products`，最后用主力 SKU 的单价回填 `media_products.purchase_price`。
