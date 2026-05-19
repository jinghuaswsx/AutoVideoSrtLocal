# 明空产品库资产去重与店小秘 Top500 同步

## 背景

店小秘 Listing 快照是按日期归档的事实表，同一个商品会在多个 `snapshot_date` 中重复出现。商品主图、详情图、明空中文名和首个素材链接属于商品主体，不属于每天的排名行；继续写在 `dianxiaomi_rankings` 上会让 79 天历史快照重复承载同一份资产数据。

## 设计

- 新增 `dianxiaomi_product_assets`，按产品维度保存主图 URL、本地 object key、详情图 JSON、中文名、首个明空素材和错误信息。
- `dianxiaomi_rankings` 保留销量、排名、店铺、金额、`product_code` 等快照事实；新同步不再向排名行写入资产列。
- 产品库 API 优先 join `dianxiaomi_product_assets`，旧的排名行资产列仅作为迁移期 fallback。
- 店小秘定时同步默认 `--target-rows 500`，每天只保留近 7 天窗口销量榜前 500 名；`--target-rows 0` 只作为人工全量归档开关。

## 历史数据

迁移先把已有 `dianxiaomi_rankings` 中的资产字段汇总进 `dianxiaomi_product_assets`。确认产品库从产品资产表读取正常后，再执行压缩清理，把排名表上的重复资产列置空，只保留 `product_code` 做关联。

历史快照也按同一口径裁剪：每个 `snapshot_date` 保留 `rank_position <= 500` 的行，删除 500 名之后的历史行。这样既减少重复资产字段，也把长期快照行数从“每天约 1800 条”收敛到“每天最多 500 条”。

## 验证

- 单元测试覆盖默认 Top500、产品资产 upsert 去重、产品库 API 从资产表读取、migration 建表。
- 发布后检查最新快照条数应收敛到 500 左右，历史资产读取不应依赖每一行的重复字段。
