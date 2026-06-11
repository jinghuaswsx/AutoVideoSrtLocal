# 广告预警模块设计

## 背景与目标

系统每天从 Meta 广告后台拉取大量广告数据并汇总到 `media_product_lang_ad_summary_cache`。目前运营人员需要**手动**逐个查看各语言广告的 ROAS 数据才能发现亏损仍在投放的广告，效率低且容易遗漏。

本模块的目标：**自动识别每个商品×语言维度的低 ROAS 仍在投放的广告，集中展示预警信息并提供研判依据，辅助运营决策是否关停。**

### 核心指标

- **保本 ROAS（参考值）：** 1.5
- **阈值可配置：** 通过系统设置灵活调整

### 关键原则

- **辅助决策，不自动化关停** — 仅展示数据和研判结论，关停操作仍由运营人员在 Meta 后台执行
- **基于规则研判，不引入 LLM** — 研判结论由确定性规则引擎得出，不依赖大模型
- **轻量无外部依赖** — 不新增 JS 图表库，趋势图用服务端内联 SVG 渲染
- **不新增数据库表** — 完全复用现有缓存和明细表

---

## 数据架构

### 无需新增表

| 数据用途 | 来源 | 说明 |
|---------|------|------|
| 预警主判断 | `media_product_lang_ad_summary_cache` | 字段 `ad_roas`, `active_7d_ad_spend_usd`, `ad_spend_usd` |
| 商品信息 | `media_products` | 商品名称、编码、店铺等 |
| 近 N 日趋势 | `meta_ad_daily_ad_metrics` | 按 `product_id` + 语言推导条件查每日 `spend_usd`, `purchase_value_usd` |
| 运行时长 | `meta_ad_daily_ad_metrics` | 取最早 `meta_business_date` 和活跃天数 |
| 账户信息 | `meta_ad_accounts` | 广告账户 code、store_codes 映射 |
| 阈值配置 | `system_settings` | 新增 key `ad_alert_roas_threshold`（JSON 字段） |

### 语言匹配策略

复用 `media_product_lang_ad_summary_cache._LANG_REFRESH_SQL` 中已实现的语言推导逻辑：
- `market_country` → lang 映射（US/GB/AU → en, DE → de, FR → fr...）
- `ad_name` 和 `normalized_ad_code` 匹配 `media_items.filename`

趋势查询时使用同一套映射关系从 `meta_ad_daily_ad_metrics` 取数据。

---

## 研判规则引擎

### 预警触发条件（AND）

1. `ad_roas < threshold`（默认 < 1.5）
2. `active_7d_ad_spend_usd > 0`（近 7 天仍在消耗预算）
3. `ad_spend_usd > 0`（有过投放记录）

### 规则维度

| 维度 | 计算方式 | 输出值 |
|------|---------|--------|
| 亏损严重度 | `ROAS < 1.0` → 重度；`1.0 ≤ ROAS < 1.3` → 中度；`1.3 ≤ ROAS < threshold` → 轻度 | `严重 / 中度 / 轻度` |
| 趋势方向 | 比较近 7 天日均 ROAS vs 过去 7-14 天日均 ROAS | `恶化 / 持平 / 改善` |
| 运行阶段 | `active_days < 7` → 学习期；`≥ 7` → 稳定期 | `学习期 / 稳定期` |

### 研判结论

| 结论 | 条件 | 文案 |
|------|------|------|
| **建议关停** | 严重度=重度 AND 稳定期 | ROAS 低于 1.0 且已运行超过 7 天，持续亏损建议关停止损 |
| **建议观察** | 学习期（无论严重度） | 广告尚在 Meta 学习期（不到 7 天），可再观察几天 |
| **建议优化** | 中/轻度 AND 稳定期 AND 趋势=恶化 | ROAS 偏低且近期趋势持续恶化，建议优化素材或调整受众 |
| **建议暂缓** | 中/轻度 AND 稳定期 AND 趋势=改善/持平 | ROAS 虽低于阈值但近期有回升趋势，可暂缓关停继续观察 |

