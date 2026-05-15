# Meta 热帖全集同步设计

日期：2026-05-15

## 背景

Meta 热帖同步任务当前每天 07:00 只采集 500 条。线上只读探测显示，在当前筛选条件下，上游接口返回 `total=2307`、`size=30`，第 77 页结束，第 78 页为空。500 条只覆盖前 17 页，不足以支持“今日新增”和欧洲投放评估覆盖全集素材。

## 口径

- 每天 07:00 同步 `/api/spy/hot/posts` 当前筛选条件下的全集。
- 同步停止条件按优先级：
  1. 上游返回空 `items`；
  2. 已写入数量达到上游首个有效 `total`；
  3. 达到防御性 `max_pages` 上限。
- `first_seen_at` 仍表示本地首次入库时间，“今日新增”只按当天 `first_seen_at` 展示。
- 商品分析、视频下载、美国可搬运分析、欧洲适配评估沿用现有 10 分钟队列任务，直到 `pending` 队列清空。

## 默认参数

- `FULL_SYNC_MAX_PAGES = 120`
- 当前 page size 为 30，120 页可覆盖 3600 条，足够覆盖当前 2307 条，并为短期增长留余量。
- 保留 `target_count` 兼容参数，`target_count=None` 或 `target_count<=0` 表示全集。
- `sync_period_likes` 表示周期互动变化，上游可能返回负数，数据库字段必须使用 signed `BIGINT`。

## 验收

- 每日同步 summary 包含 `reported_total`、`posts`、`pages`、`stop_reason`。
- 在当前接口规模下，同步应写入约 2307 条，`stop_reason` 为 `reported_total_reached` 或 `empty_page`。
- 若接口增长超过 120 页，summary 必须返回 `stop_reason=max_pages_reached`，方便后台观察并调高上限。
