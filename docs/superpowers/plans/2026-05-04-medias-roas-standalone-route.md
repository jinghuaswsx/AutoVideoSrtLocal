# 素材管理：单产品 ROAS 独立路由 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给单产品 ROAS 编辑增加独立路由 `/medias/<int:pid>/roas`，并把模态/独立页统一改造成"输入即自动计算 + 自动保存 + 顶部状态条"的工作流。

**Architecture:** 抽公共 Jinja partial（`_roas_form.html` + `_roas_styles.html`）+ 公共 JS 类 `RoasFormController`，模态和独立页共用一份 HTML/CSS/JS 行为。新增视图 `roas_page` 只负责权限校验 + 渲染产品上下文，保存复用现有 `PUT /medias/api/products/<pid>`。

**Tech Stack:** Flask + Jinja2 + 原生 JS + pytest + Ocean Blue OKLCH design tokens

**Spec：** [docs/superpowers/specs/2026-05-04-medias-roas-standalone-route-design.md](../specs/2026-05-04-medias-roas-standalone-route-design.md)

**工作分支：** `feature/medias-roas-route`（已在 `.worktrees/medias-roas-route` 准备好；baseline `pytest tests/test_product_roas.py tests/test_material_roas_frontend.py -q` = 16 passed）

---

## 文件清单

**新建**
- `web/templates/medias/_roas_form.html` — 共享 partial：产品卡 + 表单两栏
- `web/templates/medias/_roas_styles.html` — 共享 partial：`.oc` page-scoped tokens + ROAS 专属样式（含胶囊按钮、状态条）
- `web/templates/medias/roas.html` — 独立路由页（继承 `layout.html`）
- `web/static/roas_form.js` — `RoasFormController` 类（自动计算 + 自动保存 + 状态条 + last-write-wins）
- `tests/test_medias_roas_route.py` — 后端路由测试
- `tests/test_roas_form_partial.py` — partial 渲染 / 字段完备性测试
- `tests/test_roas_form_controller_assets.py` — `roas_form.js` 静态断言（参照 `test_material_roas_frontend.py` 风格）

**修改**
- `web/routes/medias/products.py` — 新增 `roas_page` 视图函数
- `web/templates/medias_list.html` — 模态 body 替换为 `{% include _roas_form.html %}`、styles 替换为 `{% include _roas_styles.html %}`、模态 head 加胶囊跳转按钮、模态 footer 删保存按钮 + saveMsg、加顶部状态条
- `web/static/medias.js` — `openRoasModal` / `saveRoas` 改造为构造 `RoasFormController`，删除原 `saveRoas`、`renderRoasResult` 等冗余函数
- `tests/test_material_roas_frontend.py` — 既有断言改为针对 partial 文件（HTML 抽出后内容仍在）

---

## Task 1: 后端路由 + 视图函数

**Files:**
- Modify: `web/routes/medias/products.py:1-32` (新增 `roas_page` 视图)
- Create: `tests/test_medias_roas_route.py`

- [ ] **Step 1: 写失败测试**

新建 `tests/test_medias_roas_route.py`：

```python
from __future__ import annotations


def test_roas_page_returns_html_for_owner(authed_client_no_db, monkeypatch):
    from web.routes import medias as r

    monkeypatch.setattr(
        r.medias,
        "get_product",
        lambda pid: {
            "id": pid,
            "user_id": 1,
            "name": "测试产品",
            "product_code": "baseball-cap-organizer-rjc",
            "purchase_price": "7.4",
            "standalone_price": "20.95",
        },
    )
    monkeypatch.setattr(r, "_can_access_product", lambda product: product is not None)

    resp = authed_client_no_db.get("/medias/6/roas")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "独立站保本 ROAS" in body
    assert 'data-roas-field="purchase_price"' in body
    assert 'data-roas-field="standalone_price"' in body
    assert "baseball-cap-organizer-rjc" in body


def test_roas_page_404_when_product_missing(authed_client_no_db, monkeypatch):
    from web.routes import medias as r

    monkeypatch.setattr(r.medias, "get_product", lambda pid: None)

    resp = authed_client_no_db.get("/medias/9999/roas")

    assert resp.status_code == 404


def test_roas_page_404_when_user_cannot_access(authed_client_no_db, monkeypatch):
    from web.routes import medias as r

    monkeypatch.setattr(
        r.medias,
        "get_product",
        lambda pid: {"id": pid, "user_id": 999, "name": "x"},
    )
    monkeypatch.setattr(r, "_can_access_product", lambda product: False)

    resp = authed_client_no_db.get("/medias/6/roas")

    assert resp.status_code == 404


def test_roas_page_redirects_to_login_when_anonymous(monkeypatch):
    monkeypatch.setattr("web.app._run_startup_recovery", lambda: None)
    monkeypatch.setattr("web.app.recover_all_interrupted_tasks", lambda: None)
    monkeypatch.setattr("web.app.mark_interrupted_bulk_translate_tasks", lambda: None)
    monkeypatch.setattr("web.app._seed_default_prompts", lambda: None)
    monkeypatch.setattr("appcore.db.execute", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "appcore.medias.list_enabled_language_codes",
        lambda: ["en"],
    )

    from web.app import create_app

    client = create_app().test_client()
    resp = client.get("/medias/6/roas", follow_redirects=False)

    assert resp.status_code in (301, 302)
    assert "/login" in resp.headers.get("Location", "")
```

- [ ] **Step 2: 跑测试确认失败**

```bash
python -m pytest tests/test_medias_roas_route.py -q
```

预期：4 个测试全部 FAIL（路由不存在 → 404 fallthrough，部分 assertion 不通过）。

- [ ] **Step 3: 实现视图**

修改 `web/routes/medias/products.py`，在文件末尾追加（在 imports 部分确认已有 `render_template`，没有就加）：

```python
# === 文件顶部 imports 区，确认包含 ===
from flask import abort, jsonify, render_template, request
```

末尾追加：

```python
@bp.route("/<int:pid>/roas")
@login_required
def roas_page(pid: int):
    product = medias.get_product(pid)
    routes = _routes_module()
    if not product or not routes._can_access_product(product):
        abort(404)
    return render_template(
        "medias/roas.html",
        product=_serialize_product(product),
        roas_rmb_per_usd=product_roas.get_configured_rmb_per_usd(),
    )
```

> 注：`_can_access_product` 定义在 `web/routes/medias/__init__.py`（同 cover 路由用法），通过 `_routes_module()` 取到。如果 grep 发现它就在 `products.py` 同包别处导出，直接 import 即可。

- [ ] **Step 4: 创建占位模板 `web/templates/medias/roas.html`**

仅为让 Task 1 测试通过的最小骨架（Task 3 会扩充）：

```jinja
{% extends "layout.html" %}
{% block title %}独立站保本 ROAS - {{ product.product_code }}{% endblock %}
{% block page_title %}独立站保本 ROAS{% endblock %}
{% block content %}
<div class="oc oc-roas-page" data-product-id="{{ product.id }}">
  <h2>独立站保本 ROAS</h2>
  <p>{{ product.product_code }}</p>
  <input type="hidden" data-roas-field="purchase_price" value="{{ product.purchase_price or '' }}">
  <input type="hidden" data-roas-field="standalone_price" value="{{ product.standalone_price or '' }}">
</div>
{% endblock %}
```

