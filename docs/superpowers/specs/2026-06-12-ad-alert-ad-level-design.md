# 广告预警 AD 级详情 + Gemini 评估设计

## 背景与目标

当前广告预警模块只展示到**商品×语言**维度（一条记录包含该语言下所有广告的汇总 ROAS）。但同一个商品同一种语言可能有多条广告投放不同的国家市场，其中可能：
- 英语美国广告赚钱，但英语澳大利亚广告亏钱
- 德语德国广告 ROAS=2.0，但德语奥地利广告 ROAS=0.8

运营人员需要在查看某语言预警详情时，能看到**该语言下每一条 AD** 的独立数据和评估，辅助决策具体关停哪条 AD。

## 关键原则

- **沿用现有框架** — 不新增页面，在已有详情弹窗中扩展 AD 列表
- **Gemini 3.5 Flash 辅助研判** — 调用 LLM 评估一组亏损 AD 并给出分组建议，不自动执行
- **只建议，不动作** — 不调用 Meta API 关停
- **不新增数据库表** — 直接从 `meta_ad_daily_ad_metrics` 聚合查询

---

## 数据架构

### 数据来源

| 字段 | 来源表 | 说明 |
|------|--------|------|
| 商品、语言 | `media_product_lang_ad_summary_cache` | 已有，用于定位预警记录 |
| 国家 | `meta_ad_daily_ad_metrics.market_country` | 已解析的市场国家代码（US/DE/FR/…） |
| AD 名称 | `meta_ad_daily_ad_metrics.ad_name / normalized_ad_code` | Meta AD 标识 |
| AD 花费 | `meta_ad_daily_ad_metrics.spend_usd` | 按 product + country + ad 聚合 |
| AD 购买价值 | `meta_ad_daily_ad_metrics.purchase_value_usd` | 同上 |
| AD 活跃天数 | 按 meta_business_date COUNT DISTINCT | |

### AD 级聚合查询

```sql
SELECT
  market_country,
  ad_name,
  normalized_ad_code,
  SUM(spend_usd) AS total_spend,
  SUM(purchase_value_usd) AS total_purchase,
  COUNT(DISTINCT meta_business_date) AS active_days,
  CASE WHEN SUM(spend_usd) > 0
    THEN ROUND(SUM(purchase_value_usd) / SUM(spend_usd), 4)
  END AS ad_roas
FROM meta_ad_daily_ad_metrics
WHERE product_id = %(product_id)s
  AND market_country IS NOT NULL
  AND market_country <> ''
  AND COALESCE(spend_usd, 0) > 0
GROUP BY product_id, market_country, ad_name, normalized_ad_code
ORDER BY ad_roas ASC
```

### 国家→语言标签映射

复用 `appcore/ad_alerts.py` 中已有的 `_COUNTRY_LANG_CASE_SQL` 和 `_LANG_LABELS`。

---

## Gemini 评估设计

### LLM Use Case 注册

在 `appcore/llm_use_cases.py` 新增：
- `code`: `ad_alert.evaluate`
- `provider`: `openrouter` / `gemini_aistudio`
- `model`: `google/gemini-3.5-flash` / `gemini-3.5-flash`

### 评估流程

```
用户打开预警详情弹窗 → 自动加载该语言的 AD 列表
                       → 展示 AD 表格（按 ROAS 升序排列）
                       → 用户点击「AI 评估」按钮
                       → 前端发请求 POST /ad-alerts/api/evaluate
                       → 后端收集该语言下所有亏损 AD 数据
                       → 调用 Gemini 3.5 Flash
                       → 返回结构化评估结果
                       → 前端渲染评估面板
```

### 评估 Prompt 设计

```json
{
  "messages": [
    {
      "role": "system",
      "content": "你是一个 Meta 广告优化分析师。你的任务是根据广告投放数据分析一组广告的表现，给出每条广告的关停建议。重点关注亏损（ROAS < 1.5）但仍持续投放的广告。\n\n输出格式必须是一段 JSON 数组，每个元素包含: country (国家代码)、ad_name (广告名)、roas (ROAS 数值)、judgment (建议: 关停/优化/观察)、reason (简短理由)"
    },
    {
      "role": "user",
      "content": "以下是商品「{product_name}」(编码: {product_code}) 在 {lang_label} 语言下的广告投放数据，保本 ROAS 为 {threshold}。请分析并给出建议：\n\n广告列表：\n{ad_list_text}\n\n请重点指出 ROAS 低于保本线且仍在消耗的广告，判断是否应关停。"
    }
  ]
}
```

### 评估响应格式

```python
@dataclass
class AdEvaluation:
    country: str
    ad_name: str
    roas: float
    judgment: str  # 关停 / 优化 / 观察
    reason: str
```

