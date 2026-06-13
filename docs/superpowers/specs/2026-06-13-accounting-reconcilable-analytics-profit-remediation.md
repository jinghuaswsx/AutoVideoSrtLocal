# 会计可对账数据分析与利润核算整改

最后更新：2026-06-13

## 背景

数据分析、产品盈亏明细、订单利润核算三个板块已经具备基础统计能力，但从会计复核角度仍存在广告费来源不统一、开放日 realtime 与日终 daily 对账脱节、明细与汇总口径不一致、导出缺少对账字段等问题。整改目标是让页面、API、导出在同一期间、同一筛选条件下能解释收入、费用、利润和广告费差异来源。

## 会计口径

- 业务日期沿用 Meta 业务日，按北京时间 16:00 切日；开放业务日允许使用 realtime 快照兜底。
- 商品收入口径为商品金额加订单级运费分摊，利润计算必须显式扣除 Shopify 费用、广告费、采购成本、头程/物流成本、退货预留。
- 广告费分为已分摊广告费、未分摊广告费、总广告费；总利润必须扣除总广告费，不能只扣已分摊广告费。
- 缺采购价或物流费的行可以参与估算利润，但 UI、API、导出必须标明估算字段，避免把估算利润表述成实际完备利润。
- 所有面向数据分析、订单利润、产品盈亏的 JSON 输出应携带 `data_quality`；若不能完整对账，必须暴露原因而不是静默省略。

## Task 清单

### T1 统一广告费来源与 data_quality 对账

- 建立共享的广告费区间读取口径：同一函数负责 daily、开放日 realtime、国家维度和全国家维度的总广告费。
- `data_quality` 的广告费对账必须使用同一来源解析，避免开放日 realtime 数据被 daily-only 对账误判。
- 对账 payload 必须清楚区分 `allocated_ad_spend_usd`、`unallocated_ad_spend_usd`、`total_ad_spend_usd`、`source_mode`。

验收：
- 开放日全国家、开放日单国家、历史日终三类日期范围均能返回可解释的 `data_quality`。
- 已分摊广告费加未分摊广告费与总广告费在容差内一致时，状态为通过。

### T2 订单利润核算总账、列表、明细统一

- `/order-profit/api/summary`、`/order-profit/api/orders`、`/order-profit/api/orders/<id>`、`/order-profit/api/lines`、`/order-profit/api/loss_alerts` 都必须携带统一 `data_quality`。
- 分页订单列表不得使用当前页广告费去对账整个日期窗口，应使用同一筛选条件下的窗口汇总。
- 订单详情和亏损预警必须应用与订单列表一致的开放日广告费调整，或明确标记为落库口径。
- 前端展示必须区分实际完备利润、含估算利润、未分摊广告调整后的总利润。

验收：
- 同一订单在列表、展开明细、亏损预警中的广告费和利润解释一致。
- 分页不会改变期间广告费对账结果。

### T3 产品盈亏明细、报表、广告 tab 统一

- 产品盈亏列表、产品盈亏报表、广告 tab 必须使用同一广告费来源。
- 单国家开放日不得静默显示广告费 0；若 realtime 国家维度不可用，必须在 `data_quality` 标记缺源。
- 产品广告 tab 在开放日应使用 realtime 兜底，或显式显示日终缺源状态。
- 汇总必须披露已分摊广告费、未分摊广告费、总广告费和扣总广告后的利润。

验收：
- 相同日期、站点、国家筛选下，产品列表、报表、广告 tab 的总广告费可以互相对上。
- 国家筛选开放日不会把未知广告费当成 0 利润。

### T4 数据分析模块补齐会计质量条

- 实时大盘继续作为 realtime 强口径入口，保留 data_quality。
- 旧产品看板、国家看板、真实 ROAS、月/日/周明细 API 应尽量补齐 `data_quality` 或明确无法对账原因。
- 趋势类逻辑不得使用自然北京时间日期替代 Meta 业务日。

验收：
- 数据分析模块主要 JSON 输出都能说明数据源、业务日范围、广告费对账状态。
- 16:00 切日边界不会把上一业务日未落日终的数据当作 0。

### T5 导出与前端完整披露

- 产品盈亏 Excel 增加数据质量/对账摘要 sheet。
- 总账 sheet 增加已分摊广告费、未分摊广告费、总广告费、利润口径说明。
- 订单利润和产品盈亏前端不再使用“仅含完备行”等会误导估算利润的文案。

验收：
- 页面和导出的会计关键字段一致。
- 用户不用打开代码即可理解利润是否含估算、广告费是否含未分摊。

## 相关锚点

- `AGENTS.md` 数据质量护栏、业务日、订单利润、产品盈亏指引。
- `docs/analytics-data-quality-guardrails.md`
- `docs/superpowers/specs/2026-05-08-analytics-business-date-alignment-fix.md`
- `docs/superpowers/specs/2026-05-10-realtime-dashboard-profit-margin.md`
- `docs/superpowers/specs/2026-05-04-order-level-shipping-cost-design.md`
- `docs/superpowers/specs/2026-05-04-shopify-payments-fee-rules.md`
