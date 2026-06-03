# TABCUT 商品榜日周月采集与展示设计

最后更新：2026-06-03

## 背景

TABCUT 现有内部页已经展示视频榜和商品快照，但商品榜数据主要来自既有类目 Top 快照，不能直接复刻 Tabcut 原站「商品榜」下的榜单结构。新增能力需要采集美国站商品榜里的「商品热销榜」和「新品榜」，并在内部 TABCUT 菜单下用子 Tab 展示日榜、周榜、月榜。

## 范围

- 国家固定为 `US`。
- 榜单类型：`hot`（商品热销榜）、`new`（新品榜）。
- 周期：`1d`（日榜）、`7d`（周榜）、`30d`（月榜）。
- 默认展示商品热销榜月榜，支持切换榜单类型和周期。
- 入库复用 `tabcut_goods` 和 `tabcut_goods_snapshots`，通过 `source` 区分 `goods_hot_1d`、`goods_hot_7d`、`goods_hot_30d`、`goods_new_1d`、`goods_new_7d`、`goods_new_30d`。
- 不新增 schema；原始响应保存在 `raw_json` / `snapshot_json`，缺失的字段由前端和 service 从原始 JSON 补齐。

## 采集

- 新增商品榜 URL 构造函数，集中映射榜单类型、周期和 Tabcut trpc 参数。
- 新增 runner 函数采集所有商品榜组合，分页保存原始 JSON、CSV 摘要，并调用现有 `normalize_goods_row`、`store.upsert_goods`、`store.upsert_goods_snapshot` 入库。
- 采集前仍走现有 CDP client，自动检测登录状态；如果服务器没有 TABCUT 登录凭据且浏览器仍是游客态，采集应失败并给出明确错误，不写游客残缺数据。

## 前端

- `/xuanpin/tabcut` 保留现有视频榜。
- 商品榜视图增加子 Tab：商品热销榜、新品榜；周期切换：日榜、周榜、月榜。
- 表格列参考 Tabcut 原站：排名、商品、价格、佣金比例、总销量、周期内销量、周期内销售额、周期内销量增长率、总关联视频、操作。
- 查询参数传递 `goods_rank_kind` 和 `goods_rank_period`，后端白名单过滤。

## 验证

- 单测覆盖 URL 参数映射、采集计划、store 过滤、service hydration、路由/模板关键控件。
- 不连接 Windows 本机 MySQL；需要真实采集时只在服务器环境运行。
