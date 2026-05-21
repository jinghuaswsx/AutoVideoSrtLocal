# Tabcut 每日采集与定时任务管理

最后更新：2026-05-12

## 采集目标

Tabcut 选品模块固定采集美国站（`US`）数据，依赖服务器上已经登录旗舰版账号的专用可视浏览器环境。

每日采集内容：

- 视频榜：日榜、周榜、月榜，每个周期分别采集播放榜和销量榜，各 10 页，每页 100 条，即每个榜单 1000 条。
- 商品榜：采集用户框选的 9 个类目，每个类目每天前 50 名。
- 目标日期：默认从北京时间当天往前取，最新商品榜日期为前一天，例如 2026-05-12 运行时采集 2026-05-11。

框选商品榜类目：

- 家装建材（Home Improvement，`categoryId=11`）
- 居家日用（Home Supplies，`categoryId=12`）
- 家电（Household Appliances，`categoryId=13`）
- 厨房用品（Kitchenware，`categoryId=16`）
- 宠物用品（Pet Supplies，`categoryId=20`）
- 手机与数码（Phones & Electronics，`categoryId=21`）
- 五金工具（Tools & Hardware，`categoryId=25`）
- 玩具和爱好（Toys & Hobbies，`categoryId=26`）
- 汽车与摩托车（Automotive & Motorcycle，`categoryId=27`）

## 浏览器环境

每日任务必须运行在服务器浏览器环境上，不使用 Windows 本机浏览器，也不连接 Windows 本机 MySQL。

生产环境绑定：

- systemd service：`autovideosrt-tabcut-vnc.service`
- Chrome profile：`/data/autovideosrt/browser/profiles/tabcut`
- CDP：`http://127.0.0.1:9227`
- noVNC：`http://172.16.254.106:6097/vnc.html`
- 登录态：Tabcut 旗舰版账号，由运维在 noVNC 窗口保持登录

采集命令通过 CDP 在该浏览器上下文内发起请求，保留登录态与会员权限；请求间隔不低于 3 秒。

## 数据流

入口命令：

```bash
python -m tools.tabcut_crawler.main --mode recent7 --days 30
```

核心流程：

1. `tools.tabcut_crawler.runner.build_recent7_plan()` 生成采集计划。
2. 视频榜调用 `GET /api/ranking/videos`，固定 `region=US`、`rankDay in (1,7,30)`、`sort in (play,sales)`。
3. 商品榜调用 `GET /api/trpc/ranking.goods.rankingData`，固定 `region=US`、`rankType=1`、`orderType=1`，按 9 个 `categoryId` 各取 `pageSize=50`。
4. 采集结果标准化后写入：
   - `tabcut_videos`
   - `tabcut_video_snapshots`
   - `tabcut_goods`
   - `tabcut_goods_snapshots`
   - `tabcut_video_candidates`
5. 原始脱敏产物写入 `/data/autovideosrt/tabcut/daily`，便于排查。

## 定时任务管理

生产 systemd timer：

- service：`autovideosrt-tabcut-daily-selection.service`
- timer：`autovideosrt-tabcut-daily-selection.timer`
- schedule：每天 08:00（北京时间）
- dependency：`Wants/After=autovideosrt-tabcut-vnc.service`

安装或更新：

```bash
bash deploy/server_browser/install_tabcut_daily_selection_timer.sh
```

Web 后台「定时任务」菜单登记：

- task code：`tabcut_daily_selection`
- source type：`systemd`
- source ref：`autovideosrt-tabcut-daily-selection.timer`
- control strategy：继承 systemd，可在后台启停
- log table：`scheduled_task_runs`

## 日志

`tools.tabcut_crawler.main` 默认记录一次 `scheduled_task_runs`：

- 启动时写入 `running`
- 成功时写入 `success`，`summary_json` 包含请求数、视频数、商品数、候选数、输出目录
- 失败时写入 `failed`，`error_message` 记录异常信息，并触发既有定时任务失败告警机制
- `output_file` 指向本次采集产物目录

手动调试如不想写日志，可加：

```bash
python -m tools.tabcut_crawler.main --mode recent7 --days 1 --no-record-run
```
