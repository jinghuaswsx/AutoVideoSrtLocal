# 订单分析（月度视图）重设计 — 设计文档

- 日期：2026-04-28
- 范围：`/order-analytics` 页面 → `订单分析` Tab → 月度视图
- 不在范围：`产品看板` Tab、`订单导入` Tab、`广告分析` Tab、周度视图、每日明细的核心结构（仅跟随收紧字号）

## 背景与动机

当前页面的"商品 × 国家订单分布"表存在三个问题：

1. **视觉臃肿**：`font-size: 19px` + `padding: 10px 10px`，远比同页面 `产品看板` 的 dashboard 表（13px）重；商品名列 `white-space: normal + word-break: break-word` 导致窄列时一字一行。
2. **国家列宽失控**：列宽未约束，行高被拉得很高，一屏只能看到 1–2 个国家，无法横向比较。
3. **缺少素材覆盖信息**：用户无法在订单分布旁直接看到"该产品该国语种是否有素材、有几条"。当订单很少时，用户分不清是"没素材所以没单"还是"有素材但效果差"。

## 设计目标

- 一屏能稳定看到 **≥ 10 个国家** 的订单分布
- 每个产品 × 每个国家的格子里，同时呈现 **素材数量** 和 **订单数量**
- 视觉风格收紧到 dashboard 表格的精致度（`--text-sm` + 紧凑 padding + sticky header/first-col）

## 数据模型与字段约定

### 国家 → 语种映射（硬编码常量）

放在 `appcore/order_analytics.py`：

```python
COUNTRY_TO_LANG = {
    "US": "en", "GB": "en", "UK": "en",
    "AU": "en", "CA": "en", "IE": "en", "NZ": "en",
    "DE": "de", "AT": "de",
    "FR": "fr",
    "ES": "es",
    "IT": "it",
    "NL": "nl",
    "SE": "sv",
    "FI": "fi",
    "JP": "ja",
    "KR": "ko",
    "BR": "pt-BR",
    "PT": "pt",
}
```

注：当前架构里没有"启用国家"配置，只有"启用语种"。本方案不引入新的 `media_country_configs` 表，未来若需配置化，可平滑迁到 `media_languages.country_codes` 字段。

### 启用国家列表的推导

```python
def get_enabled_country_columns() -> list[dict]:
    """从 media_languages.enabled=1 反推出"启用的国家列表"。

    每个启用语种对应一组国家代码（语种 → COUNTRY_TO_LANG 反向查找）；
    多个国家共享同一语种时（如 US/GB/CA 都对应 en），按 sort_order 内的优先序输出全部。
    返回：[{"country": "US", "lang": "en"}, {"country": "GB", "lang": "en"}, …]
    """
```

固定优先序（同一语种内）：
- `en` → `US, GB, AU, CA, IE, NZ`
- `de` → `DE, AT`
- `pt-BR` → `BR`，`pt` → `PT`
- 其他语种各对应单一国家

### `get_monthly_summary()` 返回值扩展

新增字段：

```python
{
    "products": [...],           # 既有
    "countries": [...],          # 既有（订单数据里实际出现的国家，仍保留供其他可能使用）
    "country_list": [...],       # 既有
    "matrix": {...},             # 既有

    # 新增 ↓
    "country_columns": [
        {"country": "US", "lang": "en"},
        {"country": "GB", "lang": "en"},
        ...
    ],
    "media_counts": {
        product_id: {"en": 3, "de": 2, ...},
        ...
    },
}
```

`media_counts` 直接复用现有 `_count_media_items_by_product()`（已 `WHERE deleted_at IS NULL GROUP BY product_id, lang`），按 `product_id` 过滤后返回。

## 前端视觉规范

### 列结构

```
| 商品 | 总单量 | 订单数 | 收入 | US/en | GB/en | DE/de | FR/fr | ES/es | IT/it | NL/nl | SE/sv | FI/fi | JP/ja | BR/pt-BR | … | 操作 |
```

- 共 ~14 个国家列（启用语种数决定）
- 列宽：商品 240px、汇总三列各 70px、收入 90px、国家列各 80px、操作列 100px
- 1440px 屏：可见 10+ 国家，余下横向滚动
- 1920px 屏：基本无横滚

### 单元格内部（产品 × 国家）

```
┌────────┐
│  📦 3  │ ← 素材数（小灰字 + 图标 12px）
│   23   │ ← 订单数（粗体，accent 主色）
└────────┘
```

**状态分类与色板**：

