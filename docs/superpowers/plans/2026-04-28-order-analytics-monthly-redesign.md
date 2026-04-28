# Order Analytics 月度视图重设计 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `/order-analytics` → `订单分析` Tab → 月度视图的"商品 × 国家"表，从 19px 大字号 + 失控列宽，重做成 dashboard 风格的紧凑表，每个国家单元格上下两行同时显示「素材数量 / 订单数量」。

**Architecture:**
- 后端：`appcore/order_analytics.py` 新增国家↔语种映射常量、`get_enabled_country_columns()` 函数，扩展 `get_monthly_summary()` 返回 `country_columns` + `media_counts`
- 前端：`web/templates/order_analytics.html` 添加 `.oam-*` CSS 类，重写 `renderMonthSummary()` 使用固定列序的紧凑表 + 单元格双行布局
- 不动产品看板 / 广告分析 / 周度视图 / DB schema

**Tech Stack:** Python 3.11、Flask、原生 JavaScript（无框架）、pytest、MySQL（既有）

参考 spec：[docs/superpowers/specs/2026-04-28-order-analytics-monthly-redesign-design.md](../specs/2026-04-28-order-analytics-monthly-redesign-design.md)

---

## Task 1：添加国家↔语种映射常量与启用国家列推导

**Files:**
- Modify: `appcore/order_analytics.py`（在 `_compute_pct_change` 函数定义之前的"分析查询"分隔注释处之前插入）
- Test: `tests/test_order_analytics_dashboard.py`（在文件末尾追加）

- [ ] **Step 1: Write the failing tests**

在 `tests/test_order_analytics_dashboard.py` 末尾追加：

```python
# ── 国家映射 / 启用国家推导 ────────────────────────────────


def test_country_to_lang_canonical_codes_present():
    assert oa.COUNTRY_TO_LANG["US"] == "en"
    assert oa.COUNTRY_TO_LANG["GB"] == "en"
    assert oa.COUNTRY_TO_LANG["UK"] == "en"
    assert oa.COUNTRY_TO_LANG["DE"] == "de"
    assert oa.COUNTRY_TO_LANG["AT"] == "de"
    assert oa.COUNTRY_TO_LANG["BR"] == "pt-BR"
    assert oa.COUNTRY_TO_LANG["PT"] == "pt"


def test_lang_to_countries_uses_priority_order(monkeypatch):
    """同一语种多个国家时，按 LANG_PRIORITY_COUNTRIES 给的固定优先序输出。"""
    enabled = ["en", "de"]
    monkeypatch.setattr(oa, "_load_enabled_lang_codes", lambda: enabled)
    cols = oa.get_enabled_country_columns()
    countries = [c["country"] for c in cols]
    # en → US, GB, AU, CA, IE, NZ；de → DE, AT
    assert countries == ["US", "GB", "AU", "CA", "IE", "NZ", "DE", "AT"]
    # 每列都带 lang 字段
    assert cols[0] == {"country": "US", "lang": "en"}
    assert cols[6] == {"country": "DE", "lang": "de"}


def test_get_enabled_country_columns_skips_unmapped_lang(monkeypatch):
    """启用了 COUNTRY_TO_LANG 没覆盖的语种时，跳过该语种但不报错。"""
    monkeypatch.setattr(oa, "_load_enabled_lang_codes", lambda: ["en", "xx-unknown"])
    cols = oa.get_enabled_country_columns()
    assert all(c["lang"] != "xx-unknown" for c in cols)
    assert {c["country"] for c in cols} == {"US", "GB", "AU", "CA", "IE", "NZ"}


def test_get_enabled_country_columns_full_set(monkeypatch):
    monkeypatch.setattr(
        oa,
        "_load_enabled_lang_codes",
        lambda: ["en", "de", "fr", "es", "it", "ja", "nl", "sv", "fi", "pt-BR"],
    )
    cols = oa.get_enabled_country_columns()
    countries = [c["country"] for c in cols]
    assert countries == [
        "US", "GB", "AU", "CA", "IE", "NZ",
        "DE", "AT",
        "FR",
        "ES",
        "IT",
        "JP",
        "NL",
        "SE",
        "FI",
        "BR",
    ]
```

- [ ] **Step 2: 运行测试确认失败**

```bash
python -m pytest tests/test_order_analytics_dashboard.py -k "country" -q
```