Gemini 返回 JSON 后解析为 `AdEvaluation[]`，前端按 judgment 分组展示。

---

## 前端设计

### 现有详情弹窗扩展

在详情弹窗中新增一个区域（位于趋势图下方、研判结论之前）：

```
┌──────────────────────────────────────────────────────────┐
│  🔔 广告预警详情                              [关闭]    │
├──────────────────────────────────────────────────────────┤
│  商品: ABC123  ·  语言: 英语 (EN)                        │
├──────────────────────────────────────────────────────────┤
│  ROAS 趋势图（已有，保持不变）                            │
├──────────────────────────────────────────────────────────┤
│  累计数据汇总表（已有，保持不变）                          │
├──────────────────────────────────────────────────────────┤
│  📋 该语言下的广告投放列表                                 │
│                                                          │
│  ┌──────────┬──────────────┬───────┬───────┬─────────┐  │
│  │ 国家      │ AD 名称      │ 花费   │ ROAS  │ 活跃天数 │  │
│  ├──────────┼──────────────┼───────┼───────┼─────────┤  │
│  │🇺🇸 US     │ ABC123_en_01 │ $500  │ 0.87  │ 23天    │  │
│  │🇦🇺 AU     │ ABC123_au_01 │ $300  │ 1.12  │ 15天    │  │
│  │🇬🇧 GB     │ ABC123_uk_01 │ $200  │ 1.35  │ 10天    │  │
│  └──────────┴──────────────┴───────┴───────┴─────────┘  │
│                                                          │
│  [🤖 AI 评估]  ← 点击后调用 Gemini                         │
│                                                          │
│  ┌─ AI 评估结果 ────────────────────────────────────────┐ │
│  │                                                     │ │
│  │  🚫 建议关停                                         │ │
│  │  • US · ABC123_en_01 — ROAS 0.87，已消耗 $500，      │ │
│  │    持续亏损远超7天，建议立即关停止损                   │ │
│  │                                                     │ │
│  │  ⚠️ 建议优化                                         │ │
│  │  • AU · ABC123_au_01 — ROAS 1.12，近7天趋势走低，    │ │
│  │    建议优化素材或调整定向后再观察一周                   │ │
│  │                                                     │ │
│  │  👁 建议观察                                         │ │
│  │  • GB · ABC123_uk_01 — ROAS 1.35 接近保本线，        │ │
│  │    活跃仅10天，尚有优化空间，可暂缓处理                │ │
│  └─────────────────────────────────────────────────────┘ │
├──────────────────────────────────────────────────────────┤
│  研判结论（已有）                                          │
└──────────────────────────────────────────────────────────┘
```

### 技术方案

- AD 列表在详情加载时自动请求（不额外等用户操作）
- AI 评估按钮点击时才调用 Gemini（节省成本）
- 评估结果缓存在前端内存中，关闭弹窗即释放

---

## 后端架构

### `appcore/ad_alerts.py` 新增函数

```
├── get_ad_list(product_id, lang) → list[AdListItem]  # 新增
├── evaluate_ads(product_id, lang, threshold)          # 新增
│   → list[AdEvaluation]                               # 调用 Gemini
```

### 路由新增

- `GET /ad-alerts/api/ad-list?product_id=X&lang=en` → AD 列表
- `POST /ad-alerts/api/evaluate` (JSON body: `{"product_id": X, "lang": "en"}`) → Gemini 评估结果

### 新增文件

| 操作 | 文件 | 说明 |
|------|------|------|
| Modify | `appcore/ad_alerts.py` | 新增 `get_ad_list()`, `evaluate_ads()`, 数据模型 |
| Modify | `web/routes/ad_alerts.py` | 新增 2 个路由 |
| Modify | `web/templates/ad_alerts.html` | 详情弹窗新增 AD 列表 + AI 评估区域 |
| Modify | `appcore/llm_use_cases.py` | 注册 `ad_alert.evaluate` use case |

---

## 实现计划

### Phase 1: 后端数据查询
1. 新增 `AdListItem` dataclass
2. 实现 `get_ad_list(product_id, lang)` 聚合查询
3. 注册 `ad_alert.evaluate` use case

### Phase 2: Gemini 评估
1. 实现 `evaluate_ads()` 调用 Gemini
2. 设计并固化 prompt
3. 解析 JSON 响应为结构化结果

### Phase 3: 路由
1. `GET /ad-alerts/api/ad-list`
2. `POST /ad-alerts/api/evaluate`

### Phase 4: 前端
1. 详情弹窗 AD 列表区域
2. AI 评估按钮 + 加载态
3. 评估结果渲染