---

## 后端架构

### `appcore/ad_alerts.py` — 核心逻辑层

```
appcore/ad_alerts.py
├── 常量
│   ├── ALERT_THRESHOLD_SETTING_KEY = "ad_alert_roas_threshold"
│   └── DEFAULT_THRESHOLD = 1.5
├── get_threshold() → float                  # 读配置
├── set_threshold(value: float)               # 写配置
├── get_alerts(threshold, lang_filter, severity_filter, search)
│   └── → list[AlertItem]                     # 预警主列表
├── get_alert_detail(product_id, lang)
│   └── → AlertDetail                        # 单条详情 + 趋势
├── get_trend_series(product_id, lang, days)
│   └── → list[DailyPoint]                   # 趋势序列
├── judge_alert(roas, active_days, trend)     # 规则引擎
│   └── → Judgment                           # 研判结论
└── TrendDirection enum, Severity enum, AlertItem / AlertDetail dataclass
```

### `web/routes/ad_alerts.py` — Flask 路由

```
web/routes/ad_alerts.py
├── GET /ad-alerts              → 预警列表页
├── GET /ad-alerts/api/list     → 列表数据 JSON
├── GET /ad-alerts/api/detail   → 详情数据 JSON
└── POST /ad-alerts/api/threshold  → 更新阈值
```

### 数据流

```
用户打开 /ad-alerts
    → GET 路由渲染 Jinja2 模板
    → 模板内嵌初始数据（服务端预渲染）
    → 前端进一步交互异步调用 /api/list, /api/detail

页面加载 ad-alerts.html
    → GET /ad-alerts/api/list?threshold=1.5
    → appcore/ad_alerts.get_alerts(...)
        → SELECT FROM media_product_lang_ad_summary_cache
            WHERE ad_roas < threshold AND active_7d_ad_spend_usd > 0
        → JOIN media_products 取商品信息
        → 对每条结果调用 judge_alert()
        → 返回 list[AlertItem]

用户展开详情
    → GET /ad-alerts/api/detail?product_id=123&lang=en
    → appcore/ad_alerts.get_alert_detail(...)
        → 查缓存表获取累计数据
        → get_trend_series() → 查 daily 表近 14/30 天
        → 返回 detail + trend_data
```

---

## 前端设计

### 页面结构

#### 筛选栏
- 严重度筛选：全部 / 重度 / 中度 / 轻度
- 搜索框：按商品名称/编码搜索
- 刷新按钮
- 阈值显示 + 快捷设置按钮

#### 预警列表卡片
每个预警卡片展示：
```
┌─────────────────────────────────────────────────────────┐
│ 🌐 EN · 商品名称 ABC123                         店铺标签 │
│ ROAS: 0.87 (严重) ｜ 消耗: $1,240 ｜ 运行 23 天        │
│ 近7天趋势: 🔻 恶化 ｜ 预估亏损: -$165                 │
│ ⚠️ 建议关停：ROAS 低于 1.0 且已运行超 7 天...         │
│ [查看详情 ▸]                                            │
└─────────────────────────────────────────────────────────┘
```

#### 详情弹窗

点击"查看详情"弹出一个 Ocean Blue 风格的 modal，包含：
1. 基本信息区（商品、语言、店铺、账户）
2. 趋势图区（SVG 折线图，14 天 spend + purchase）
3. 累计数据汇总表
4. 研判结论区（带颜色标签和详细文案）

### 趋势图实现

**纯内联 SVG 渲染，不引入任何第三方图表库：**
- 后端 `get_trend_series()` 返回 `[{"date": "2026-05-28", "spend": 45.2, "purchase": 38.1}, ...]` 数组
- 模板中 Python 计算 SVG path 的 points 坐标
- 两条折线（spend 蓝色, purchase 绿色）+ X 轴日期标签
- 约 30 行模板代码即可实现