期待：4 个测试 FAIL，错误信息包含 `AttributeError: module 'appcore.order_analytics' has no attribute 'COUNTRY_TO_LANG'`。

- [ ] **Step 3: 实现常量与函数**

在 `appcore/order_analytics.py` 中找到这一行：

```python
# ── 分析查询 ───────────────────────────────────────────
```

在它之前插入下面这段代码（紧跟在 `match_orders_to_products()` 函数定义之后）：

```python
# ── 国家 ↔ 语种映射 ───────────────────────────────────────


COUNTRY_TO_LANG: dict[str, str] = {
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


# 同语种多国家时，列输出顺序固定走这张表（不出现的语种按 dict 插入序）
LANG_PRIORITY_COUNTRIES: dict[str, list[str]] = {
    "en": ["US", "GB", "AU", "CA", "IE", "NZ"],
    "de": ["DE", "AT"],
}


def _load_enabled_lang_codes() -> list[str]:
    """读取 media_languages.enabled=1 的语种 code，按 sort_order 升序。

    与 appcore.medias.list_enabled_language_codes() 等价；这里独立写一份避免
    在订单分析路径上造成循环依赖。
    """
    rows = query(
        "SELECT code FROM media_languages "
        "WHERE enabled=1 ORDER BY sort_order ASC, code ASC"
    )
    return [r["code"] for r in rows]


def get_enabled_country_columns() -> list[dict]:
    """根据 media_languages 启用语种推导出"国家列"序列。

    返回列表如 [{"country": "US", "lang": "en"}, …]，按
    sort_order(语种) → LANG_PRIORITY_COUNTRIES(同语种内部顺序) 双重排序。
    未在 COUNTRY_TO_LANG 里出现的启用语种被静默跳过（不报错）。
    """
    enabled_langs = _load_enabled_lang_codes()
    columns: list[dict] = []
    seen: set[str] = set()

    # 反向构建：lang → [country, ...]，对未在优先表里的语种走 dict 插入序
    lang_to_countries: dict[str, list[str]] = {}
    for country, lang in COUNTRY_TO_LANG.items():
        lang_to_countries.setdefault(lang, []).append(country)
    # 优先表覆盖默认顺序
    for lang, ordered in LANG_PRIORITY_COUNTRIES.items():
        if lang in lang_to_countries:
            lang_to_countries[lang] = ordered

    for lang in enabled_langs:
        countries = lang_to_countries.get(lang)
        if not countries:
            continue
        for country in countries:
            if country in seen:
                continue
            seen.add(country)
            columns.append({"country": country, "lang": lang})

    return columns
```

- [ ] **Step 4: 运行测试确认通过**

```bash
python -m pytest tests/test_order_analytics_dashboard.py -k "country" -q
```

期待：4 个测试 PASS。

- [ ] **Step 5: Commit**

```bash
git add appcore/order_analytics.py tests/test_order_analytics_dashboard.py
git commit -m "$(cat <<'EOF'
feat(order-analytics): country↔lang map + enabled country columns

Adds COUNTRY_TO_LANG dict (19 codes / 12 langs incl. pt-BR/pt split),
LANG_PRIORITY_COUNTRIES for stable multi-country lang ordering, and
get_enabled_country_columns() that derives the column list from
media_languages.enabled=1.

Used by upcoming monthly view redesign to render a fixed country grid
with material counts per product × country language.
EOF
)"
```

---

## Task 2：扩展 `get_monthly_summary()` 返回 `country_columns` + `media_counts`

**Files:**
- Modify: `appcore/order_analytics.py:724-792`（`get_monthly_summary` 函数体末尾的 `return {...}`）
- Test: `tests/test_order_analytics_dashboard.py`

- [ ] **Step 1: Write the failing test**

追加到 `tests/test_order_analytics_dashboard.py`：