| 状态 | 素材位 | 订单位 | 整格背景 |
|---|---|---|---|
| 0 单量 + 0 素材 | `—` 灰 | `—` 灰 | `--bg-subtle` |
| ≥1 素材 + 0 单量 | `📦 3` 默认灰 | `0` 灰 | 默认 |
| 0 素材 + ≥1 单量 | `⚠ 0` **红色** (`--danger`) | `23` accent | 默认 |
| ≥1 素材 + ≥1 单量 | `📦 3` 灰 | `23` accent 粗体 | 默认 |

### 商品列内部

```
Last Day 49% OFF Wireless Led Strobe Lights - 4 PCS    ← 单行 ellipsis，hover tooltip
PCK001                                                  ← 小灰字 product_code
```

### 表头改造

```
US      GB      DE      FR
en      en      de      fr      ← 第二行小灰字显示对应语种
```

让用户一眼分辨"这列对哪种素材"。

## 视觉规范（Token）

```css
.oam-table { font-size: var(--text-sm); }
.oam-table th, .oam-table td { padding: 8px var(--space-3); }
.oam-table th { font-weight: 600; background: var(--bg-subtle); }

.oam-col-product { width: 240px; min-width: 240px; max-width: 240px; }
.oam-col-num    { width: 70px; text-align: right; }
.oam-col-revenue { width: 90px; }
.oam-col-country { width: 80px; text-align: center; }
.oam-col-actions { width: 100px; }

.oam-product-name {
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  font-weight: 500;
}
.oam-product-code { font-size: var(--text-xs); color: var(--fg-subtle); }

.oam-cell-country {
  display: flex; flex-direction: column; align-items: center;
  gap: 2px; line-height: 1.3;
}
.oam-cell-media   { font-size: var(--text-xs); color: var(--fg-muted); }
.oam-cell-orders  { font-weight: 600; color: var(--accent); font-size: var(--text-base); }
.oam-cell-warn    { color: var(--danger); }
.oam-cell-empty   { color: var(--fg-subtle); }
.oam-cell-bg-empty { background: var(--bg-subtle); }
```

颜色严格走已有 token，零硬编码、零紫色（hue ≤ 240）。

## 顶部统计 / 底部明细 处理

- 顶部 5 张统计卡（活跃商品 / 总单量 / 总订单数 / 总收入 / 国家数）：保留，仅微调内边距与字号到 dashboard 风格。
- 底部"每日销量明细"：保留结构，跟随主表的字号收紧（19px → 13px、`oa-table` → `oam-table`）。

## 后端改动清单

1. **`appcore/order_analytics.py`**
   - 新增 `COUNTRY_TO_LANG` 常量
   - 新增 `_LANG_TO_COUNTRIES` 反向映射 + 优先序
   - 新增 `get_enabled_country_columns()`
   - `get_monthly_summary(year, month, product_id)` 在返回 dict 末尾追加 `country_columns` 和 `media_counts` 两个字段
2. **`web/routes/order_analytics.py`**
   - `monthly()` 视图函数：仅做序列化兼容（Decimal → float），无逻辑变化

## 前端改动清单

仅修改 `web/templates/order_analytics.html` 内 `panelAnalytics` 月度视图相关：

1. CSS：在 `extra_style` 块底部追加 `.oam-*` 一组类
2. `renderMonthSummary()` 重写表头与表体生成逻辑，按 `data.country_columns` 输出固定列 + 单元格上下两行布局
3. CSV 导出（`exportMonthCSV`）保持兼容，新增"素材数 / 国家"列

`产品看板` Tab 的 `oad-*` 完全不动。

## 测试策略

- **单元测试** `tests/test_order_analytics_dashboard.py` 已有 fixture，新增：
  - `test_get_enabled_country_columns_orders_by_priority`：启用 en/de/fr 时返回 `US,GB,AU,CA,IE,NZ,DE,AT,FR`
  - `test_get_monthly_summary_includes_media_counts`：fixture 插入 1 个产品 + 2 条 `media_items`(en,de)，断言返回的 `media_counts[pid]['en']==1`
- **手工验收**：服务器跑起来后访问 `/order-analytics` → `订单分析` → 月度视图，肉眼比对截图

## 风险与回滚

- 国家代码大小写：订单导入路径已统一为大写国家代码（`appcore/order_analytics.py: parse_shopify_file`），映射表只覆盖大写
- 罕见国家（不在 `COUNTRY_TO_LANG` 里的）：单元格保留订单数显示，素材位显示 `—`，避免数据丢失
- 回滚：单 PR + 单 commit，回退即可还原

## 不做的事（YAGNI）

- 不引入"国家配置"管理界面
- 不动周度视图布局
- 不改 dashboard、广告分析、订单导入相关代码
- 不增加新数据库表/迁移