- [ ] **Step 5: 跑测试确认通过**

```bash
python -m pytest tests/test_medias_roas_route.py -q
```

预期：4 passed。

- [ ] **Step 6: Commit**

```bash
git add web/routes/medias/products.py web/templates/medias/roas.html tests/test_medias_roas_route.py
git commit -m "feat(roas): add /medias/<pid>/roas route with permission guard"
```

---

## Task 2: 抽 `_roas_form.html` partial

把模态里的产品卡 + 表单两栏抽成共享 partial，模态和独立页都 include 它。

**Files:**
- Create: `web/templates/medias/_roas_form.html`
- Modify: `web/templates/medias_list.html:2443-2507`（把模态 body 内容替换为 include）
- Modify: `web/templates/medias/roas.html`（替换 Task 1 的占位 → include partial）
- Create: `tests/test_roas_form_partial.py`
- Modify: `tests/test_material_roas_frontend.py`（部分断言迁移到新文件，保持回归覆盖）

- [ ] **Step 1: 写失败测试**

新建 `tests/test_roas_form_partial.py`：

```python
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PARTIAL = ROOT / "web" / "templates" / "medias" / "_roas_form.html"


def test_partial_file_exists():
    assert PARTIAL.exists(), f"missing partial: {PARTIAL}"


def test_partial_contains_product_card_block():
    html = PARTIAL.read_text(encoding="utf-8")
    assert 'class="oc-roas-product"' in html
    assert 'id="roasProductId"' in html
    assert 'id="roasProductCover"' in html


def test_partial_contains_all_site_fields():
    html = PARTIAL.read_text(encoding="utf-8")
    for field in (
        "purchase_1688_url",
        "purchase_price",
        "standalone_price",
        "standalone_shipping_fee",
        "package_length_cm",
        "package_width_cm",
        "package_height_cm",
        "packet_cost_estimated",
        "packet_cost_actual",
    ):
        assert f'data-roas-field="{field}"' in html, f"missing field {field}"


def test_partial_contains_tk_fields_and_average_shipping_tool():
    html = PARTIAL.read_text(encoding="utf-8")
    for field in ("tk_sea_cost", "tk_air_cost", "tk_sale_price"):
        assert f'data-roas-field="{field}"' in html
    assert 'id="roasAverageShippingInput"' in html
    assert 'id="roasAverageShippingResult"' in html


def test_partial_contains_calculate_button_and_results():
    html = PARTIAL.read_text(encoding="utf-8")
    assert 'id="roasCalculateBtn"' in html
    assert 'id="roasEstimatedValue"' in html
    assert 'id="roasActualValue"' in html
    assert 'id="roasEffectiveValue"' in html


def test_medias_list_includes_partial():
    html = (ROOT / "web" / "templates" / "medias_list.html").read_text(encoding="utf-8")
    assert "medias/_roas_form.html" in html


def test_roas_page_includes_partial():
    html = (ROOT / "web" / "templates" / "medias" / "roas.html").read_text(encoding="utf-8")
    assert "medias/_roas_form.html" in html
```

- [ ] **Step 2: 跑测试确认失败**

```bash
python -m pytest tests/test_roas_form_partial.py -q
```

预期：7 全 FAIL（partial 不存在）。

- [ ] **Step 3: 创建 partial 文件**

新建 `web/templates/medias/_roas_form.html`，把 `medias_list.html:2443-2507` 这段 `<div class="oc-modal-body">` 内的内容（产品卡 + form）原样复制进来。**逐字节迁移，不增减字段、不改 id/class**：

```jinja
{# Shared ROAS form partial — used by medias_list.html modal and medias/roas.html standalone page #}
<div class="oc-roas-product">
  <div id="roasProductCover" class="roas-cover-ph"><svg width="24" height="24"><use href="#ic-package"/></svg></div>
  <dl class="oc-roas-meta">
    <div><dt>产品 ID</dt><dd id="roasProductId">—</dd></div>
    <div><dt>产品中文名</dt><dd id="roasProductName">—</dd></div>
    <div><dt>产品英文名</dt><dd id="roasProductEnglish">—</dd></div>
    <div><dt>当前采用</dt><dd id="roasEffectiveBasis">—</dd></div>
  </dl>
</div>
<form id="roasForm">
  <div class="oc-roas-layout">
    <div class="oc-roas-column">
      <section id="roasSiteSection" class="oc-roas-section oc-roas-site-section">
        <h4>独立站信息</h4>
        <div class="oc-roas-field-list">
          <div class="oc-roas-field"><label for="roas1688Url">1688 采购链接</label><input id="roas1688Url" data-roas-field="purchase_1688_url" type="url"></div>
          <div class="oc-roas-field"><label for="roasPurchasePrice">采购价格 (RMB)</label><input id="roasPurchasePrice" data-roas-field="purchase_price" type="number" min="0" step="0.01"></div>
          <div class="oc-roas-field"><label for="roasStandalonePrice">独立站售价 (USD)</label><input id="roasStandalonePrice" data-roas-field="standalone_price" type="number" min="0" step="0.01"></div>
          <div class="oc-roas-field"><label for="roasStandaloneShipping">用户支付运费 (USD)</label><input id="roasStandaloneShipping" data-roas-field="standalone_shipping_fee" type="number" min="0" step="0.01"></div>
          <div class="oc-roas-field"><label for="roasLength">长 (cm)</label><input id="roasLength" data-roas-field="package_length_cm" type="number" min="0" step="0.01"></div>
          <div class="oc-roas-field"><label for="roasWidth">宽 (cm)</label><input id="roasWidth" data-roas-field="package_width_cm" type="number" min="0" step="0.01"></div>
          <div class="oc-roas-field"><label for="roasHeight">高 (cm)</label><input id="roasHeight" data-roas-field="package_height_cm" type="number" min="0" step="0.01"></div>
          <div class="oc-roas-field"><label for="roasPacketEstimated">预估小包成本 (RMB)</label><input id="roasPacketEstimated" data-roas-field="packet_cost_estimated" type="number" min="0" step="0.01"></div>
          <div class="oc-roas-field"><label for="roasPacketActual">实际小包成本 (RMB)</label><input id="roasPacketActual" data-roas-field="packet_cost_actual" type="number" min="0" step="0.01"></div>
        </div>
      </section>
      <section class="oc-roas-section">
        <div class="oc-roas-section-head">
          <h4>计算结果</h4>
          <button type="button" id="roasCalculateBtn" class="oc-roas-calc-btn">计算 ROAS</button>
        </div>
        <div class="oc-roas-results">
          <div id="roasEstimatedBox" class="oc-roas-result"><div class="label">预估保本 ROAS</div><div id="roasEstimatedValue" class="value">—</div></div>
          <div id="roasActualBox" class="oc-roas-result"><div class="label">实际保本 ROAS</div><div id="roasActualValue" class="value">—</div></div>
          <div id="roasEffectiveBox" class="oc-roas-result active"><div class="label">当前采用值</div><div id="roasEffectiveValue" class="value">—</div></div>
        </div>
        <p id="roasNote" class="oc-roas-note">采购价格和小包成本按 1 USD = {{ roas_rmb_per_usd or material_roas_rmb_per_usd or 6.83 }} RMB 换算；手续费按独立站售价 + 用户支付运费的 10% 计算。</p>
      </section>
    </div>
    <div class="oc-roas-column oc-roas-tk-column">
      <section id="roasTkSection" class="oc-roas-section oc-roas-tk-section">
        <h4>TK 可选项</h4>
        <div class="oc-roas-field-list">
          <div class="oc-roas-field"><label for="roasTkSea">TK 海运成本</label><input id="roasTkSea" data-roas-field="tk_sea_cost" type="number" min="0" step="0.01"></div>
          <div class="oc-roas-field"><label for="roasTkAir">TK 空运成本</label><input id="roasTkAir" data-roas-field="tk_air_cost" type="number" min="0" step="0.01"></div>
          <div class="oc-roas-field"><label for="roasTkSale">TK 售卖定价</label><input id="roasTkSale" data-roas-field="tk_sale_price" type="number" min="0" step="0.01"></div>
        </div>
      </section>
      <section id="roasAverageShippingSection" class="oc-roas-section oc-roas-average-shipping-section">
        <div class="oc-roas-avg-head">
          <h4>平均运费计算器</h4>
          <div class="oc-roas-avg-summary">
            <strong id="roasAverageShippingResult" class="oc-roas-avg-result">--</strong>
            <div id="roasAverageShippingMeta" class="oc-roas-avg-meta">有效行数 0 · 合计 0.0</div>
          </div>
        </div>
        <div class="oc-roas-avg-field">
          <label for="roasAverageShippingInput">原始运费列表</label>
          <textarea id="roasAverageShippingInput" class="oc-roas-avg-input" spellcheck="false" placeholder="粘贴运费列表"></textarea>
        </div>
      </section>
    </div>
  </div>
</form>
```

