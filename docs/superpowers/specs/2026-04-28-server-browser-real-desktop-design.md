# 服务端共享浏览器搬迁到真桌面设计

## 背景

当前 `autovideosrt-browser.service`（店小秘 Shopify ID 同步）和 `autovideosrt-mk-browser.service`（明空选品店小秘）跑在 `Xvfb (:20/:21) + openbox + x11vnc + websockify/noVNC` 链路上，用户要看浏览器只能在本地起 SSH 隧道开 noVNC，介入弹窗、登录态恢复都很别扭。

2026-04-21 服务器迁到内网 LocalServer（172.30.254.14）后，Codex 已经把"真桌面"前置条件做好了：
- KDE Plasma X11 + GDM3 自动登录 cjh
- Sunlogin 客户端 + KDE autostart
- `x11vnc :0 → 5908`（带密码 `/etc/x11vnc-cjh.pass`）

这次设计在此基础上把抓取浏览器从 Xvfb 搬到真桌面 `:0`，让用户通过向日葵直接看见自动化浏览器、能在出问题时手动介入。

## 设计目标

- 自动化 Chromium 跑在 cjh 真桌面 `:0` 上
- 用户通过向日葵看到 Chrome 窗口，能用真键鼠介入（关弹窗、补登录、看页面）
- 共享 profile 路径不变，登录态零损失
- CDP 端口、API、入参都不变，调用方零感知
- 拆掉 Xvfb / openbox / x11vnc(:20/:21) / websockify / noVNC 全链路
- 不动 Codex 加的 `:0` x11vnc / Sunlogin / GDM3，避免相互踩踏

## 关键决策

### 1. systemd unit 用 `User=cjh`（不是 root）

理由：
- Profile 落到 cjh 名下，cjh 桌面手动启动的浏览器和 systemd 启动的浏览器能共享
- pulseaudio / dbus / 输入法跟随 cjh，跟桌面一致
- 权限最小化

代价：需要 `chown -R cjh:cjh /data/autovideosrt/browser/`（目前是 root:root）。

### 2. Environment 显式注入 X11 句柄

```
Environment="DISPLAY=:0"
Environment="XAUTHORITY=/run/user/1000/gdm/Xauthority"
Environment="XDG_RUNTIME_DIR=/run/user/1000"
```

注意是 `gdm` 不是 `gdm3`（已实测 `/run/user/1000/gdm/Xauthority` 存在）。

`After=graphical.target` + `Wants=graphical.target` 确保桌面 ready 后才启动。

### 3. 启动脚本只启 Chromium，不启 Xvfb 链路

`run_server_browser.sh` 删掉 Xvfb / openbox / x11vnc / websockify 这四段，只保留：
- `dbus-launch`（如必要）
- Chromium 启动（`--user-data-dir`、`--remote-debugging-port`、`--window-size` 保留；`--no-sandbox` 在 cjh 用户下可保留也可移除，先保留稳妥）

### 4. 端口策略：只留 CDP

| 端口 | 旧 | 新 |
|---|---|---|
| 9222 / 9223 | CDP | CDP（不变） |
| 5901 / 5902 | x11vnc :20/:21 | 删除 |
| 6080 / 6081 | noVNC | 删除 |
| 5908 | — | Codex 已加，保留不动 |

向日葵承担"看桌面"职责，不再需要 noVNC。

### 5. 共享 profile 路径不变

`/data/autovideosrt/browser/profiles/shared` 和 `/data/autovideosrt/browser/profiles/mk-selection` 路径不变，只改 owner。登录态、cookies、扩展设置全部继承。

## 交付物

- 改后的 `autovideosrt-browser.service` / `autovideosrt-mk-browser.service`
- 简化后的 `run_server_browser.sh`
- 简化后的 `/etc/default/autovideosrt-{,mk-}browser`（删 VNC/NOVNC/SCREEN/DISPLAY 字段）
- 简化后的 `install_server_browser.sh` / `install_mk_browser.sh`（删 apt xvfb/x11vnc/novnc/websockify/openbox；新增 chown）
- 简化后的 `tools/open_server_browser_tunnel.ps1` / `tools/open_mk_server_browser_tunnel.ps1`（只留 CDP 端口）
- 改写的 `docs/server_browser_runtime.md`（删 noVNC 章节，加向日葵章节）

## 切换流程

1. SSH 到 server，`systemctl stop autovideosrt-browser autovideosrt-mk-browser`
2. `cd /opt/autovideosrt && git pull`（含改后的 unit/sh/install）
3. `chown -R cjh:cjh /data/autovideosrt/browser/`
4. `bash deploy/server_browser/install_server_browser.sh && bash deploy/server_browser/install_mk_browser.sh`
5. `systemctl daemon-reload && systemctl restart autovideosrt-browser autovideosrt-mk-browser`

## 验证标准

四步顺序验证，任一失败立即停下排查：

1. `systemctl status autovideosrt-browser autovideosrt-mk-browser` → active
2. `curl http://127.0.0.1:9222/json/version` 和 `:9223` → 返回 Chrome 版本 JSON
3. 向日葵接进 cjh 桌面 → 看到两个 Chrome 窗口（店小秘列表页 + 明空选品页）
4. 手动 `python tools/shopifyid_dianxiaomi_sync.py --skip-login-prompt --browser-mode server-cdp --browser-cdp-url http://127.0.0.1:9222 --db-mode local` 跑通

## 回滚

- **未合 master**：worktree 工作流，`git checkout master` + 重跑旧版 install 脚本回到 Xvfb 链路
- **已合 master 后失败**：`git revert <merge>` → push → server `git pull` → 重跑 install（旧版会重装回 Xvfb 链路）
- profile 路径不变，登录态在两个方向都不丢

## 不在范围

- `_click_sync_products_button` 的 `count() != 1` 偶发 race（4-28 16:23 那次失败，16:25 重跑就过）：搬到真桌面后页面渲染时序会变，先不修，搬完观察一周再决定
- `_dismiss_dianxiaomi_notice_overlays` 现有逻辑：master 上已有覆盖性修复，不改
- 明控（`appcore/mk_import.py`）：纯 HTTP 接口，跟浏览器无关，不在搬迁范围
- Codex 维护的 GDM3 / Sunlogin / `:0` x11vnc：保留不动
