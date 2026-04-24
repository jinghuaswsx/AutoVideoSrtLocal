# 服务端共享浏览器运行层设计

## 背景

当前店小秘、明空网络、Shopify 小语种适配等模块都依赖浏览器登录态。Windows 本机专用 Chrome 可以满足单机运行，但不适合长期迁移到 Ubuntu Server。

## 设计目标

在 Ubuntu Server 上提供一套轻量、可视化、可复用的共享浏览器运行层：

- 支持人工远程登录网站
- 支持自动化模块通过 CDP 连接同一个浏览器
- 不要求安装完整桌面环境
- 不把调试端口直接暴露公网

## 架构

- `Xvfb` 提供虚拟显示
- `openbox` 提供最小窗口管理
- `x11vnc + noVNC` 提供浏览器远程可视化入口
- `Chromium` 提供共享 profile 浏览器
- `CDP` 提供脚本自动化入口
- `SSH tunnel` 负责安全访问

## 关键决策

### 1. 不装完整桌面

只安装浏览器运行必需组件，不安装 Ubuntu Desktop / GNOME。

### 2. 共享 profile

统一使用 `/data/autovideosrt/browser/profiles/shared`，让多个模块共享登录态。

### 3. 端口只绑定本机

`6080` 和 `9222` 都绑定 `127.0.0.1`，外部通过 SSH 隧道访问。

### 4. Chromium 采用 Playwright 浏览器

避免 Ubuntu Server 上 Chromium Snap 带来的维护成本，统一由 `playwright install chromium` 管理浏览器二进制。

## 交付物

- 安装脚本
- systemd service
- 浏览器运行脚本
- 本地 SSH 隧道脚本
- 运维说明文档

## 验证标准

- 服务器能启动 `autovideosrt-browser.service`
- `127.0.0.1:9222/json/version` 可访问
- `127.0.0.1:6080/vnc.html` 可访问
- 本地通过 SSH 隧道能看到远程 Chromium
- 用户能在远程 Chromium 内登录店小秘
- 后续自动化脚本可复用该浏览器 CDP
