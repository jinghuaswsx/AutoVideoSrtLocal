# Meta XHR 报告日过滤修复（2026-05-10）

## 文档锚点

- [2026-05-09 Meta 广告 XHR 通道账户时区 + Playwright 线程隔离](2026-05-09-meta-ads-account-timezone-and-async-fix.md)：`account_xhr_time_range(account, business_date)` 会在 PDT 等时区返回跨两个账户自然日的 `time_range`。
- [2026-05-09 Meta 广告 XHR Token Channel](2026-05-09-meta-ads-xhr-token-channel.md)：`date_start` / `date_stop` 是 XHR row 的报告日校验字段。
- [2026-05-07 Meta 广告实时同步 多账户改造](2026-05-07-meta-ads-multi-account-design.md)：`meta_daily_final_sync` 和实时同步共用账户配置，单账户失败不应拖垮其他账户。
- [AGENTS.md](../../../AGENTS.md)：Meta daily-final、实时表 fallback、文档驱动代码和主工作目录隔离规则。

## 背景

2026-05-10 对最近 15 个已收盘业务日做安全补数时发现：`xhr_api` 账号在 PDT 下会请求 `{"since": D, "until": D+1}`。Meta `/insights` 在 `time_increment=1` 下返回的是账户时区自然日行，每行带 `date_start/date_stop`。现有 daily-final / realtime XHR 导入把返回的所有行都写到同一个 `business_date=D`，导致 `D+1` 自然日的数据被并入 `D`。

现场影响：

- 旧户 `2026-04-24` 被一次 XHR 重跑写成 `2026-04-24 + 2026-04-25` 合计，campaign spend 从 CSV 单日口径 `10654.44` 变为 `21667.49`。
- `2026-05-09` 自动 daily-final 在 16:51 写入了 campaign/ad partial，且缺 adset 层；随后用单日 CSV 安全补跑修复。

## 修复口径

XHR 请求仍保留账户时区扩展后的 `time_range`，避免重新引入 2026-05-09 的 PDT 空数据问题。但导入前必须按报告日过滤：

1. 目标报告日 = `account_xhr_time_range(account, business_date)["since"]`。
2. XHR row 若带 `date_start` 或 `date_stop`，仅当该日期等于目标报告日时保留。
3. Row 不带日期字段时保留，用于兼容测试 fixture 和 Meta 异常 payload；生产字段集已经包含 `date_start/date_stop`。
4. daily-final 三层级和 realtime campaign XHR 都必须走同一个过滤 helper。
5. summary 中保留 raw / filtered / dropped 计数，便于后续看运行日志确认是否发生跨日报告行丢弃。

这让 XHR 入库口径与 Ads Manager CSV 的单日 `date=D_D` 对齐：请求可以多覆盖一天，但写库只能写目标账户报告日。

## 非目标

- 不重写 `account_xhr_time_range`；它仍负责按账户时区覆盖 BJ 业务窗口。
- 不改账号 `sync_mode` 配置，不改 systemd timer，不触发生产 Meta 网络访问。
- 不补做历史数据清理脚本；2026-05-10 已通过手工 CSV 安全补跑修复生产最近 15 天。

## 验收

- daily-final XHR 收到 `date_start=D` 和 `date_start=D+1` 两行时，只把 `D` 行传给 `_replace_*_daily_rows_from_api`。
- realtime XHR 收到同款两行时，只把 `D` 行传给 `_import_meta_realtime_api_rows`。
- 现有时区 helper 测试继续通过，确认请求 `time_range` 仍保持账户时区扩展。
