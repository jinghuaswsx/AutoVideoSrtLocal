# 素材管理 ROAS Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在素材管理产品行新增 ROAS 维护入口，保存产品成本信息并实时计算独立站预估/实际/有效保本 ROAS。

**Architecture:** 产品级 ROAS 数据直接追加到 `media_products`，沿用现有素材管理产品 API 读写。保本 ROAS 公式放在小型纯函数模块中，后端和测试可复用；前端弹窗负责实时展示计算结果。

**Tech Stack:** Flask、PyMySQL、Jinja 模板、原生 JavaScript、pytest、MySQL migration SQL。

---

## 文件结构

- Create: `appcore/product_roas.py`，放置保本 ROAS 计算和数值归一化。
- Create: `db/migrations/2026_04_28_media_products_roas_fields.sql`，追加 ROAS 字段。
- Modify: `appcore/medias.py`，允许更新 ROAS 字段并做数值归一化。
- Modify: `web/routes/medias.py`，序列化 ROAS 字段，允许 PUT 保存。
- Modify: `web/templates/medias_list.html`，新增 ROAS 弹窗结构和样式。
- Modify: `web/static/medias.js`，新增列表按钮、弹窗打开/保存、实时计算逻辑。
- Create: `tests/test_product_roas.py`，覆盖公式。
- Modify/Create: 素材管理相关测试，覆盖字段白名单、序列化、前端挂载点。

### Task 1: ROAS 计算纯函数

**Files:**
- Create: `appcore/product_roas.py`
- Test: `tests/test_product_roas.py`

- [ ] **Step 1: 写失败测试**

```python
from appcore.product_roas import calculate_break_even_roas


def test_calculates_estimated_and_actual_roas():
    result = calculate_break_even_roas(
        purchase_price=20,
        estimated_packet_cost=10,
        actual_packet_cost=12,
        standalone_price=60,
    )

    assert result["estimated_roas"] == 2.5
    assert result["actual_roas"] == 60 / 22
    assert result["effective_basis"] == "actual"
    assert result["effective_roas"] == 60 / 22


def test_uses_estimated_roas_when_actual_packet_cost_missing():
    result = calculate_break_even_roas(
        purchase_price=20,
        estimated_packet_cost=10,
        actual_packet_cost=None,
        standalone_price=60,
    )

    assert result["estimated_roas"] == 2.5
    assert result["actual_roas"] is None
    assert result["effective_basis"] == "estimated"
    assert result["effective_roas"] == 2.5


def test_returns_none_when_margin_cannot_break_even():
    result = calculate_break_even_roas(
        purchase_price=50,
        estimated_packet_cost=10,
        actual_packet_cost=None,
        standalone_price=60,
    )

    assert result["estimated_roas"] is None
    assert result["effective_roas"] is None
    assert result["effective_basis"] == "estimated"
```

- [ ] **Step 2: 运行失败测试**

Run: `pytest tests/test_product_roas.py -q`

Expected: 因 `appcore.product_roas` 不存在而失败。

- [ ] **Step 3: 实现最小函数**

```python
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any


FEE_RATE = Decimal("0.10")


def decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("value must be numeric") from None


def _roas(price: Decimal | None, purchase: Decimal | None, packet: Decimal | None) -> float | None:
    if price is None or purchase is None or packet is None:
        return None
    available = price * (Decimal("1") - FEE_RATE) - purchase - packet
    if available <= 0:
        return None
    return float(price / available)


def calculate_break_even_roas(
    *,
    purchase_price: Any,
    estimated_packet_cost: Any,
    actual_packet_cost: Any,
    standalone_price: Any,
) -> dict[str, float | str | None]:
    purchase = decimal_or_none(purchase_price)
    estimated = decimal_or_none(estimated_packet_cost)
    actual = decimal_or_none(actual_packet_cost)
    price = decimal_or_none(standalone_price)
    estimated_roas = _roas(price, purchase, estimated)
    actual_roas = _roas(price, purchase, actual)
    use_actual = actual is not None
    return {
        "estimated_roas": estimated_roas,
        "actual_roas": actual_roas,
        "effective_basis": "actual" if use_actual else "estimated",
        "effective_roas": actual_roas if use_actual else estimated_roas,
    }
```

- [ ] **Step 4: 运行通过测试**

Run: `pytest tests/test_product_roas.py -q`

Expected: 3 passed。

### Task 2: 数据库字段与后端保存

**Files:**
- Create: `db/migrations/2026_04_28_media_products_roas_fields.sql`
- Modify: `appcore/medias.py`
- Modify: `web/routes/medias.py`
- Test: `tests/test_product_roas.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_product_roas.py` 追加：

