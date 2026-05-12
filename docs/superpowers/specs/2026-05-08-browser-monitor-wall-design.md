# 浏览器监控四宫格设计

日期：2026-05-08

## 文档锚点

- `docs/server_browser_runtime.md#端口`：DXM01-Meta、DXM02-MK、DXM03-RJC、TABCUT 的 CDP/noVNC 端口定义。
- `docs/server_browser_runtime.md#服务名`：四套可视化浏览器 systemd service 名称。
- `docs/superpowers/specs/2026-05-08-cdp-environment-split-design.md#常驻监控与报警`：四套环境必须常驻、可视化、CDP 可用，并由 `cdp_environment_watchdog` 每分钟检查。
- `AGENTS.md#定时任务归集规则`：本需求不新增定时任务，复用已登记的 `cdp_environment_watchdog` 与 `scheduled_task_runs`。

## 目标

在 Web 后台左侧菜单“实验室”中新增“浏览器监控”入口。用户点击后直接进入一个四宫格页面，一眼看到四套可视化浏览器窗口状态：

- `DXM01-Meta`：Meta Ads Manager 导出环境，noVNC 端口 `6092`。
- `DXM02-MK`：明空选品店小秘环境，noVNC 端口 `6093`。
- `DXM03-RJC`：荣锦成店小秘订单、SKU、Shopify ID 同步环境，noVNC 端口 `6095`。
- `TABCUT`：Tabcut 选品采集环境，noVNC 端口 `6097`。

顶部横条用于压缩展示状态汇总和快捷操作，不再占用右下角窗口。

## 页面入口与权限

- 新增路由：`GET /browser-monitor`。
- 页面标题：`浏览器监控`。
- 菜单位置：左侧菜单“实验室”分组内，菜单名固定为 `浏览器监控`。
- 权限：复用现有 `lab` 菜单权限；没有 `lab` 权限的用户不可通过菜单进入，也不能直接访问页面。
- 页面作为后台登录态页面，不提供匿名公开入口。

## 四宫格布局

桌面端使用 2x2 网格：

| 位置 | 内容 |
|---|---|
| 左上 | `DXM01-Meta` noVNC iframe |
| 右上 | `DXM02-MK` noVNC iframe |
| 左下 | `DXM03-RJC` noVNC iframe |
| 右下 | `TABCUT` noVNC iframe |

每个 noVNC iframe 直接加载内网 noVNC URL：

- `http://172.30.254.14:6092/vnc.html?host=172.30.254.14&port=6092&autoconnect=true&resize=remote`
- `http://172.30.254.14:6093/vnc.html?host=172.30.254.14&port=6093&autoconnect=true&resize=remote`
- `http://172.30.254.14:6095/vnc.html?host=172.30.254.14&port=6095&autoconnect=true&resize=remote`
- `http://172.30.254.14:6097/vnc.html?host=172.30.254.14&port=6097&autoconnect=true&resize=remote`

移动端和窄屏下退化为单列堆叠，避免 iframe 被压到不可读。

## 状态汇总

页面不新增探测定时任务。顶部状态横条复用 `cdp_environment_watchdog` 最近一轮 `scheduled_task_runs.summary_json`：

- 显示最近检查开始时间和状态。
- 对每个环境展示最近检查结果：正常、异常、未知。
- 若最近摘要里包含 `initial`/`final` 检查结果，优先展示 `final.ok`；没有摘要时显示未知。
- 每个环境的单独打开链接仍保留在各自浏览器卡片右上角，便于用户进入大窗口操作。

如果读取运行日志失败，页面仍展示四个 iframe 和单独打开链接，状态区显示“状态暂不可用”。

## 操作

- “刷新画面”：前端重新赋值四个 iframe 的 `src`，触发 noVNC 重新加载。
- “单独打开”：每个环境提供一个新标签页链接，打开对应 noVNC 页面。
- 不提供页面内重启 systemd service 的按钮；环境恢复仍由既有 watchdog 或人工运维完成。

## 视觉与交互

沿用 Ocean Blue Admin 设计系统：

- 使用现有 `layout.html` 的侧栏、头部和主内容结构。
- 颜色使用 CSS 变量或现有蓝/灰 token，不引入紫色。
- 四宫格卡片使用白底、浅边框、8-12px 圆角。
- iframe 固定最小高度，避免加载过程导致布局跳动。

## 测试

聚焦测试覆盖：

- `/browser-monitor` 登录后返回 `200`。
- 页面包含 `DXM01-Meta`、`DXM02-MK`、`DXM03-RJC`、`TABCUT`。
- 页面包含四个 noVNC iframe URL，端口分别为 `6092`、`6093`、`6095`、`6097`。
- 侧栏实验室分组内包含 `浏览器监控`，链接指向 `/browser-monitor`。
- 访问 `/browser-monitor` 时实验室分组高亮，浏览器监控菜单高亮。

## 不适用项

- 不新增数据库表。
- 不新增定时任务。
- 不改三套 DXM systemd unit；TABCUT 作为新增独立 systemd unit 管理。
- 不更新 `CHANGELOG`：当前仓库无 `CHANGELOG*` 文件；本变更是内部后台入口和使用文档更新。