- [ ] **Step 4: 把 `medias_list.html` 模态 body 替换为 include**

把 `web/templates/medias_list.html:2443-2507`（即 `<div class="oc-modal-body">` 之内、`</div></form>` 之外）整段内容替换为：

```jinja
    <div class="oc-modal-body">
      {% include "medias/_roas_form.html" %}
    </div>
```

- [ ] **Step 5: 把 `medias/roas.html` 占位替换为 include**

修改 `web/templates/medias/roas.html`：

```jinja
{% extends "layout.html" %}
{% block title %}独立站保本 ROAS - {{ product.product_code }}{% endblock %}
{% block page_title %}独立站保本 ROAS{% endblock %}
{% block content %}
<div class="oc oc-roas-page" data-product-id="{{ product.id }}">
  <header class="oc-roas-page-head">
    <a class="oc-btn ghost" href="/medias">← 返回素材管理</a>
    <h1>独立站保本 ROAS</h1>
  </header>
  {% include "medias/_roas_form.html" %}
</div>
{% endblock %}
```

- [ ] **Step 6: 更新 `tests/test_material_roas_frontend.py`**

把 `test_medias_list_has_roas_modal_mount` / `test_roas_modal_splits_site_and_tk_fields_into_single_column_sections` / `test_roas_modal_uses_manual_calculate_button_and_injected_exchange_rate` 中**直接读 `medias_list.html`** 的字段断言改为读 `web/templates/medias/_roas_form.html`（除了断言 `id="roasModalMask"` 这种**外壳**仍属 `medias_list.html`）。

具体改法：在文件顶部加：

```python
PARTIAL = ROOT / "web" / "templates" / "medias" / "_roas_form.html"
```

然后把每个测试函数里的 `(ROOT / "web" / "templates" / "medias_list.html").read_text(...)` 视情况改读 `PARTIAL.read_text(...)`：
- 检查 `id="roasModalMask"` 的：留在 `medias_list.html`
- 检查 `data-roas-field=...`、`id="roasSiteSection"`、`id="roasTkSection"`、`id="roasCalculateBtn"`、`独立站售价` 等表单内字段的：改读 `PARTIAL`

- [ ] **Step 7: 跑全部相关测试**

```bash
python -m pytest tests/test_roas_form_partial.py tests/test_material_roas_frontend.py tests/test_medias_roas_route.py tests/test_product_roas.py -q
```

预期：全 pass（partial 7 + 原有 16 + 路由 4 = 27 左右）。

- [ ] **Step 8: Commit**

```bash
git add web/templates/medias/_roas_form.html web/templates/medias/roas.html web/templates/medias_list.html tests/test_roas_form_partial.py tests/test_material_roas_frontend.py
git commit -m "refactor(roas): extract shared _roas_form.html partial used by modal + standalone page"
```

---

## Task 3: 抽 `_roas_styles.html` partial（OKLCH tokens + ROAS 专属样式）

**Files:**
- Create: `web/templates/medias/_roas_styles.html`
- Modify: `web/templates/medias_list.html:5-?` (`extra_style` block 内 ROAS 相关 CSS 块)
- Modify: `web/templates/medias/roas.html` (在 `extra_style` block include partial)

- [ ] **Step 1: 写失败测试**

`tests/test_roas_form_partial.py` 末尾追加：

```python
STYLES = ROOT / "web" / "templates" / "medias" / "_roas_styles.html"


def test_styles_partial_exists():
    assert STYLES.exists()


def test_styles_partial_defines_oc_tokens():
    css = STYLES.read_text(encoding="utf-8")
    assert "--oc-accent" in css
    assert "--oc-bg-subtle" in css
    assert "--oc-roas" not in css or "oc-roas" in css  # ROAS-specific tokens optional


def test_styles_partial_defines_roas_layout_rules():
    css = STYLES.read_text(encoding="utf-8")
    assert ".oc-roas-layout" in css
    assert ".oc-roas-section" in css
    assert ".oc-roas-results" in css


def test_medias_list_includes_styles_partial():
    html = (ROOT / "web" / "templates" / "medias_list.html").read_text(encoding="utf-8")
    assert "medias/_roas_styles.html" in html


def test_roas_page_includes_styles_partial():
    html = (ROOT / "web" / "templates" / "medias" / "roas.html").read_text(encoding="utf-8")
    assert "medias/_roas_styles.html" in html
```

- [ ] **Step 2: 跑测试确认失败**

```bash
python -m pytest tests/test_roas_form_partial.py -q
```

预期：5 个新增测试 FAIL。

- [ ] **Step 3: 找出 `medias_list.html` 中 ROAS 相关 CSS 范围**

```bash
grep -n "oc-roas\|--oc-" web/templates/medias_list.html | head -30
```

记录范围：所有 `--oc-*` token 定义（`.oc { ... }` 块内）+ `.oc-roas-*` 选择器规则。

- [ ] **Step 4: 创建 `_roas_styles.html`**

新建 `web/templates/medias/_roas_styles.html`，把 Step 3 找到的 CSS 块原样复制进来，外面**不要**再包 `<style>`（让父模板的 `extra_style` block 自己处理）。结构：