```python
import pytest
from appcore import medias


def test_update_product_accepts_roas_fields(monkeypatch):
    captured = {}

    def fake_execute(sql, args):
        captured["sql"] = sql
        captured["args"] = args
        return 1

    monkeypatch.setattr(medias, "execute", fake_execute)

    medias.update_product(
        9,
        purchase_1688_url="https://detail.1688.com/example",
        purchase_price="20.50",
        packet_cost_estimated="8.20",
        packet_cost_actual="9.30",
        package_length_cm="10",
        package_width_cm="5",
        package_height_cm="3",
        standalone_price="59.99",
    )

    assert "purchase_price=%s" in captured["sql"]
    assert "standalone_price=%s" in captured["sql"]
    assert captured["args"][-1] == 9
    assert captured["args"][1] == 20.5


def test_update_product_rejects_invalid_roas_number():
    with pytest.raises(ValueError):
        medias.update_product(9, purchase_price="abc")
```

- [ ] **Step 2: 运行失败测试**

Run: `pytest tests/test_product_roas.py -q`

Expected: 新字段不在允许列表或校验未实现导致失败。

- [ ] **Step 3: 添加 migration**

字段：

```sql
ALTER TABLE media_products
  ADD COLUMN purchase_1688_url VARCHAR(2048) NULL,
  ADD COLUMN purchase_price DECIMAL(10,2) NULL,
  ADD COLUMN packet_cost_estimated DECIMAL(10,2) NULL,
  ADD COLUMN packet_cost_actual DECIMAL(10,2) NULL,
  ADD COLUMN package_length_cm DECIMAL(8,2) NULL,
  ADD COLUMN package_width_cm DECIMAL(8,2) NULL,
  ADD COLUMN package_height_cm DECIMAL(8,2) NULL,
  ADD COLUMN tk_sea_cost DECIMAL(10,2) NULL,
  ADD COLUMN tk_air_cost DECIMAL(10,2) NULL,
  ADD COLUMN tk_sale_price DECIMAL(10,2) NULL,
  ADD COLUMN standalone_price DECIMAL(10,2) NULL;
```

实际文件使用 `information_schema.columns` 包裹，保证重复执行安全。

- [ ] **Step 4: 修改 `appcore/medias.py`**

把 ROAS 字段加入 `allowed`，对金额和尺寸字段用 `product_roas.decimal_or_none` 归一化成 `float` 或 `None`，URL 做 strip。

- [ ] **Step 5: 修改 `web/routes/medias.py`**

`api_update_product` 接收 ROAS 字段加入 `update_fields`。`_serialize_product` 返回同名字段，并追加 `roas_calculation`。

- [ ] **Step 6: 运行通过测试**

Run: `pytest tests/test_product_roas.py -q`

Expected: 全部通过。

### Task 3: 前端弹窗和实时计算

**Files:**
- Modify: `web/templates/medias_list.html`
- Modify: `web/static/medias.js`
- Test: `tests/test_material_roas_frontend.py`

- [ ] **Step 1: 写失败测试**

```python
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_medias_list_has_roas_modal_mount():
    html = (ROOT / "web" / "templates" / "medias_list.html").read_text(encoding="utf-8")
    assert 'id="roasModalMask"' in html
    assert 'id="roasForm"' in html
    assert "独立站保本 ROAS" in html


def test_medias_js_wires_roas_button_and_calculation():
    js = (ROOT / "web" / "static" / "medias.js").read_text(encoding="utf-8")
    assert "data-roas" in js
    assert "openRoasModal" in js
    assert "calculateRoasBreakEven" in js
    assert "packet_cost_actual" in js
```

- [ ] **Step 2: 运行失败测试**

Run: `pytest tests/test_material_roas_frontend.py -q`

Expected: 找不到 ROAS 弹窗和 JS 函数而失败。

- [ ] **Step 3: 模板新增弹窗**

在 `medias_list.html` 追加 `roasModalMask`，沿用 `oc-modal-mask`、`oc-modal`、`oc-field` 等现有样式。新增紧凑 CSS 类控制产品信息区、表单网格、结果区和 TK 可选区。

- [ ] **Step 4: JS 新增按钮和逻辑**

在产品行操作区添加：

```html
<button class="oc-btn sm ghost" data-roas="${p.id}"><span>ROAS</span></button>
```

新增 `openRoasModal(product)`、`closeRoasModal()`、`collectRoasPayload()`、`calculateRoasBreakEven()`、`renderRoasResult()`、`saveRoas()`。

- [ ] **Step 5: 运行前端静态测试**

Run: `pytest tests/test_material_roas_frontend.py -q`

Expected: 2 passed。

### Task 4: 集成验证

**Files:**
- All changed files

- [ ] **Step 1: 运行新增低耦合测试**

Run: `pytest tests/test_product_roas.py tests/test_material_roas_frontend.py -q`

Expected: 全部通过。

- [ ] **Step 2: 运行语法检查**

Run: `python -m py_compile appcore/product_roas.py appcore/medias.py web/routes/medias.py`

Expected: exit 0。

- [ ] **Step 3: 记录 DB 相关测试限制**

不要在 Windows 本地启动 MySQL。若运行旧素材管理 DB 测试失败且报 `127.0.0.1:3306` 连接拒绝，在最终说明中记录为环境限制。
