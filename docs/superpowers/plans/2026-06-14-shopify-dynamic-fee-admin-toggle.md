# 手续费真实优先开关搬进超管设置（toggle） Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把手续费真实优先总开关从 env/config 搬到超管「系统设置」页的一个纯开/关 toggle，切换后无需重启即时生效（≤30s）。

**Architecture:** 新增 `system_settings` 键 `shopify_dynamic_fee_enabled`（"1"/"0"）。`shopify_fee_resolver.is_dynamic_fee_effective` 改为优先读此键（进程内缓存 30s），UI > env > config。超管页 `web/routes/admin.py::settings` 加保存/回显，`admin_settings.html` 加 toggle 控件。不改手续费三级链路本身。

**Tech Stack:** Python 3.12、Flask、pytest + monkeypatch、`threading`（缓存锁）。

**Spec:** `docs/superpowers/specs/2026-06-14-shopify-dynamic-fee-admin-toggle-design.md`

---

## File Structure

- **Modify** `appcore/order_analytics/shopify_fee_resolver.py`：加 `import threading, time`；加缓存全局 + `_read_dynamic_fee_toggle()` + `invalidate_dynamic_fee_toggle_cache()`；改 `is_dynamic_fee_effective`。
- **Modify** `tests/test_shopify_fee_dynamic.py`：追加缓存 + 三态单测。
- **Modify** `web/routes/admin.py::settings`：POST general 段保存 toggle + 失效缓存；GET 段回显 context。
- **Modify** `web/templates/admin_settings.html`：general tab 表单加 toggle 控件。
- **Create** `tests/test_shopify_fee_toggle_admin_settings.py`：源码断言测试（照 `test_ad_alert_admin_settings.py` 模式）。

---

## Task 1：resolver 读取 toggle（缓存 + 三态）

**Files:**
- Modify: `appcore/order_analytics/shopify_fee_resolver.py`（import 区 :1-7；`is_dynamic_fee_effective` :67-75）
- Test: `tests/test_shopify_fee_dynamic.py`

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_shopify_fee_dynamic.py` 末尾）

```python
def test_read_dynamic_fee_toggle_caches_and_invalidates(monkeypatch):
    from appcore.order_analytics import shopify_fee_resolver as r
    calls = {"n": 0}
    def fake_get_setting(key):
        calls["n"] += 1
        assert key == "shopify_dynamic_fee_enabled"
        return "0"
    monkeypatch.setattr("appcore.settings.get_setting", fake_get_setting)
    r.invalidate_dynamic_fee_toggle_cache()
    assert r._read_dynamic_fee_toggle() == "0"
    assert r._read_dynamic_fee_toggle() == "0"   # 命中缓存
    assert calls["n"] == 1
    r.invalidate_dynamic_fee_toggle_cache()
    assert r._read_dynamic_fee_toggle() == "0"   # 失效后重查
    assert calls["n"] == 2


def test_read_dynamic_fee_toggle_db_error_returns_none(monkeypatch):
    from appcore.order_analytics import shopify_fee_resolver as r
    def boom(key):
        raise RuntimeError("db down")
    monkeypatch.setattr("appcore.settings.get_setting", boom)
    r.invalidate_dynamic_fee_toggle_cache()
    assert r._read_dynamic_fee_toggle() is None


def test_is_dynamic_fee_effective_toggle_three_states(monkeypatch):
    from datetime import datetime
    from appcore.order_analytics import shopify_fee_resolver as r
    in_window = datetime(2026, 3, 1, 10, 0, 0)
    # toggle="0" → 强制关，即使 env 设过去日
    monkeypatch.setenv("SHOPIFY_DYNAMIC_FEE_EFFECTIVE_AT", "2026-01-01T00:00:00+08:00")
    monkeypatch.setattr(r, "_read_dynamic_fee_toggle", lambda: "0")
    assert r.is_dynamic_fee_effective(in_window) is False
    # toggle="1" → 全量开，即使 env 设未来日；缺 order_time 仍保守
    monkeypatch.setenv("SHOPIFY_DYNAMIC_FEE_EFFECTIVE_AT", "2099-01-01T00:00:00+08:00")
    monkeypatch.setattr(r, "_read_dynamic_fee_toggle", lambda: "1")
    assert r.is_dynamic_fee_effective(in_window) is True
    assert r.is_dynamic_fee_effective(None) is False
    # toggle 未设 → 回退 env/config effective_at
    monkeypatch.setenv("SHOPIFY_DYNAMIC_FEE_EFFECTIVE_AT", "2026-01-01T00:00:00+08:00")
    monkeypatch.setattr(r, "_read_dynamic_fee_toggle", lambda: None)
    assert r.is_dynamic_fee_effective(in_window) is True
    assert r.is_dynamic_fee_effective(datetime(2025, 12, 1, 10, 0, 0)) is False
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_shopify_fee_dynamic.py -k "toggle" -v`
Expected: FAIL，`module ... has no attribute '_read_dynamic_fee_toggle'`

- [ ] **Step 3: 实现**

在 `appcore/order_analytics/shopify_fee_resolver.py` import 区（第 3-4 行 `import os` / `import sys` 附近）加：

```python
import threading
import time
```

在 `_parse_effective_at` 定义（约 :50）**之前**插入缓存读取：

```python
_DYNAMIC_FEE_TOGGLE_KEY = "shopify_dynamic_fee_enabled"
_TOGGLE_CACHE_TTL = 30.0
_toggle_lock = threading.Lock()
_toggle_cache = {"value": None, "fetched_at": 0.0, "primed": False}