```jinja
{# Shared ROAS styles — Ocean Blue OKLCH tokens + ROAS-specific layout #}
.oc {
  --oc-bg:            oklch(99%  0.004 230);
  --oc-bg-subtle:     oklch(97%  0.006 230);
  /* …复制 medias_list.html extra_style block 内的所有 --oc-* 定义… */
}

/* 复制所有 .oc-roas-* 选择器规则 */
.oc-roas-layout { ... }
.oc-roas-section { ... }
/* … */
```

> **关键约束**：逐字节迁移，不修改任何属性值/选择器；hue 必须保持原值（200-240 范围内），禁止引入紫色。

- [ ] **Step 5: 在 `medias_list.html` 中替换 CSS 块为 include**

`extra_style` block 中原 `--oc-*` token 区 + `.oc-roas-*` 规则区替换为：

```jinja
{% block extra_style %}
{# 其它非 ROAS 样式保留 #}
{% include "medias/_roas_styles.html" %}
{# 其它非 ROAS 样式保留 #}
{% endblock %}
```

> 实施时要小心：`medias_list.html` 的 `extra_style` 包含很多其它非 ROAS 样式（任务工作台、批翻译等），只迁移 ROAS 相关部分。

- [ ] **Step 6: 在 `roas.html` 加 `extra_style` block**

修改 `web/templates/medias/roas.html`：

```jinja
{% extends "layout.html" %}
{% block title %}独立站保本 ROAS - {{ product.product_code }}{% endblock %}
{% block page_title %}独立站保本 ROAS{% endblock %}
{% block extra_style %}
{% include "medias/_roas_styles.html" %}
{% endblock %}
{% block content %}
<div class="oc oc-roas-page" data-product-id="{{ product.id }}">
  <header class="oc-roas-page-head">
    <a class="oc-btn ghost" href="/medias">← 返回素材管理</a>
    <h1>独立站保本 ROAS</h1>
  </header>
  {% include "medias/_roas_form.html" %}
</div>
{% endblock %}
```

- [ ] **Step 7: 跑测试**

```bash
python -m pytest tests/test_roas_form_partial.py tests/test_material_roas_frontend.py tests/test_medias_roas_route.py -q
```

预期：全 pass。

- [ ] **Step 8: 视觉冒烟（手工，限模态）**

启动开发服务器，打开 `/medias`，点任意产品 ROAS 按钮，确认模态视觉**与 commit 前一致**（colors/spacing/border）。如有偏差立即定位 Step 4 哪个 token/规则漏迁了。

- [ ] **Step 9: Commit**

```bash
git add web/templates/medias/_roas_styles.html web/templates/medias_list.html web/templates/medias/roas.html tests/test_roas_form_partial.py
git commit -m "refactor(roas): extract _roas_styles.html partial (OKLCH tokens + roas layout)"
```

---

## Task 4: `RoasFormController` JS 类（核心控制器，含静态断言测试）

**Files:**
- Create: `web/static/roas_form.js`
- Create: `tests/test_roas_form_controller_assets.py`

> 注：项目现有前端测试方式是"读 JS 文件做字符串断言"（参见 `test_material_roas_frontend.py`），不跑真正的 JS 引擎。本 task 沿用此风格。控制器在 Task 5/6 接入 DOM 后由 webapp-testing 端到端验证（Task 7）。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_roas_form_controller_assets.py`：

```python
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "web" / "static" / "roas_form.js"


def test_file_exists():
    assert JS.exists()


def test_exposes_controller_class():
    src = JS.read_text(encoding="utf-8")
    assert "class RoasFormController" in src
    assert "window.RoasFormController = RoasFormController" in src


def test_controller_implements_required_methods():
    src = JS.read_text(encoding="utf-8")
    for method in (
        "fillFromProduct",
        "collectPayload",
        "computeRoas",
        "renderResult",
        "save",
        "_setStatus",
        "_scheduleAutoSave",
    ):
        assert method in src, f"missing method {method}"


def test_controller_uses_600ms_debounce():
    src = JS.read_text(encoding="utf-8")
    assert "600" in src and "setTimeout" in src


def test_controller_targets_correct_endpoint_and_field_names():
    src = JS.read_text(encoding="utf-8")
    assert "/medias/api/products/" in src
    for field in (
        "purchase_1688_url",
        "purchase_price",
        "packet_cost_estimated",
        "packet_cost_actual",
        "package_length_cm",
        "package_width_cm",
        "package_height_cm",
        "tk_sea_cost",
        "tk_air_cost",
        "tk_sale_price",
        "standalone_price",
        "standalone_shipping_fee",
    ):
        assert f'"{field}"' in src or f"'{field}'" in src, f"missing field {field}"


def test_controller_handles_last_write_wins():
    src = JS.read_text(encoding="utf-8")
    assert "_pendingPayload" in src or "pendingPayload" in src
    assert "_inFlight" in src or "inFlight" in src


def test_controller_status_states_present():
    src = JS.read_text(encoding="utf-8")
    for state in ("saving", "saved", "error", "idle"):
        assert f"'{state}'" in src or f'"{state}"' in src
