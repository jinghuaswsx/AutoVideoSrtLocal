# 明空产品库资产去重与店小秘 Top500 同步

2026-06-03 update: DXM02 Listing 采集口径已改为“近 30 天 `paidProductCount > 10` 全量归档”，不再默认 Top500；见 `docs/superpowers/specs/2026-06-03-dxm02-listing-30d-min-sales-design.md`。

## 背景

店小秘 Listing 快照是按日期归档的事实表，同一个商品会在多个 `snapshot_date` 中重复出现。商品主图、详情图、明空中文名和首个素材链接属于商品主体，不属于每天的排名行；继续写在 `dianxiaomi_rankings` 上会让 79 天历史快照重复承载同一份资产数据。

## 设计

- 新增 `dianxiaomi_product_assets`，按产品维度保存主图 URL、本地 object key、详情图 JSON、中文名、首个明空素材和错误信息。
- `dianxiaomi_rankings` 保留销量、排名、店铺、金额、`product_code` 等快照事实；新同步不再向排名行写入资产列。
- 产品库 API 优先 join `dianxiaomi_product_assets`，旧的排名行资产列仅作为迁移期 fallback。
- 2026-06-03 起，店小秘定时同步默认 `--snapshot-window-days 30 --target-rows 0 --min-sales-count 10`，每天保留近 30 天销量大于 10 的全量 Listing；Top500 裁剪只作为本历史方案的旧背景。

## 历史数据

迁移先把已有 `dianxiaomi_rankings` 中的资产字段汇总进 `dianxiaomi_product_assets`。确认产品库从产品资产表读取正常后，再执行压缩清理，把排名表上的重复资产列置空，只保留 `product_code` 做关联。

历史快照也按同一口径裁剪：每个 `snapshot_date` 保留 `rank_position <= 500` 的行，删除 500 名之后的历史行。这样既减少重复资产字段，也把长期快照行数从“每天约 1800 条”收敛到“每天最多 500 条”。

## 验证

- 单元测试覆盖 30 天销量大于 10 的全量采集、产品资产 upsert 去重、产品库 API 从资产表读取、migration 建表。
- 发布后检查最新快照不应再被 500 行截断，历史资产读取不应依赖每一行的重复字段。