def _read_dynamic_fee_toggle() -> str | None:
    """读 system_settings.shopify_dynamic_fee_enabled，进程内缓存 30s。
    DB 异常返回 None（回退 env/config），不抛错。热路径，避免每单查 DB。"""
    now = time.monotonic()
    with _toggle_lock:
        if _toggle_cache["primed"] and now - _toggle_cache["fetched_at"] < _TOGGLE_CACHE_TTL:
            return _toggle_cache["value"]
    try:
        from appcore.settings import get_setting
        value = get_setting(_DYNAMIC_FEE_TOGGLE_KEY)
    except Exception:
        value = None
    with _toggle_lock:
        _toggle_cache.update(value=value, fetched_at=now, primed=True)
    return value


def invalidate_dynamic_fee_toggle_cache() -> None:
    """保存设置后由路由调用，立即失效本进程缓存（其他 worker 靠 TTL 收敛）。"""
    with _toggle_lock:
        _toggle_cache["primed"] = False
```

把 `is_dynamic_fee_effective`（:67-75）替换为：

```python
def is_dynamic_fee_effective(order_time: datetime | None) -> bool:
    toggle = _read_dynamic_fee_toggle()
    if toggle == "0":
        return False                      # UI 显式关闭 → 全部策略 C
    if toggle == "1":
        return order_time is not None     # UI 显式开启 → 全量真实优先（忽略 env/config 日期）
    # toggle 未设 → 回退现有 env/config effective_at 逻辑
    effective_at = _parse_effective_at()
    if effective_at is None or order_time is None:
        return False
    comparable = order_time
    if comparable.tzinfo is not None:
        comparable = comparable.astimezone(timezone.utc).replace(tzinfo=None)
    return comparable >= effective_at
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_shopify_fee_dynamic.py -k "toggle" -v`
Expected: PASS（3 项）

- [ ] **Step 5: 跑回归确认未破坏现有 resolver 测试**

Run: `pytest tests/test_shopify_fee_dynamic.py -q`
Expected: 全部 PASS（现有开关/边界测试不受影响，因它们显式 setenv/setattr 且未设 toggle）

- [ ] **Step 6: Commit**

```bash
git add appcore/order_analytics/shopify_fee_resolver.py tests/test_shopify_fee_dynamic.py
git commit -m "feat(shopify-fee): resolver 优先读 system_settings toggle（缓存 30s，UI>env>config）"
```

---

## Task 2：超管 UI（保存 + 回显 + toggle 控件）

**Files:**
- Modify: `web/routes/admin.py::settings`（POST general 段 :491-498 之后；GET render :564）
- Modify: `web/templates/admin_settings.html`（general tab 表单，TTS 项 :270 之后、`section-divider` :272 之前）
- Test: `tests/test_shopify_fee_toggle_admin_settings.py`（新建）

- [ ] **Step 1: 写失败测试**（新建 `tests/test_shopify_fee_toggle_admin_settings.py`）

```python
from __future__ import annotations

from pathlib import Path