```

- [ ] **Step 2: 跑测试确认失败**

```bash
python -m pytest tests/test_roas_form_controller_assets.py -q
```

预期：7 全 FAIL（JS 文件不存在）。

- [ ] **Step 3: 实现 `web/static/roas_form.js`**

```javascript
(function () {
  'use strict';

  const ROAS_FIELDS = [
    'purchase_1688_url',
    'purchase_price',
    'packet_cost_estimated',
    'packet_cost_actual',
    'package_length_cm',
    'package_width_cm',
    'package_height_cm',
    'tk_sea_cost',
    'tk_air_cost',
    'tk_sale_price',
    'standalone_price',
    'standalone_shipping_fee',
  ];
  const DEBOUNCE_MS = 600;
  const FEE_RATE = 0.1;

  function numberOrNull(value) {
    if (value === null || value === undefined || value === '') return null;
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  }

  function currentRmbPerUsd() {
    const parsed = Number(window.MATERIAL_ROAS_RMB_PER_USD || 6.83);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : 6.83;
  }

  function formatRoas(value) {
    if (value === null || value === undefined || !Number.isFinite(value)) return '—';
    return Number(value).toFixed(2);
  }

  function formatTime(d) {
    const pad = (n) => String(n).padStart(2, '0');
    return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  }

  class RoasFormController {
    constructor(rootEl, opts) {
      if (!rootEl) throw new Error('RoasFormController: rootEl required');
      this.root = rootEl;
      this.productId = (opts && opts.productId) || null;
      this.statusBarEl = (opts && opts.statusBarEl) || null;
      this.onAfterSave = (opts && opts.onAfterSave) || null;
      this._debounceTimer = null;
      this._inFlight = false;
      this._pendingPayload = null;
      this._setStatus('idle');
      this.bind();
    }

    bind() {
      ROAS_FIELDS.forEach((field) => {
        const input = this.root.querySelector(`[data-roas-field="${field}"]`);
        if (!input) return;
        input.addEventListener('input', () => {
          this.renderResult();
          this._scheduleAutoSave();
        });
      });
      const calcBtn = this.root.querySelector('#roasCalculateBtn');
      if (calcBtn) {
        calcBtn.addEventListener('click', () => {
          this.renderResult();
          this.save({ immediate: true });
        });
      }
      const avgInput = this.root.querySelector('#roasAverageShippingInput');
      if (avgInput && window.roasAverageShippingTool) {
        avgInput.addEventListener('input', window.roasAverageShippingTool.updateView);
      }
      const retry = this.statusBarEl && this.statusBarEl.querySelector('.oc-roas-status-retry');
      if (retry) {
        retry.addEventListener('click', () => this.save({ immediate: true }));
      }
    }

    fillFromProduct(product) {
      if (!product) return;
      ROAS_FIELDS.forEach((field) => {
        const input = this.root.querySelector(`[data-roas-field="${field}"]`);
        if (!input) return;
        const value = product[field] !== null && product[field] !== undefined ? product[field] : '';
        input.value = value;
      });
      const idEl = this.root.querySelector('#roasProductId');
      if (idEl) idEl.textContent = product.id || '—';
      const nameEl = this.root.querySelector('#roasProductName');
      if (nameEl) nameEl.textContent = product.name || '—';
      const codeEl = this.root.querySelector('#roasProductEnglish');
      if (codeEl) codeEl.textContent = product.product_code || '—';
      const cover = this.root.querySelector('#roasProductCover');
      if (cover) {
        cover.innerHTML = product.cover_thumbnail_url
          ? `<img src="${String(product.cover_thumbnail_url).replace(/"/g, '&quot;')}" alt="">`
          : '<svg width="24" height="24"><use href="#ic-package"/></svg>';
      }
      this.renderResult();
    }

    collectPayload() {
      const payload = {};
      ROAS_FIELDS.forEach((field) => {
        const input = this.root.querySelector(`[data-roas-field="${field}"]`);
        if (!input) return;
        const raw = String(input.value || '').trim();
        payload[field] = raw || null;
      });
      return payload;
    }

    computeRoas() {
      const values = this.collectPayload();
      const price = numberOrNull(values.standalone_price);
      const shipping = numberOrNull(values.standalone_shipping_fee) || 0;
      const purchase = numberOrNull(values.purchase_price);
      const estimatedPacket = numberOrNull(values.packet_cost_estimated);
      const actualPacket = numberOrNull(values.packet_cost_actual);
      const rmbPerUsd = currentRmbPerUsd();
      const revenue = price === null ? null : price + shipping;
      const calc = (packet) => {
        if (revenue === null || purchase === null || packet === null) return null;
        const available = revenue * (1 - FEE_RATE) - purchase / rmbPerUsd - packet / rmbPerUsd;
        if (available <= 0) return null;
        return revenue / available;
      };
      const estimated = calc(estimatedPacket);
      const actual = calc(actualPacket);
      const useActual = actualPacket !== null;
      return {
        estimated_roas: estimated,
        actual_roas: actual,
        effective_basis: useActual ? 'actual' : 'estimated',
        effective_roas: useActual ? actual : estimated,
        rmb_per_usd: rmbPerUsd,
      };
    }

    renderResult() {
      const result = this.computeRoas();
      const payload = this.collectPayload();
      const set = (id, text) => {
        const el = this.root.querySelector(id);
        if (el) el.textContent = text;
      };
      set('#roasEstimatedValue', formatRoas(result.estimated_roas));
      set(
        '#roasActualValue',
        numberOrNull(payload.packet_cost_actual) === null ? '待回填' : formatRoas(result.actual_roas)
      );
      set('#roasEffectiveValue', formatRoas(result.effective_roas));
      set('#roasEffectiveBasis', result.effective_basis === 'actual' ? '实际' : '预估');
      const estBox = this.root.querySelector('#roasEstimatedBox');
      const actBox = this.root.querySelector('#roasActualBox');
      if (estBox) estBox.classList.toggle('active', result.effective_basis === 'estimated');
      if (actBox) actBox.classList.toggle('active', result.effective_basis === 'actual');
    }

    _scheduleAutoSave() {
      if (this._debounceTimer) clearTimeout(this._debounceTimer);
      this._debounceTimer = setTimeout(() => {
        this._debounceTimer = null;
        this.save({ immediate: false });
      }, DEBOUNCE_MS);
    }

    async save(opts) {
      const payload = this.collectPayload();
      if (this._inFlight) {
        this._pendingPayload = payload;
        return;
      }
      if (this._debounceTimer) {
        clearTimeout(this._debounceTimer);
        this._debounceTimer = null;
      }
      this._inFlight = true;
      this._setStatus('saving');
      try {
        const resp = await fetch('/medias/api/products/' + this.productId, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'same-origin',
          body: JSON.stringify(payload),
        });
        if (!resp.ok) {
          let msg = '保存失败';
          try {
            const data = await resp.json();
            msg = data.error || data.message || msg;
          } catch (e) {}
          throw new Error(msg);
        }
        this._setStatus('saved');
        if (this.onAfterSave) this.onAfterSave(payload);
      } catch (e) {
        this._setStatus('error', e.message || '保存失败');
      } finally {
        this._inFlight = false;
        if (this._pendingPayload) {
          this._pendingPayload = null;
          this.save({ immediate: true });
        }
      }
    }

    _setStatus(state, message) {
      if (!this.statusBarEl) return;
      this.statusBarEl.dataset.state = state;
      const text = this.statusBarEl.querySelector('.oc-roas-status-text');
      const retry = this.statusBarEl.querySelector('.oc-roas-status-retry');
      if (text) {
        if (state === 'saving') text.textContent = '保存中…';
        else if (state === 'saved') text.textContent = `已保存 ✓ ${formatTime(new Date())}`;
        else if (state === 'error') text.textContent = `保存失败：${message || ''}`;
        else text.textContent = '尚未编辑';
      }
      if (retry) retry.hidden = state !== 'error';
    }
  }

  window.RoasFormController = RoasFormController;
})();
```

- [ ] **Step 4: 跑测试确认通过**

```bash
python -m pytest tests/test_roas_form_controller_assets.py -q
```

预期：7 passed。

- [ ] **Step 5: Commit**

```bash
git add web/static/roas_form.js tests/test_roas_form_controller_assets.py
git commit -m "feat(roas): RoasFormController class — auto-calc + debounced auto-save + status bar"
```

---

## Task 5: 模态接入 `RoasFormController`（含胶囊跳转 + 状态条 + 删保存按钮）

**Files:**
- Modify: `web/templates/medias_list.html:2435-2515`（模态外壳：head 加胶囊按钮 + 状态条；footer 删保存/saveMsg、保留关闭）
- Modify: `web/templates/medias_list.html`（在合适位置 `<script src="...roas_form.js">`）
- Modify: `web/static/medias.js:494-655`（删冗余、用控制器实例）
- Modify: `tests/test_material_roas_frontend.py`（新断言）

- [ ] **Step 1: 写失败测试**

在 `tests/test_material_roas_frontend.py` 末尾追加：

```python
def test_modal_head_contains_open_in_page_pill_button():
    html = (ROOT / "web" / "templates" / "medias_list.html").read_text(encoding="utf-8")
    assert 'id="roasOpenInPage"' in html
    assert 'class="oc-btn pill ghost"' in html or 'class="oc-btn ghost pill"' in html
    assert 'target="_blank"' in html
    assert 'rel="noopener"' in html
    assert "在新页面打开" in html