```python
def test_get_monthly_summary_returns_country_columns_and_media_counts(monkeypatch):
    """get_monthly_summary 应返回固定 country_columns 与 media_counts 两个新字段。"""
    # 模拟启用 en + de
    monkeypatch.setattr(oa, "_load_enabled_lang_codes", lambda: ["en", "de"])
    # 桩掉 DB 调用
    monkeypatch.setattr(
        oa,
        "query",
        lambda sql, args=None: _stub_monthly_query(sql),
    )

    result = oa.get_monthly_summary(2026, 4)

    assert "country_columns" in result
    assert result["country_columns"][0] == {"country": "US", "lang": "en"}
    assert "media_counts" in result
    # product_id=10 在 fixture 里有 en×3 + de×1
    assert result["media_counts"][10] == {"en": 3, "de": 1}


def _stub_monthly_query(sql: str):
    """根据 SQL 关键字返回不同 fixture，模拟 4 类查询。"""
    s = sql.lower()
    if "from media_items" in s:
        return [
            {"product_id": 10, "lang": "en", "n": 3},
            {"product_id": 10, "lang": "de", "n": 1},
        ]
    if "group by billing_country" in s:
        return [{"billing_country": "US", "total_qty": 5, "order_count": 4}]
    if "group by so.product_id, display_name, so.billing_country" in s:
        return [
            {"product_id": 10, "display_name": "P10", "billing_country": "US", "total_qty": 5},
        ]
    # products 汇总
    return [
        {
            "product_id": 10,
            "display_name": "P10",
            "product_code": "PCK10",
            "total_qty": 5,
            "order_count": 4,
            "total_revenue": Decimal("99.00"),
        }
    ]
```

- [ ] **Step 2: 运行测试确认失败**

```bash
python -m pytest tests/test_order_analytics_dashboard.py::test_get_monthly_summary_returns_country_columns_and_media_counts -q
```

期待：FAIL，断言错误 `KeyError: 'country_columns'` 或 `KeyError: 'media_counts'`。

- [ ] **Step 3: 修改 `get_monthly_summary` 实现**

打开 `appcore/order_analytics.py`，定位 `def get_monthly_summary(year: int, month: int, product_id: int | None = None) -> dict:` 函数。

找到函数末尾的 `return {` 块（约 786-792 行），把它替换为：

```python
    # 素材数量：按 product × lang，复用 dashboard 已有的统计逻辑
    media_counts_all = _count_media_items_by_product()
    if product_id is not None:
        media_counts = (
            {product_id: media_counts_all[product_id]}
            if product_id in media_counts_all
            else {}
        )
    else:
        # 仅保留本次查询里出现的产品，避免响应膨胀
        active_pids = {p["product_id"] for p in products if p.get("product_id") is not None}
        media_counts = {
            pid: counts for pid, counts in media_counts_all.items() if pid in active_pids
        }

    country_columns = get_enabled_country_columns()

    return {
        "products": products,
        "countries": countries,
        "country_list": country_list,
        "matrix": matrix,
        "product_order": product_order,
        "country_columns": country_columns,
        "media_counts": media_counts,
    }
```

- [ ] **Step 4: 运行整个 dashboard 测试套件确认未破坏既有测试**

```bash
python -m pytest tests/test_order_analytics_dashboard.py -q --tb=short
```

期待：全部 PASS（包含新增 + 原有）。

- [ ] **Step 5: Commit**

```bash
git add appcore/order_analytics.py tests/test_order_analytics_dashboard.py
git commit -m "$(cat <<'EOF'
feat(order-analytics): monthly summary returns country_columns+media_counts

get_monthly_summary() now appends two fields:
- country_columns: fixed [{country,lang}] list driven by enabled langs
- media_counts: {product_id: {lang: count}} filtered to products in
  the current query (avoids payload bloat when no product filter)

Frontend will use these to render the fixed country grid and per-cell
material count.
EOF
)"
```

---

## Task 3：前端 CSS — 添加 `.oam-*` 紧凑表样式

**Files:**
- Modify: `web/templates/order_analytics.html`（在 `extra_style` block 末尾、`{% endblock %}` 之前追加）

- [ ] **Step 1: 追加 CSS**

打开 `web/templates/order_analytics.html`，找到 `{% block extra_style %}` 块的末尾（紧邻 `{% endblock %}` 之前的 `.oa-ad-unmatched` 规则）。在 `.oa-ad-unmatched { ... }` 那一行之后插入：

