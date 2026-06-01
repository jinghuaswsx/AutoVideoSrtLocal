# 2026-06-01 广告分析未匹配广告计划子 Tab 设计

## 背景

`/order-analytics/ads-view` 的广告分析面板已经包含 `概览 / Campaign / Ad Set / Ad / 人工录入` 子 Tab。概览接口 `/order-analytics/ad-summary` 已返回 `unmatched`，代表在素材管理库 `media_products` 中没有匹配产品的 Campaign 级广告计划，并且现有页面已经提供人工配对弹窗。

## 目标

新增一个广告分析子 Tab：`未匹配广告计划`。用户切入该 Tab 后，可以直接查看所选日期范围、广告户和搜索条件下，素材管理库里没有相关产品的广告计划，并继续使用现有“配对”按钮把广告计划绑定到素材库产品。

## 范围

- 在 `#panelAds` 的子 Tab 导航中新增 `未匹配广告计划`。
- 复用现有概览日期、广告户、搜索输入和 `/order-analytics/ad-summary` 数据源。
- 新 Tab 只渲染 `data.unmatched`，不展示已匹配产品汇总表。
- 新 Tab 的查询按钮、日期快捷项、广告户筛选和 Enter 搜索沿用概览行为。
- 未匹配列表继续使用现有 `openAdMatchModal(row)` 配对流程。

## 非目标

- 不新增数据库表或字段。
- 不新增后端接口。
- 不改变现有概览表、Campaign / Ad Set / Ad 列表和详情的口径。
- 不在本地 Windows MySQL 上做任何验证。

## 前端设计

新增 `data-ads-subtab="unmatched-campaigns"` 的按钮和 `data-subpanel="unmatched-campaigns"` 面板。面板内放置与概览一致的筛选条，但使用独立 DOM id，避免与概览表互相抢状态。

前端新增一个轻量状态：

- `adUnmatchedState.rows` 保存最近一次 `/ad-summary` 返回的 `unmatched`。
- `loadAdUnmatchedCampaigns()` 读取未匹配 Tab 的控件，调用 `/order-analytics/ad-summary`。
- `renderAdUnmatchedCampaigns(rows)` 复用未匹配表列和配对按钮渲染逻辑。

日期默认值沿用广告分析默认 Meta 业务日“今天”。切换到该子 Tab 时首次加载，之后筛选变化只刷新当前未匹配 Tab。

## 测试

- 模板测试：页面应出现 `未匹配广告计划` 子 Tab、独立搜索框、独立广告户筛选和查询按钮。
- 模板测试：未匹配 Tab 应调用 `/order-analytics/ad-summary`，并调用 `renderAdUnmatchedCampaigns` 与 `openAdMatchModal`。
- 现有广告分析模板测试继续通过。