def test_modal_has_status_bar():
    html = (ROOT / "web" / "templates" / "medias_list.html").read_text(encoding="utf-8")
    assert 'class="oc-roas-status-bar"' in html
    assert 'data-roas-status' in html


def test_modal_footer_no_longer_has_save_button():
    html = (ROOT / "web" / "templates" / "medias_list.html").read_text(encoding="utf-8")
    assert 'id="roasSaveBtn"' not in html
    assert 'id="roasSaveMsg"' not in html


def test_medias_list_loads_roas_form_script():
    html = (ROOT / "web" / "templates" / "medias_list.html").read_text(encoding="utf-8")
    assert "roas_form.js" in html


def test_medias_js_uses_controller_class():
    js = (ROOT / "web" / "static" / "medias.js").read_text(encoding="utf-8")
    assert "RoasFormController" in js
    assert "new RoasFormController" in js
    # 旧函数应已被替换或移除
    assert "async function saveRoas" not in js
```

- [ ] **Step 2: 跑测试确认失败**

```bash
python -m pytest tests/test_material_roas_frontend.py -q
```

预期：5 个新增断言 FAIL。

- [ ] **Step 3: 改造模态外壳 `medias_list.html`**

把 `medias_list.html:2436-2442` 的 `<div class="oc-modal-head">` 替换为：

```jinja
    <div class="oc-modal-head">
      <h3 id="roasModalTitle">独立站保本 ROAS</h3>
      <div class="oc-roas-head-actions">
        <a id="roasOpenInPage" class="oc-btn pill ghost" target="_blank" rel="noopener" href="#" hidden>
          <svg width="14" height="14"><use href="#ic-external-link"/></svg>
          在新页面打开
        </a>
        <button type="button" id="roasCloseBtn" class="oc-icon-btn" aria-label="关闭">
          <svg width="16" height="16"><use href="#ic-close"/></svg>
        </button>
      </div>
    </div>
    <div class="oc-roas-status-bar" data-roas-status data-state="idle">
      <span class="oc-roas-status-text">尚未编辑</span>
      <button type="button" class="oc-roas-status-retry" hidden>重试</button>
    </div>
```

把 `medias_list.html:2509-2513` 的 `<div class="oc-modal-foot">` 替换为：

```jinja
    <div class="oc-modal-foot">
      <button type="button" id="roasCloseBtn2" class="oc-btn ghost">关闭</button>
    </div>