```css
/* ── 月度视图 v2：紧凑表 + 国家×素材双行单元格 ───────────────── */
.oam-table-wrap {
  background: var(--bg-card); border: 1px solid var(--border);
  border-radius: var(--radius-lg); overflow: hidden; margin-bottom: var(--space-6);
}
.oam-table-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: var(--space-4) var(--space-5); border-bottom: 1px solid var(--border);
}
.oam-table-title { font-size: var(--text-md); font-weight: 600; color: var(--fg); }
.oam-table-scroll { overflow: auto; max-height: 70vh; }
.oam-table {
  border-collapse: separate; border-spacing: 0;
  font-size: var(--text-sm); table-layout: fixed; min-width: 100%;
}
.oam-table th, .oam-table td {
  padding: 8px var(--space-3); border-bottom: 1px solid var(--border);
  vertical-align: middle;
}
.oam-table thead th {
  background: var(--bg-subtle); font-weight: 600; color: var(--fg);
  position: sticky; top: 0; z-index: 2; line-height: 1.3;
}
.oam-table thead th .oam-th-lang {
  display: block; font-weight: 400; font-size: var(--text-xs);
  color: var(--fg-subtle); margin-top: 2px;
}
/* 第一列冻结 */
.oam-col-product {
  width: 240px; min-width: 240px; max-width: 240px;
  position: sticky; left: 0; z-index: 3; background: var(--bg-card);
  text-align: left;
}
.oam-table thead th.oam-col-product { z-index: 4; background: var(--bg-subtle); }
.oam-table tr:hover td.oam-col-product { background: oklch(97% 0.01 230); }
.oam-product-name {
  font-weight: 500; color: var(--fg);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.oam-product-code { font-size: var(--text-xs); color: var(--fg-subtle); margin-top: 2px; }

.oam-col-num     { width: 70px; text-align: right; }
.oam-col-revenue { width: 90px; text-align: right; }
.oam-col-country { width: 80px; text-align: center; padding: 6px 4px; }
.oam-col-actions { width: 100px; text-align: right; }

.oam-cell-country {
  display: flex; flex-direction: column; align-items: center;
  gap: 1px; line-height: 1.2;
}
.oam-cell-media {
  font-size: var(--text-xs); color: var(--fg-muted);
  display: inline-flex; align-items: center; gap: 2px;
}
.oam-cell-media svg { width: 11px; height: 11px; opacity: 0.7; }
.oam-cell-orders { font-weight: 600; color: var(--accent); font-size: var(--text-base); }
.oam-cell-warn   { color: var(--danger); font-weight: 600; }
.oam-cell-empty  { color: var(--fg-subtle); }
.oam-col-country.oam-cell-bg-empty { background: var(--bg-subtle); }

.oam-row-total td {
  font-weight: 700; background: var(--bg-subtle); border-top: 2px solid var(--border-strong);
}
.oam-table tr:hover td:not(.oam-col-product) { background: oklch(97% 0.01 230); }
```

- [ ] **Step 2: 视觉冒烟（启动现有 dev server 看 layout 不破坏）**

由于 CSS 类名以 `.oam-*` 为前缀，与现有 `.oa-*` / `.oad-*` 互不冲突，没有可能破坏既有页面渲染。**不需要启服务器**，留到 Task 5 一起验。

- [ ] **Step 3: Commit**

```bash
git add web/templates/order_analytics.html
git commit -m "$(cat <<'EOF'
style(order-analytics): add .oam-* compact monthly-view CSS

New class set scoped under .oam-* (no overlap with existing .oa-/.oad-).
Defines:
- 240px sticky product column with ellipsis
- 80px fixed centered country columns
- Two-row country cell: media count (top, small grey) + order count
  (bottom, accent bold)
- 0/0 empty state, ⚠ red warning for orders-without-material

JS rewrite to use these classes lands in next commit.
EOF
)"
```

---

## Task 4：重写 `renderMonthSummary()` 走新布局

**Files:**
- Modify: `web/templates/order_analytics.html:1276-1356`（`renderMonthSummary` 函数体）
- Modify: `web/templates/order_analytics.html`（`monthTableTitle`/`monthTable` 容器结构）

- [ ] **Step 1: 替换月度视图容器结构**

打开 `web/templates/order_analytics.html`，定位到 `<!-- 月度汇总表：商品 × 国家 -->` 这一行（约 686 行），把它和后面的 `<div class="oa-table-wrap">…</div>`（直到第 702 行 `</div>`）整段替换为：

```html
      <!-- 月度汇总表 v2：商品 × 国家（紧凑双行单元格） -->
      <div class="oam-table-wrap">
        <div class="oam-table-header">
          <div class="oam-table-title" id="monthTableTitle">商品 × 国家订单分布</div>
          <button class="btn btn-ghost btn-sm" onclick="exportMonthCSV()">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
            导出 CSV
          </button>
        </div>
        <div class="oam-table-scroll">
          <table class="oam-table" id="monthTable">
            <thead><tr id="monthTableHead"></tr></thead>
            <tbody id="monthTableBody"></tbody>
          </table>
        </div>
      </div>
```

