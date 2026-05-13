# Tabcut 视频商品价格分离与筛选设计

最后更新：2026-05-13

## 背景

Tabcut 视频榜返回的 `itemList[]` 中包含视频对应商品信息，已有采集设计把 `skuPrice` 识别为关键字段。当前实现已在商品快照 `tabcut_goods_snapshots.price_min / price_max` 中分离商品价格，也会在视频列表响应阶段从商品快照或 `video_raw_json.itemList[].skuPrice` 临时补出 `primary_item_price_min`。但视频候选表 `tabcut_video_candidates` 没有独立价格字段，后端也没有价格筛选参数，无法稳定支持后续前端按价格筛选。

## 目标

- 在视频采集标准化阶段分离视频对应商品价格。
- 在 `tabcut_video_candidates` 保存候选视频对应商品的 `primary_item_price_min / primary_item_price_max / price_currency`。
- 在视频候选查询 API 支持 `min_item_price` 和 `max_item_price` 参数。
- 提供一次性回填工具，把已有候选数据中的价格从 `candidate_json`、`tabcut_videos.raw_json` 和 `tabcut_goods_snapshots` 回填到新列。

## 非目标

- 不新增前端筛选控件；前端后续只需要调用已准备好的参数。
- 不改变 Tabcut 登录态、CDP、请求节流、定时任务时间。
- 不连接 Windows 本机 MySQL `127.0.0.1:3306` 做验证。

## 数据模型

新增迁移 `db/migrations/2026_05_13_tabcut_video_candidate_price.sql`：

- `tabcut_video_candidates.primary_item_price_min DECIMAL(18,4) NULL`
- `tabcut_video_candidates.primary_item_price_max DECIMAL(18,4) NULL`
- `tabcut_video_candidates.price_currency VARCHAR(16) NULL`
- `idx_tabcut_video_candidates_price (biz_date, region, primary_item_price_min)`

价格语义：

- `primary_item_price_min` 是列表和筛选优先使用的显示价格。
- `primary_item_price_max` 用于保留价格区间；没有区间时与 min 相同。
- `price_currency` 保存 Tabcut/TikTok 返回的货币符号或币种，缺失时前端可继续默认 `$`。

## 采集与回填

标准化优先级：

1. 视频接口主商品：`itemList[0].skuPrice` / `itemList[0].priceAmount` / `itemList[0].priceList`。
2. 分析视频接口根字段：`priceAmount` / `priceList` / `priceOrigin`。
3. 商品榜标准化结果：`price_min / price_max`。

回填工具 `tools/tabcut_price_backfill.py`：

- 扫描 `tabcut_video_candidates` 中价格为空的行。
- 解析 `candidate_json.video`、`candidate_json.video.raw`、`tabcut_videos.raw_json`、`candidate_json.goods`。
- 若原始视频数据缺价格，兜底使用同 `(biz_date, region, primary_item_id)` 的商品快照聚合价格。
- 支持 `--dry-run` 输出统计，不写库。

## API

`GET /xuanpin/api/tabcut/videos` 与旧别名 `GET /medias/api/tabcut-selection/videos` 透传以下参数：

- `min_item_price`: 数字，按 `primary_item_price_min >= value` 过滤。
- `max_item_price`: 数字，按 `primary_item_price_min <= value` 过滤。

响应继续返回：

- `primary_item_price_min`
- `primary_item_price_max`
- `price_currency`
- `currency_symbol`，由 service 用 `price_currency` 或 raw JSON 兜底补齐。

## 验证

- `pytest tests/test_tabcut_selection_scoring.py tests/test_tabcut_selection_store.py tests/test_tabcut_crawler.py tests/test_tabcut_selection_schema.py tests/test_tabcut_price_backfill.py -q`
- `python -m tools.tabcut_price_backfill --dry-run` 需要在已配置非 Windows 本机 MySQL 的环境执行；当前开发 worktree 没有 DB 环境时只跑单元测试。