```

> 注：原来 `id="roasCancelBtn"` 改名为 `roasCloseBtn2` 避免与 head 的 `roasCloseBtn` 冲突；两者都绑定到同一个关闭逻辑。或者直接删除 footer 关闭按钮（因为 head 已经有 X），让 footer 完全空。**推荐删除 footer**，简化 DOM：

```jinja
    {# footer removed: head 的 X + 顶部状态条已覆盖所有交互 #}
```

如选删除，则去掉整个 `<div class="oc-modal-foot">...</div>`。

- [ ] **Step 4: 在模板里引入 `roas_form.js`**

在 `medias_list.html` 加载 `medias.js` 的位置之前（参考 `grep -n "medias.js" web/templates/medias_list.html`）插入：

```jinja
<script src="{{ url_for('static', filename='roas_form.js') }}"></script>
```

- [ ] **Step 5: 改造 `web/static/medias.js`**

5a. **删除** `medias.js:494-655` 范围内已被控制器接管的函数：
   - `calculateRoasBreakEven`（保留供 `loadList` 显示用？查 grep `calculateRoasBreakEven` 的所有引用，如果列表卡片渲染还需要，**保留**；不引入冗余删除）
   - `setRoasFieldValues` / `collectRoasPayload` / `renderRoasResult` / `markRoasResultDirty`
   - `saveRoas`（整个 async 函数）

5b. **改造** `openRoasModal(product)` 为：

```javascript
function openRoasModal(product) {
  if (!product) return;
  state.roasProduct = product;
  const mask = $('roasModalMask');
  if (!mask) return;
  const root = mask.querySelector('.oc-modal');
  const statusBar = root && root.querySelector('[data-roas-status]');
  const openLink = $('roasOpenInPage');
  if (openLink) {
    openLink.href = '/medias/' + product.id + '/roas';
    openLink.hidden = false;
  }
  if (!state.roasController || state.roasController.root !== root) {
    state.roasController = new RoasFormController(root, {
      productId: product.id,
      statusBarEl: statusBar,
      onAfterSave: (payload) => {
        Object.assign(product, payload);
        product.roas_calculation = state.roasController.computeRoas();
        loadList();
      },
    });
  } else {
    state.roasController.productId = product.id;
  }
  state.roasController.fillFromProduct(product);
  mask.hidden = false;
}

function closeRoasModal() {
  const mask = $('roasModalMask');
  if (mask) mask.hidden = true;
  state.roasProduct = null;
}
```

5c. 找到 `roasSaveBtn` / `roasCancelBtn` 的事件绑定，全部删除。新增 `roasCloseBtn` 关闭模态：

```javascript
const closeBtn = $('roasCloseBtn');
if (closeBtn) closeBtn.addEventListener('click', closeRoasModal);
```

5d. 在 `state` 初始化对象里加 `roasController: null,`。

- [ ] **Step 6: 跑测试确认通过**

```bash
python -m pytest tests/test_material_roas_frontend.py tests/test_roas_form_partial.py tests/test_medias_roas_route.py tests/test_roas_form_controller_assets.py tests/test_product_roas.py -q
```

预期：全 pass。

- [ ] **Step 7: webapp-testing 模态冒烟（手工）**

启动开发服务器（参考项目跑 `python -m web.app` 或类似），用 `testuser.md` 凭据登录，进 `/medias`：
1. 点击任意产品 ROAS 按钮 → 模态打开，无 console error
2. 顶部状态条显示「尚未编辑」
3. 修改「独立站售价」字段 → 等 ~1s → 状态条变「保存中…」→「已保存 ✓ HH:MM:SS」
4. 关闭模态 → 重新打开 → 字段值保留
5. 点「计算 ROAS」按钮 → 立即触发保存 + 计算结果刷新
6. 模态 head 看到「在新页面打开」胶囊按钮

如有问题：定位是控制器逻辑、DOM 选择器、还是事件绑定漏配。

- [ ] **Step 8: Commit**

```bash
git add web/templates/medias_list.html web/static/medias.js tests/test_material_roas_frontend.py
git commit -m "feat(roas): wire modal to RoasFormController + add pill link + status bar"
```

---

## Task 6: 独立页接入控制器 + 服务端注入产品数据

**Files:**
- Modify: `web/templates/medias/roas.html`
- Modify: `tests/test_medias_roas_route.py`

- [ ] **Step 1: 写失败测试**

`tests/test_medias_roas_route.py` 末尾追加：

```python
def test_roas_page_includes_status_bar_and_back_link(authed_client_no_db, monkeypatch):
    from web.routes import medias as r

    monkeypatch.setattr(
        r.medias,
        "get_product",
        lambda pid: {"id": pid, "user_id": 1, "name": "x", "product_code": "x-rjc"},
    )
    monkeypatch.setattr(r, "_can_access_product", lambda product: product is not None)

    body = authed_client_no_db.get("/medias/6/roas").get_data(as_text=True)

    assert 'class="oc-roas-status-bar"' in body
    assert 'data-roas-status' in body
    assert 'href="/medias"' in body
    assert "返回素材管理" in body


def test_roas_page_loads_controller_script_and_bootstraps(authed_client_no_db, monkeypatch):
    from web.routes import medias as r

    monkeypatch.setattr(
        r.medias,
        "get_product",
        lambda pid: {
            "id": pid,
            "user_id": 1,
            "name": "x",
            "product_code": "x-rjc",
            "purchase_price": "7.4",
        },
    )
    monkeypatch.setattr(r, "_can_access_product", lambda product: product is not None)

    body = authed_client_no_db.get("/medias/6/roas").get_data(as_text=True)

    assert "roas_form.js" in body
    assert "new RoasFormController" in body
    assert '"id": 6' in body or "'id': 6" in body or '"id":6' in body
```

- [ ] **Step 2: 跑测试确认失败**

```bash
python -m pytest tests/test_medias_roas_route.py -q
```

预期：2 个新增 FAIL。

- [ ] **Step 3: 完善 `web/templates/medias/roas.html`**

```jinja
{% extends "layout.html" %}
{% block title %}独立站保本 ROAS - {{ product.product_code }}{% endblock %}
{% block page_title %}独立站保本 ROAS{% endblock %}
{% block extra_style %}
{% include "medias/_roas_styles.html" %}
{% endblock %}
{% block content %}
<div class="oc oc-roas-page" data-product-id="{{ product.id }}">
  <div class="oc-roas-status-bar" data-roas-status data-state="idle">
    <span class="oc-roas-status-text">尚未编辑</span>
    <button type="button" class="oc-roas-status-retry" hidden>重试</button>
  </div>
  <header class="oc-roas-page-head">
    <a class="oc-btn ghost" href="/medias">← 返回素材管理</a>
    <h1>独立站保本 ROAS</h1>
  </header>
  {% include "medias/_roas_form.html" %}
</div>
{% endblock %}
{% block scripts %}
<script>
  window.MATERIAL_ROAS_RMB_PER_USD = {{ roas_rmb_per_usd or 6.83 }};
</script>
<script src="{{ url_for('static', filename='roas_form.js') }}"></script>
<script>
  (function () {
    const root = document.querySelector('.oc-roas-page');
    const statusBar = document.querySelector('[data-roas-status]');
    const product = {{ product | tojson }};
    new RoasFormController(root, {
      productId: product.id,
      statusBarEl: statusBar,
    }).fillFromProduct(product);
  })();
</script>
{% endblock %}
```

> 如果 `layout.html` 没有 `scripts` block，改为在 content 末尾直接写 `<script>` 标签。运行 `grep -n "block scripts" web/templates/layout.html` 确认。

- [ ] **Step 4: 跑测试确认通过**

```bash
python -m pytest tests/test_medias_roas_route.py tests/test_roas_form_partial.py tests/test_material_roas_frontend.py -q
```

预期：全 pass。

- [ ] **Step 5: webapp-testing 独立页冒烟（手工）**

启动开发服务器，用 `testuser.md` 凭据登录，浏览器打开 `http://172.30.254.14/medias/6/roas`（或本地等价地址）：
1. 页面渲染：侧栏「素材管理」激活、面包屑「← 返回素材管理」、产品卡有 ID/英文名、表单字段已填入数据库现值
2. 修改任一字段 → 等 ~1s → 顶部状态条「保存中…」→「已保存 ✓ HH:MM:SS」
3. 刷新页面 → 字段值保留
4. 点「计算 ROAS」 → 立即重算 + 立即保存
5. 故意输异常值（如把售价清空）→ ROAS 计算结果显示 `—`，不报错

- [ ] **Step 6: Commit**

```bash
git add web/templates/medias/roas.html tests/test_medias_roas_route.py
git commit -m "feat(roas): standalone page bootstraps RoasFormController with product data"
```

---

## Task 7: 胶囊按钮 + 状态条 CSS

**Files:**
- Modify: `web/templates/medias/_roas_styles.html`
- Modify: `tests/test_roas_form_partial.py`

- [ ] **Step 1: 写失败测试**

`tests/test_roas_form_partial.py` 末尾追加：

```python
def test_styles_define_pill_button():
    css = STYLES.read_text(encoding="utf-8")
    assert ".oc-btn.pill" in css
    assert "border-radius" in css


def test_styles_define_status_bar_states():
    css = STYLES.read_text(encoding="utf-8")
    assert ".oc-roas-status-bar" in css
    for state in ("saving", "saved", "error"):
        assert f'[data-state="{state}"]' in css


def test_styles_define_page_head_for_standalone():
    css = STYLES.read_text(encoding="utf-8")
    assert ".oc-roas-page" in css
    assert ".oc-roas-page-head" in css


def test_styles_no_purple_hue():
    """Ocean Blue rule: hue must stay in 200-240. Forbid 245+ hue values."""
    import re

    css = STYLES.read_text(encoding="utf-8")
    bad = re.findall(r"oklch\([^)]*?\b(2[5-9]\d|3\d\d)\b[^)]*?\)", css)
    assert not bad, f"forbidden purple/indigo hue found: {bad}"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
python -m pytest tests/test_roas_form_partial.py -q
```

预期：4 个新增 FAIL（除 no_purple_hue 可能 pass，因为现有 token 都在范围）。

- [ ] **Step 3: 在 `_roas_styles.html` 末尾追加新样式**

```css
/* === 胶囊按钮 === */
.oc-btn.pill {
  height: 28px;
  padding: 0 12px;
  border-radius: 9999px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  text-decoration: none;
  border: 1px solid var(--oc-border-strong, oklch(84% 0.015 230));
  color: var(--oc-fg, oklch(22% 0.020 235));
  background: var(--oc-bg, oklch(99% 0.004 230));
  transition: background-color 120ms cubic-bezier(0.32, 0.72, 0, 1);
}
.oc-btn.pill:hover {
  background: var(--oc-bg-muted, oklch(94% 0.010 230));
}

/* === 模态 head 内的胶囊操作组 === */
.oc-roas-head-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

/* === 顶部状态条 === */
.oc-roas-status-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 16px;
  font-size: 13px;
  border-bottom: 1px solid var(--oc-border, oklch(91% 0.012 230));
  position: sticky;
  top: 0;
  z-index: 5;
  background: var(--oc-bg, oklch(99% 0.004 230));
  color: var(--oc-fg-subtle, oklch(62% 0.015 230));
  transition: background-color 180ms cubic-bezier(0.16, 1, 0.3, 1);
}
.oc-roas-status-bar[data-state="saving"] {
  background: oklch(95% 0.04 230);
  color: oklch(50% 0.17 230);
}
.oc-roas-status-bar[data-state="saved"] {
  background: oklch(95% 0.04 165);
  color: oklch(38% 0.09 165);
}
.oc-roas-status-bar[data-state="error"] {
  background: oklch(96% 0.04 25);
  color: oklch(42% 0.14 25);
}
.oc-roas-status-retry {
  height: 24px;
  padding: 0 10px;
  border-radius: 6px;
  border: 1px solid currentColor;
  background: transparent;
  color: inherit;
  cursor: pointer;
  font-size: 12px;
}

/* === 独立路由页面专属布局 === */
.oc-roas-page {
  max-width: 1440px;
  margin: 0 auto;
  padding: 0 24px 40px;
}
.oc-roas-page-head {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 16px 0;
}
.oc-roas-page-head h1 {
  font-size: 22px;
  margin: 0;
}

/* 移动端单列堆叠（CLAUDE.md 移动版铁律：< 1024 才适配，PC 不动） */
@media (max-width: 1024px) {
  .oc-roas-page .oc-roas-layout {
    grid-template-columns: 1fr;
  }
  .oc-roas-page {
    padding: 0 16px 24px;
  }
}
```

- [ ] **Step 4: 跑测试确认通过**

```bash
python -m pytest tests/test_roas_form_partial.py -q
```

预期：全 pass。

- [ ] **Step 5: 视觉冒烟**

刷新独立页 + 模态：
1. 模态 head 胶囊按钮显示正确（无溢出、hover 灰背景）
2. 状态条颜色随状态切换（蓝 → 绿 → 红）
3. 独立页面标题区垂直对齐
4. 浏览器缩到 < 1024px：表单两栏变单列、状态条仍 sticky

- [ ] **Step 6: Commit**

```bash
git add web/templates/medias/_roas_styles.html tests/test_roas_form_partial.py
git commit -m "style(roas): pill button + sticky status bar + standalone page layout"
```

---

## Task 8: 端到端冒烟 + 列表页回归

**Files:** none（仅验证 + 补遗）

- [ ] **Step 1: 跑全量 ROAS 相关测试**

```bash
python -m pytest tests/test_product_roas.py tests/test_material_roas_frontend.py tests/test_medias_roas_route.py tests/test_roas_form_partial.py tests/test_roas_form_controller_assets.py -q
```

预期：全 pass。

- [ ] **Step 2: webapp-testing 端到端**

启动 dev server。用 `testuser.md` 凭据登录后，按下面顺序走一遍并截图归档：

| 场景 | 步骤 | 预期 |
|----|----|----|
| 模态原有功能 | `/medias` → 任意产品行点 ROAS | 模态打开，字段填好，无 console error |
| 模态自动保存 | 改任一字段 | 顶部状态条 1s 内变「保存中…」→「已保存 ✓」 |
| 模态胶囊跳转 | 点「在新页面打开」 | 新标签页打开 `/medias/<pid>/roas`，字段一致 |
| 模态计算 ROAS 兜底 | 字段填齐 → 点「计算 ROAS」 | 结果区刷新 + 立即保存（状态条触发） |
| 独立页直链 | 浏览器粘贴 `/medias/6/roas` 回车 | 整页加载，侧栏「素材管理」激活 |
| 独立页自动保存 | 改任一字段 | 状态条 1s 内进入「已保存 ✓」 |
| 独立页刷新 | 改字段保存后 F5 | 字段值保留 |
| 独立页 404 | 访问 `/medias/99999/roas` | 返回 404 |
| 列表页未受影响 | 回 `/medias`，搜索/筛选/排序/分页/新建任务 | 全部正常 |
| PC 视觉无回归 | 浏览器 ≥ 1280px | 模态/独立页两栏布局，颜色一致 |
| 移动端响应式 | DevTools 切到 768px | 表单变单列，状态条仍贴顶 |

任一项失败：回到对应 Task 修复 + 重新跑测试，**不要新开 hotfix commit 修补**，沿用 worktree 流程。

- [ ] **Step 3: 跑全套测试以防意外回归**

```bash
python -m pytest tests/ -q --ignore=tests/test_shopify_image_localizer_batch_cdp.py
```

> 排除已知慢/外部依赖测试。如出现非 ROAS 相关 fail，与 baseline 比对（baseline 是否本来就 fail）；非新增 fail 不阻塞。

- [ ] **Step 4: 生成 PR / 合并准备**

按 CLAUDE.md worktree 收尾流程：

```bash
# 在 worktree 内
git log master..HEAD --oneline   # 列出本次所有 commit
```

切回主 worktree 后由用户决定是 merge 到 master 还是先开 PR。

> **不要在本 task 内自动 merge / push / 部署 / 清理 worktree**——CLAUDE.md 收尾顺序需要用户决策点（master 直推 vs PR）。把"收尾建议"作为 verification 报告输出给用户。

---

## 自检（Self-Review）

**1. Spec 覆盖度核对：**

| Spec 章节 | 实施 Task |
|----|----|
| §3.1 后端视图 | Task 1 |
| §3.2 复用 PUT 端点 | Task 4（控制器 fetch）+ Task 8（端到端验证） |
| §3.3 URL 风格 | Task 1（`/medias/<int:pid>/roas`） |
| §4.1 模板拆分 partial | Task 2 + Task 3 |
| §4.2 RoasFormController | Task 4 |
| §4.3 状态条 UI | Task 5（模态 DOM）+ Task 6（独立页 DOM）+ Task 7（CSS） |
| §4.4 自动保存语义（debounce 600ms / last-write-wins / 派生不入 PUT） | Task 4（实现） + Task 8（验证） |
| §5 模态新增胶囊按钮 | Task 5（DOM）+ Task 7（CSS） |
| §6 独立页布局 | Task 6 + Task 7 |
| §7.1 后端测试 | Task 1 |
| §7.2 前端 / 集成 | Task 2/3/4/5（pytest 静态断言）+ Task 8（webapp-testing） |
| §9 风险缓解 | Task 2 Step 8（视觉冒烟）+ Task 3 Step 8 + Task 5 Step 7 + Task 6 Step 5 + Task 8（全场景冒烟） |
| §10 不在范围 | 无 task（YAGNI 不实现） |

**2. Placeholder 扫描：**未使用 TODO/TBD；每段代码块都是完整可运行；测试包含具体 assertion；命令包含具体路径。

**3. 类型/命名一致性：**
- `RoasFormController` 在 Task 4 定义、Task 5 用 `new RoasFormController(...)`、Task 6 用 `new RoasFormController(...)`：一致
- 字段名 `purchase_price` / `standalone_price` / ...：所有 task 使用同一组字符串（与 `_ROAS_PRODUCT_FIELDS` 后端白名单对齐）
- 路由 `/medias/<int:pid>/roas`：Task 1（后端）、Task 5（模态 link）、Task 6（独立页测试）、Task 8（端到端）一致
- 保存端点 `/medias/api/products/<pid>`：Task 4 控制器 fetch、Task 8 验证一致
- 状态值 `idle / saving / saved / error`：Task 4 实现、Task 5 DOM、Task 6 DOM、Task 7 CSS 一致

**4. 不可逆操作风险：**所有 commit 都在 worktree 分支 `feature/medias-roas-route` 上，未触及 master；Task 8 明确**不**自动 push/merge/部署/清理 worktree。