- [ ] **Step 2: 替换 `renderMonthSummary` 函数**

定位到 `function renderMonthSummary(data, year, month) {`（约第 1276 行），把整个函数体（直到对应的 `}` ，约 1356 行）替换为：

```javascript
  function renderMonthSummary(data, year, month) {
    // 顶部 5 张统计卡（保留原 oa-stats 容器，不动）
    var totalQty = 0, totalOrders = 0, totalRevenue = 0;
    data.products.forEach(function(p) {
      totalQty += (parseInt(p.total_qty) || 0);
      totalOrders += (parseInt(p.order_count) || 0);
      totalRevenue += (parseFloat(p.total_revenue) || 0);
    });
    document.getElementById('monthStatsCards').innerHTML =
      statCard('活跃商品', data.products.length) +
      statCard('总单量', totalQty.toLocaleString()) +
      statCard('总订单数', totalOrders.toLocaleString()) +
      statCard('总收入', '$' + totalRevenue.toFixed(2)) +
      statCard('国家/地区', (data.country_columns || []).length);

    var title = year + '年' + month + '月 商品 × 国家订单分布';
    if (currentProductName) title += ' — ' + currentProductName;
    document.getElementById('monthTableTitle').textContent = title;

    var countryCols = Array.isArray(data.country_columns) ? data.country_columns : [];
    var mediaCounts = data.media_counts || {};

    // 表头：商品 / 总单量 / 订单数 / 收入(可选) / 国家列 / 操作
    var headRow = document.getElementById('monthTableHead');
    headRow.innerHTML = '';
    addThText(headRow, 'oam-col-product', '商品');
    addThText(headRow, 'oam-col-num', '总单量');
    addThText(headRow, 'oam-col-num', '订单数');
    if (!currentProductId) addThText(headRow, 'oam-col-revenue', '收入');
    countryCols.forEach(function(col) {
      var th = document.createElement('th');
      th.className = 'oam-col-country';
      th.innerHTML = (col.country || '') +
        '<span class="oam-th-lang">' + (col.lang || '') + '</span>';
      headRow.appendChild(th);
    });
    addThText(headRow, 'oam-col-actions', '操作');

    // 表体
    var tbody = document.getElementById('monthTableBody');
    tbody.innerHTML = '';

    var totalsByCountry = {};
    countryCols.forEach(function(col) { totalsByCountry[col.country] = 0; });
    var sumQty = 0, sumOrders = 0;

    data.product_order.forEach(function(dn) {
      var p = data.products.find(function(x) { return (x.display_name || '未知') === dn; }) || {};
      var pid = p.product_id;
      var langCounts = (pid != null && mediaCounts[pid]) ? mediaCounts[pid] : {};
      var rowOrdersByCountry = data.matrix[dn] || {};

      var tr = document.createElement('tr');
      tr.style.cursor = 'pointer';
      tr.title = '点击查看该产品详情';
      tr.onclick = function() { if (pid) selectProduct(pid, dn); };

      // 商品列：名 + product_code
      var tdProd = document.createElement('td');
      tdProd.className = 'oam-col-product';
      tdProd.innerHTML =
        '<div class="oam-product-name" title="' + escAttr(dn) + '">' + escHtml(dn) + '</div>' +
        (p.product_code ? '<div class="oam-product-code">' + escHtml(p.product_code) + '</div>' : '');
      tr.appendChild(tdProd);

      // 汇总列
      var qty = parseInt(p.total_qty) || 0;
      var orderCnt = parseInt(p.order_count) || 0;
      sumQty += qty; sumOrders += orderCnt;
      addTdText(tr, 'oam-col-num', qty.toLocaleString());
      addTdText(tr, 'oam-col-num', orderCnt.toLocaleString());
      if (!currentProductId) {
        addTdText(tr, 'oam-col-revenue', '$' + (parseFloat(p.total_revenue) || 0).toFixed(2));
      }

      // 国家列：双行 [素材数 / 订单数]
      countryCols.forEach(function(col) {
        var orders = parseInt(rowOrdersByCountry[col.country]) || 0;
        var media = parseInt(langCounts[col.lang]) || 0;
        totalsByCountry[col.country] = (totalsByCountry[col.country] || 0) + orders;

        var td = document.createElement('td');
        td.className = 'oam-col-country';
        if (orders === 0 && media === 0) {
          td.classList.add('oam-cell-bg-empty');
          td.innerHTML = '<div class="oam-cell-country">' +
            '<span class="oam-cell-empty">—</span>' +
            '<span class="oam-cell-empty">—</span>' +
            '</div>';
        } else {
          var mediaHtml;
          if (media === 0 && orders > 0) {
            mediaHtml = '<span class="oam-cell-warn" title="该国语种 ' + col.lang +
              ' 暂无素材，但有订单——请检查">⚠ 0</span>';
          } else {
            mediaHtml = '<span class="oam-cell-media">' +
              '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="6" width="20" height="14" rx="2" ry="2"/><polyline points="2 10 22 10"/></svg>' +
              media + '</span>';
          }
          var ordersHtml = orders > 0
            ? '<span class="oam-cell-orders">' + orders + '</span>'
            : '<span class="oam-cell-empty">0</span>';
          td.innerHTML = '<div class="oam-cell-country">' + mediaHtml + ordersHtml + '</div>';
        }
        tr.appendChild(td);
      });

      // 操作列
      var tdAct = document.createElement('td');
      tdAct.className = 'oam-col-actions';
      tdAct.innerHTML = pid
        ? '<a href="/medias?product_id=' + pid + '" target="_blank" rel="noopener" ' +
          'onclick="event.stopPropagation()" style="color:var(--accent);text-decoration:none;font-size:var(--text-xs)">素材↗</a>'
        : '';
      tr.appendChild(tdAct);

      tbody.appendChild(tr);
    });

    // 合计行
    var totalRow = document.createElement('tr');
    totalRow.className = 'oam-row-total';
    addTdText(totalRow, 'oam-col-product', '合计');
    addTdText(totalRow, 'oam-col-num', sumQty.toLocaleString());
    addTdText(totalRow, 'oam-col-num', sumOrders.toLocaleString());
    if (!currentProductId) addTdText(totalRow, 'oam-col-revenue', '');
    countryCols.forEach(function(col) {
      var v = totalsByCountry[col.country] || 0;
      var td = document.createElement('td');
      td.className = 'oam-col-country';
      td.innerHTML = v > 0
        ? '<span class="oam-cell-orders">' + v.toLocaleString() + '</span>'
        : '<span class="oam-cell-empty">—</span>';
      totalRow.appendChild(td);
    });
    addTdText(totalRow, 'oam-col-actions', '');
    tbody.appendChild(totalRow);
  }

  // 小工具：纯文本表头/单元格（避免误把字符串当 HTML）
  function addThText(tr, cls, text) {
    var th = document.createElement('th');
    if (cls) th.className = cls;
    th.textContent = text;
    tr.appendChild(th);
  }
  function addTdText(tr, cls, text) {
    var td = document.createElement('td');
    if (cls) td.className = cls;
    td.textContent = text;
    tr.appendChild(td);
  }
  function escAttr(s) {
    return String(s == null ? '' : s).replace(/"/g, '&quot;').replace(/</g, '&lt;');
  }
```