def test_admin_settings_exposes_shopify_fee_toggle():
    admin_source = Path("web/routes/admin.py").read_text(encoding="utf-8")
    template_source = Path("web/templates/admin_settings.html").read_text(encoding="utf-8")

    # POST：按 checkbox 存在性写 "1"/"0" + 失效缓存
    assert 'request.form.get("shopify_dynamic_fee_enabled")' in admin_source
    assert 'set_setting("shopify_dynamic_fee_enabled"' in admin_source
    assert "invalidate_dynamic_fee_toggle_cache" in admin_source
    # GET：回显 context（仅显式 "0" 视作关）
    assert "shopify_dynamic_fee_enabled=" in admin_source
    # 模板：toggle 控件 + 文案
    assert 'name="shopify_dynamic_fee_enabled"' in template_source
    assert "手续费真实优先" in template_source
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_shopify_fee_toggle_admin_settings.py -v`
Expected: FAIL（admin.py / 模板尚无对应片段）

- [ ] **Step 3a: 改 `web/routes/admin.py` POST 保存**

在 `set_setting("tts_max_concurrency", str(n))`（:498）所在的 `if raw_tts_concurrency:` 块**之后**、`old_default = get_retention_hours("__nonexistent__")`（:500）**之前**插入：

```python
        # 手续费真实优先 toggle（checkbox：勾选才进 form）
        fee_enabled = "1" if request.form.get("shopify_dynamic_fee_enabled") else "0"
        set_setting("shopify_dynamic_fee_enabled", fee_enabled)
        try:
            from appcore.order_analytics.shopify_fee_resolver import (
                invalidate_dynamic_fee_toggle_cache,
            )
            invalidate_dynamic_fee_toggle_cache()
        except Exception:
            pass
```

- [ ] **Step 3b: 改 `web/routes/admin.py` GET 回显**

在 `return render_template("admin_settings.html",`（:564）之前的 GET 段（`current = get_all_retention_settings()` :553 附近）加：

```python
    shopify_dynamic_fee_enabled = get_setting("shopify_dynamic_fee_enabled") != "0"
```

在 `render_template("admin_settings.html", ...)` 参数列表里加一行（与 `tts_max_concurrency=tts_concurrency,` 同级）：

```python
        shopify_dynamic_fee_enabled=shopify_dynamic_fee_enabled,
```

> 注：`get_setting` 已在 admin.py 顶部 import（现有 `set_setting`/`get_setting`/`get_retention_hours` 等已用）。无需新增顶层 import。

- [ ] **Step 3c: 改 `web/templates/admin_settings.html` 加 toggle**

在 TTS 并发那个 `field-group`（结束 `</div>` 在 :270）**之后**、`<div class="section-divider"></div>`（:272）**之前**插入：

```html
      <div class="field-group">
        <label>手续费真实优先</label>
        <div class="field-row">
          <input type="checkbox" name="shopify_dynamic_fee_enabled" id="shopify_dynamic_fee_enabled"
                 {% if shopify_dynamic_fee_enabled %}checked{% endif %}>
          <label for="shopify_dynamic_fee_enabled" style="font-weight:normal;margin-left:6px;">启用真实优先手续费</label>
        </div>
        <div class="field-hint">开启：有真实 Payments 手续费用真实，缺则区域费率 / 策略 C 估算；关闭：全部回退策略 C 估算。仅影响后续计算与实时大盘未落库订单，历史已落库数字需单独跑全量重算才更新。无需重启，约 30 秒内生效。</div>
      </div>
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_shopify_fee_toggle_admin_settings.py -v`
Expected: PASS

- [ ] **Step 5: 跑相关回归**

Run: `pytest tests/test_ad_alert_admin_settings.py tests/test_shopify_fee_dynamic.py -q`
Expected: 全部 PASS（同页面其他设置项断言不受影响）

- [ ] **Step 6: Commit**

```bash
git add web/routes/admin.py web/templates/admin_settings.html tests/test_shopify_fee_toggle_admin_settings.py
git commit -m "feat(admin-settings): 手续费真实优先 toggle（保存即失效缓存、即时生效）"
```

---

## Self-Review

- **Spec 覆盖**：①system_settings 键=Task1/Task2；②resolver 读取+优先级+缓存=Task1；③即时生效（invalidate+TTL）=Task1 函数 + Task2 调用；④UI toggle+回显+旁注=Task2；⑤不自动重算=本 plan 不含任何重算调用（符合）；⑥测试=Task1 单测 + Task2 源码断言。无遗漏。
- **占位符**：无；每步含完整代码与命令。
- **类型一致**：`_read_dynamic_fee_toggle()->str|None`、`invalidate_dynamic_fee_toggle_cache()->None`、setting key 字面量 `shopify_dynamic_fee_enabled` 在 resolver/admin/模板/测试中一致；`is_dynamic_fee_effective` 签名不变（`datetime|None -> bool`）。
- **风险**：多 gunicorn worker 下保存只失效当前 worker，其他 worker ≤30s 经 TTL 收敛（设计接受）。GET 回显用 `!= "0"`（NULL/"1"→开），与当前 config 默认=开一致。
