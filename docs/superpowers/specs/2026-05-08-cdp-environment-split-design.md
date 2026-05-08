# CDP 环境拆分与命名调整（2026-05-08）

## 背景

服务端共享浏览器原来把 `127.0.0.1:9222` 作为店小秘共享 profile 使用，Meta 广告同步、Shopify ID 回填和订单抓取都可能复用同一个浏览器。实际运行中该浏览器同时打开店小秘与 Ads Manager，容易互相影响；一旦 CDP 卡住，Meta 广告同步和店小秘订单同步会一起失败。

用户要求把 CDP 环境按职责重命名并拆分：

- `DXM01-Meta`：专门同步 Meta 广告数据。
- `DXM02-MK`：原 `DXM02`，用于明空选品。
- `DXM03-RJC`：新建，用于登录荣锦成店小秘账号，后续承接订单数据与 Shopify ID 同步。

## 目标

1. 建立三个命名清晰、profile 隔离的 CDP 环境。
2. Meta 广告实时/收盘同步明确依赖 `DXM01-Meta`，不再依赖通用店小秘共享浏览器。
3. 新建 `DXM03-RJC` 浏览器服务，先供人工登录荣锦成店小秘账号。
4. 保留订单与 Shopify ID 同步的业务逻辑切换空间；在用户完成登录前，不做大规模订单同步逻辑改造。
5. 每个浏览器自动化任务使用各自 runtime 下的 lock 文件，避免不同环境互相阻塞。

## 非目标

- 不在本次重写订单同步、Shopify ID 同步的数据口径。
- 不删除历史 profile 和历史导出文件。
- 不改 Meta 多账户配置和广告费分摊逻辑。
- 不把 CDP 端口暴露到公网。

## 目标环境

| 环境 | Service | CDP | Profile | Runtime / Lock | 用途 |
|---|---|---:|---|---|---|
| `DXM01-Meta` | `autovideosrt-dxm01-meta-vnc.service` | `9222` | `/data/autovideosrt/browser/profiles/meta-ads` | `/data/autovideosrt/browser/runtime-meta-ads` | Meta Ads Manager 导出 |
| `DXM02-MK` | `autovideosrt-dxm02-mk-vnc.service` | `9223` | `/data/autovideosrt/browser/profiles/mk-selection` | `/data/autovideosrt/browser/runtime-mk-selection` | 明空选品店小秘 |
| `DXM03-RJC` | `autovideosrt-dxm03-rjc-vnc.service` | `9225` | `/data/autovideosrt/browser/profiles/rjc-dianxiaomi` | `/data/autovideosrt/browser/runtime-rjc-dianxiaomi` | 荣锦成店小秘订单与 Shopify ID |
| 小秘云仓 | `autovideosrt-xmyc-browser.service` | `9224` | `/data/autovideosrt/browser/profiles/xmyc-storage` | `/data/autovideosrt/browser/runtime-xmyc-storage` | 小秘云仓库存/采购价 |

`9224` 已被小秘云仓占用，`DXM03-RJC` 使用 `9225`，避免与现有服务冲突。

## 任务依赖

Meta 相关 systemd service 必须改为依赖 `autovideosrt-dxm01-meta-vnc.service`：

- `autovideosrt-roi-realtime-sync.service`
- `autovideosrt-meta-daily-final-sync.service`
- `autovideosrt-meta-daily-final-check.service`

这些任务继续使用 `META_AD_EXPORT_CDP_URL=http://127.0.0.1:9222`，但含义变为 `DXM01-Meta` 的独立 profile。拆分后不再通过共享 browser lock 串行。

`DXM03-RJC` 创建后先不强制订单同步与 Shopify ID 同步切换；等用户通过可视化入口登录荣锦成店小秘账号后，再单独调整：

- `tools/roi_hourly_sync.py` 中订单导入使用的 `dxm_env`。
- `deploy/server_browser/autovideosrt-shopifyid-sync.service` 的 CDP URL 与依赖 service。
- 后续 SKU / Shopify ID / 订单同步的站点范围与账号隔离逻辑。

## 兼容策略

- 代码中保留旧 `DXM-01` / `DXM-02` 名称作为迁移期兼容入口，但正式展示和新命令使用 `DXM01-Meta`、`DXM02-MK`、`DXM03-RJC`。
- `autovideosrt-browser.service` 作为旧共享浏览器不再作为新任务的首选依赖；生产切换时需要避免它继续占用 `9222`。
- 如果 `meta-ads` profile 尚未登录 Meta，需要人工登录后 Meta 导出才能成功。

## 验收

1. 仓库内存在 `autovideosrt-dxm01-meta-vnc.service`、`autovideosrt-dxm02-mk-vnc.service`、`autovideosrt-dxm03-rjc-vnc.service` 和 `install_cdp_environment_watchdog_timer.sh`。
2. Meta 定时任务依赖 `autovideosrt-dxm01-meta-vnc.service`，店小秘订单与 Shopify ID 同步依赖 `autovideosrt-dxm03-rjc-vnc.service`。
3. `DXM03-RJC` 的 service 写入 `DXM_CDP_PORT=9225` 和 `profiles/rjc-dianxiaomi`。
4. `docs/server_browser_runtime.md` 中端口、服务名和 profile 与本 spec 一致。
5. 相关测试覆盖新命名、新端口和 Meta 任务依赖。
6. 部署后 `127.0.0.1:9222/json/version`、`127.0.0.1:9223/json/version`、`127.0.0.1:9225/json/version` 可按已启动服务分别探测。

## 常驻监控与报警

DXM01-Meta、DXM02-MK、DXM03-RJC 必须在服务器在线时保持常驻、可视化、CDP 可用。新增 `cdp_environment_watchdog` 定时任务，每分钟检查三项：

- systemd service 是否为 `active`；
- CDP `/json/version` 是否可访问；
- noVNC `/vnc.html` 是否可访问。

任一检查失败时，watchdog 立即重启对应 systemd service，并把本轮运行写入 `scheduled_task_runs`。只要本轮发现过异常，即使重启后恢复，也标记为 `failed`，让 Web 后台 admin 顶部失败条立即报警；下一轮全健康时再写入 `success`，自动清掉已恢复的报警。

三个环境拆分后不再使用旧共享锁 `/data/autovideosrt/browser/runtime/automation.lock`。环境级 watchdog 与业务同步任务应直接操作各自独立 CDP service，不再通过 `with_browser_lock.sh` 串行整个共享浏览器。

## Docs-anchor

- 本文件。
- 运行层说明：`docs/server_browser_runtime.md`。
- 定时任务归集规则：`AGENTS.md#定时任务归集规则`。