注：`escHtml` 在原文件 `addTd` 等函数附近已定义（约 1450 行），可直接复用。如果运行时报 `escHtml is not defined`，回去检查是否在 `(function(){})()` 闭包外、需要把它从外层 IIFE 里挪到本闭包内或顶部声明。

- [ ] **Step 3: 启动开发服务器**

```bash
python -m flask --app web.app:app run --host 127.0.0.1 --port 5099
```

后台运行，开新终端继续。

- [ ] **Step 4: 浏览器手工验收**

访问 `http://127.0.0.1:5099/order-analytics`，登录后切到 `订单分析` Tab → 月度视图。

逐项验证：

- 商品名列固定 240px，单行省略号；下方有 product_code
- 国家列每列等宽（80px 居中）；表头显示国家代码 + 下方小灰字语种
- 单元格上下两行：上 = 素材数（带书本/盒子图标），下 = 订单数
- 0/0 单元格背景浅灰、`— / —`
- 有素材但 0 单：素材正常，订单显示灰色 `0`
- 有单 0 素材：素材位 **红色 ⚠ 0**
- hover 商品名出现 tooltip
- 第一列与表头都是 sticky
- 合计行底部显示，国家列汇总按订单数加总

如果某项不符，回到 Step 2 修。

- [ ] **Step 5: 终止 dev server**

```bash
pkill -f "flask --app web.app:app run --port 5099" || true
```

- [ ] **Step 6: Commit**