### 侧栏入口

在 `layout.html` 中新增管理员可见的独立左侧菜单入口。入口不放入"数据看板"折叠菜单内部，而是紧跟在"数据看板"集合菜单之后，避免普通数据分析权限控制影响广告预警模块的可见性。

```html
<a href="/ad-alerts" class="...">
  🔔 广告预警
</a>
```

### 阈值配置界面

在 `admin.settings` 页面的 ROAS 设置区域旁边新增一个阈值输入框，写入 `system_settings` 的 `ad_alert_roas_threshold` key。

---

## 涉及文件清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `appcore/ad_alerts.py` | **新增** | 核心逻辑：预警查询、规则引擎、趋势数据 |
| `web/routes/ad_alerts.py` | **新增** | Flask Blueprint，注册 `bp_ad_alerts` |
| `web/templates/ad_alerts.html` | **新增** | 预警列表页 |
| `web/templates/ad_alerts_detail.html` | **新增** | 详情 modal 片段 |
| `web/templates/layout.html` | **修改** | 新增侧栏菜单入口 |
| `web/routes/admin.py` | **修改** | 阈值配置 UI（admin 设置页） |
| `web/__init__.py` | **修改** | 注册新蓝图 |
| `AGENTS.md` | **修改** | 模块级引用（可选） |

---

## 实现计划

### Phase 1: 核心逻辑（appcore/ad_alerts.py）
1. 阈值配置读写（get/set threshold）
2. 预警主列表查询（get_alerts）
3. 规则引擎（judge_alert）
4. 趋势数据查询（get_trend_series）

### Phase 2: 后端路由（web/routes/ad_alerts.py）
1. Blueprint 注册
2. 列表页 GET + API 接口
3. 详情 API 接口

### Phase 3: 前端页面
1. 列表页模板 + CSS
2. 详情 modal
3. 趋势图 SVG 渲染
4. 侧栏菜单入口

### Phase 4: 配置管理
1. admin 设置页阈值配置
2. 前端阈值显示 + 快捷修改

---

## 附录：规则引擎伪代码

```python
def judge_alert(roas: float, active_days: int, trend_7d: list[float], trend_14d: list[float]) -> Judgment:
    # 严重度
    if roas < 1.0:
        severity = "severe"
    elif roas < 1.3:
        severity = "moderate"
    else:
        severity = "mild"

    # 趋势方向
    recent_avg = avg(trend_7d[:7]) if len(trend_7d) >= 7 else avg(trend_7d)
    prior_avg = avg(trend_14d[7:14]) if len(trend_14d) >= 14 else None
    if prior_avg is not None and recent_avg < prior_avg * 0.9:
        trend = "worsening"
    elif prior_avg is not None and recent_avg > prior_avg * 1.1:
        trend = "improving"
    else:
        trend = "stable"

    # 运行阶段
    phase = "learning" if active_days < 7 else "stable"

    # 结论
    if severity == "severe" and phase == "stable":
        conclusion = "建议关停"
        reason = "ROAS 低于 1.0 且已运行超过 7 天，持续亏损，建议尽快关停止损"
    elif phase == "learning":
        conclusion = "建议观察"
        reason = "广告尚在 Meta 学习期（不到 7 天），ROAS 数据尚不稳定，可再观察几天"
    elif severity in ("moderate", "mild") and phase == "stable" and trend == "worsening":
        conclusion = "建议优化"
        reason = "ROAS 偏低且近期趋势持续恶化，建议优化广告素材或调整受众定向"
    else:
        conclusion = "建议暂缓"
        reason = "ROAS 虽低于阈值但近期有回升迹象，可暂缓关停继续观察"

    return Judgment(severity=severity, trend=trend, phase=phase, conclusion=conclusion, reason=reason)
```