```bash
git add web/templates/order_analytics.html
git commit -m "$(cat <<'EOF'
feat(order-analytics): redesign monthly view with country×material cells

Rewrites renderMonthSummary() to use the new .oam-* compact layout:
- Fixed 240px sticky product column with single-line ellipsis + code
- 80px country columns with header showing code + lang label
- Two-row cells: media count icon (top) + order count (bottom)
- States:
  - 0/0 → grey ─/─ on subtle bg
  - has media + 0 orders → grey 0
  - 0 media + has orders → ⚠ 0 in red (warning)
  - both > 0 → grey count + accent-bold orders
- Actions column links to /medias?product_id=...

Container migrated from .oa-table-wrap to .oam-table-wrap (no
shared selectors with weekly view or other tabs).
EOF
)"
```

---

## Task 5：CSV 导出补素材数（每国一列）

**Files:**
- Modify: `web/templates/order_analytics.html` 的 `exportMonthCSV` 函数（grep 文件内 `function exportMonthCSV` 找到）

- [ ] **Step 1: 阅读现状**

```bash
grep -n "exportMonthCSV" web/templates/order_analytics.html
```

记下函数起止行号。

- [ ] **Step 2: 替换实现**

把 `exportMonthCSV` 整个函数体替换为：

```javascript
  window.exportMonthCSV = function() {
    if (!lastMonthData) { alert('暂无数据可导出'); return; }
    var data = lastMonthData;
    var countryCols = data.country_columns || [];
    var mediaCounts = data.media_counts || {};

    var rows = [];
    var header = ['商品名', 'product_code', '总单量', '订单数', '收入'];
    countryCols.forEach(function(col) {
      header.push(col.country + ' 订单');
      header.push(col.country + ' ' + col.lang + ' 素材数');
    });
    rows.push(header);

    data.product_order.forEach(function(dn) {
      var p = data.products.find(function(x) { return (x.display_name || '未知') === dn; }) || {};
      var pid = p.product_id;
      var langCounts = (pid != null && mediaCounts[pid]) ? mediaCounts[pid] : {};
      var rowOrders = data.matrix[dn] || {};

      var line = [
        dn,
        p.product_code || '',
        parseInt(p.total_qty) || 0,
        parseInt(p.order_count) || 0,
        parseFloat(p.total_revenue) || 0,
      ];
      countryCols.forEach(function(col) {
        line.push(parseInt(rowOrders[col.country]) || 0);
        line.push(parseInt(langCounts[col.lang]) || 0);
      });
      rows.push(line);
    });

    var csv = rows.map(function(r) {
      return r.map(function(cell) {
        var s = String(cell == null ? '' : cell);
        return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
      }).join(',');
    }).join('\n');

    var bom = '﻿';  // Excel 识别 UTF-8
    var blob = new Blob([bom + csv], { type: 'text/csv;charset=utf-8;' });
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = '订单分析-' + (data.product_order && data.product_order.length ? '商品' : '空') + '.csv';
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };
```

- [ ] **Step 3: 浏览器手工验收**

启动 dev server 后访问 `订单分析` → 月度视图 → 点 `导出 CSV`，用 Excel/Numbers 打开：
- 标题行包含每个国家两列：`US 订单` + `US en 素材数`
- 数字列正确（与页面表对齐）
- 无乱码

- [ ] **Step 4: Commit**

```bash
git add web/templates/order_analytics.html
git commit -m "$(cat <<'EOF'
feat(order-analytics): CSV export adds per-country material count

Each country now contributes two CSV columns: '<C> 订单' and
'<C> <lang> 素材数'. Driven by data.country_columns / media_counts
returned by /order-analytics/monthly.

Adds UTF-8 BOM so Excel opens it without garbled Chinese.
EOF
)"
```

---

## Task 6：每日明细表跟随收紧字号（单纯样式套用）

**Files:**
- Modify: `web/templates/order_analytics.html` 的每日明细容器（约 703-714 行）

每日明细表保留结构不动，只替换 CSS 类前缀让它享受紧凑布局。

- [ ] **Step 1: 在每日明细表上套 oam 类**

找到约 703-714 行的：

```html
      <!-- 每日明细表 -->
      <div class="oa-table-wrap" id="dailyWrap" style="display:none;">
        <div class="oa-table-header">
          <div class="oa-table-title" id="dailyTableTitle">每日销量明细</div>
        </div>
        <div class="oa-table-scroll">
          <table class="oa-table" id="dailyTable">
            <thead><tr id="dailyTableHead"></tr></thead>
            <tbody id="dailyTableBody"></tbody>
          </table>
        </div>
      </div>
```

把 `oa-table-wrap` → `oam-table-wrap`，`oa-table-header` → `oam-table-header`，`oa-table-title` → `oam-table-title`，`oa-table-scroll` → `oam-table-scroll`，`oa-table` → `oam-table`。

只动这 6 个类名，不动 id、不动 JS。

- [ ] **Step 2: 浏览器验收**

dev server 起着的话直接刷新 `订单分析` → 月度视图：
- 每日明细头部样式与上方主表一致
- 行高、字号收紧
- 内嵌的"商品 × 国家"小表保持原样（其样式由 `.oam-daily-detail-inner` 已定义？不，原来是 `.oa-daily-detail-inner`，不动）

- [ ] **Step 3: Commit**

```bash
git add web/templates/order_analytics.html
git commit -m "$(cat <<'EOF'
style(order-analytics): daily detail wrapper uses .oam-* compact style

Just swaps 6 class names (oa-table* → oam-table*) so the daily
detail card matches the redesigned monthly view's typography. No
JS changes; inner expanded table keeps its original .oa-daily-* CSS.
EOF
)"
```

---

## Task 7：测试套件全量回归 + 用户验收

- [ ] **Step 1: 跑订单分析相关测试**

```bash
python -m pytest tests/test_order_analytics_dashboard.py tests/test_order_analytics_ads.py -q --tb=short
```

期待：全部 PASS。

- [ ] **Step 2: 跑全量测试套件（防回归）**

```bash
python -m pytest -q --tb=short 2>&1 | tail -40
```

期待：全部 PASS。如果有不相关失败，记录但不在本 PR 修。

- [ ] **Step 3: 在浏览器里再过一遍验收清单**

启动 server：
```bash
python -m flask --app web.app:app run --host 127.0.0.1 --port 5099
```

访问 `订单分析` → 月度视图，对照 spec 的"目标"逐条核对：
- 一屏稳定看到 ≥ 10 个国家：✓ / ✗
- 每个产品 × 每个国家显示素材数 + 订单数：✓ / ✗
- 视觉与产品看板风格一致：✓ / ✗

不通过则回到对应 Task 修，通过则进 Step 4。

```bash
pkill -f "flask --app web.app:app run --port 5099" || true
```

- [ ] **Step 4: 在 Telegram / 给用户截图**

把改造前后两张图给用户看（可手动截图也可 console），确认接受。

- [ ] **Step 5: 合并到 master + 部署**

按 CLAUDE.md 的工作流：
1. 主 worktree 上 push
2. SSH 到服务器（172.30.254.14）`git pull && systemctl restart`
3. 健康检查（curl /order-analytics 返回 200，或浏览器肉眼确认）
4. 这个改动属于 hotfix（≤ 50 行业务代码？看实际增删行）还是 worktree？— 后端 ~50 行 + 前端 ~150 行，**超过 hotfix 阈值，应在新 worktree 完成后再合并**。

> 如果本计划是在主 worktree 直接执行的，先停下，按 CLAUDE.md 把改动迁到新 worktree（feature/order-analytics-monthly-redesign），合并完再走部署。

---

## Self-Review

1. **Spec 覆盖检查**：
   - 国家映射常量 → Task 1 ✓
   - 启用国家列推导 → Task 1 ✓
   - get_monthly_summary 返回扩展 → Task 2 ✓
   - .oam-* CSS → Task 3 ✓
   - renderMonthSummary 重写 → Task 4 ✓
   - CSV 导出 → Task 5 ✓
   - 每日明细字号收紧 → Task 6 ✓
   - 状态机（0/0、≥1/0、0/≥1、≥1/≥1）→ Task 4 Step 2 完整覆盖 ✓
2. **占位符扫描**：无 TBD/TODO；每个步骤有完整代码或确切命令 ✓
3. **类型一致性**：`country_columns` / `media_counts` / `_load_enabled_lang_codes` / `get_enabled_country_columns` / `COUNTRY_TO_LANG` / `LANG_PRIORITY_COUNTRIES` 在 Task 1、2、4、5 全程同名 ✓
4. **新 escAttr / addThText / addTdText 函数**：Task 4 Step 2 内联定义，未在前面任务引用 ✓
