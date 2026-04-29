# 小工具平均运费 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增所有登录用户可见的“小工具”页面，并提供粘贴即算的“平均运费”Tab。

**Architecture:** Flask 只新增页面蓝图和菜单入口，平均运费计算全部在浏览器本地完成。模板内脚本暴露 `window.averageShippingTool.parseValues` 和 `window.averageShippingTool.averageText`，方便测试直接验证解析和四舍五入规则。

**Tech Stack:** Flask Blueprint、Jinja2 模板、原生 JavaScript、pytest。

---

### File Structure

- Create: `web/routes/tools.py`，负责 `/tools/` 页面路由和登录保护。
- Modify: `web/app.py`，导入并注册 tools Blueprint。
- Modify: `web/templates/layout.html`，新增所有用户可见的“小工具”菜单入口。
- Create: `web/templates/tools.html`，负责 Tab UI、输入框、结果展示和本地计算逻辑。
- Create: `tests/test_tools_routes.py`，覆盖路由、菜单入口、普通用户访问。
- Create: `tests/test_tools_average_shipping_template.py`，覆盖模板脚本和样例计算结果。

### Task 1: Routes And Menu Tests

**Files:**
- Create: `tests/test_tools_routes.py`
- Modify later: `web/routes/tools.py`
- Modify later: `web/app.py`
- Modify later: `web/templates/layout.html`

- [ ] **Step 1: Write failing route tests**

```python
def test_tools_page_is_available_to_normal_users(authed_user_client_no_db):
    response = authed_user_client_no_db.get("/tools/")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "小工具" in body
    assert "平均运费" in body


def test_tools_menu_entry_is_visible_to_normal_users(authed_user_client_no_db):
    response = authed_user_client_no_db.get("/tools/")
    body = response.get_data(as_text=True)
    assert 'href="/tools/"' in body
    assert "小工具" in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_tools_routes.py -q`

Expected: FAIL because `/tools/` is not registered yet.

- [ ] **Step 3: Add minimal route and menu implementation**

Create `web/routes/tools.py`:

```python
"""小工具页面 Blueprint."""
from __future__ import annotations

from flask import Blueprint, render_template
from flask_login import login_required

bp = Blueprint("tools", __name__, url_prefix="/tools")


@bp.route("/", methods=["GET"])
@login_required
def index():
    return render_template("tools.html")
```

Modify `web/app.py` to import and register `tools_bp`.

Modify `web/templates/layout.html` to add:

```html
<a href="{{ url_for('tools.index') }}" target="_blank" rel="noopener noreferrer" {% if request.path.startswith('/tools') %}class="active"{% endif %}>
  <span class="nav-icon">🧰</span> 小工具
</a>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_tools_routes.py -q`

Expected: PASS.

### Task 2: Average Shipping Tool Tests

**Files:**
- Create: `tests/test_tools_average_shipping_template.py`
- Modify later: `web/templates/tools.html`

- [ ] **Step 1: Write failing template tests**

```python
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _extract_script() -> str:
    template = (ROOT / "web" / "templates" / "tools.html").read_text(encoding="utf-8")
    match = re.search(r"<script id=\"averageShippingToolScript\">(.*?)</script>", template, re.S)
    assert match, "average shipping script must use a stable script id"
    return match.group(1)


def test_average_shipping_sample_calculates_one_decimal():
    script = _extract_script()
    sample = """预估运费
￥35.1
￥32.1
￥49.07"""
    node_code = script + "\nconsole.log(window.averageShippingTool.averageText(process.argv[1]).display);"
    result = subprocess.run(["node", "-e", node_code, sample], text=True, capture_output=True, check=True)
    assert result.stdout.strip() == "38.8"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_tools_average_shipping_template.py -q`

Expected: FAIL because `tools.html` and the script do not exist yet.

- [ ] **Step 3: Add tools template**

Create `web/templates/tools.html` with:

- `page_title` 为“小工具”。
- Tab 区域包含一个 active Tab：“平均运费”。
- 结果区在输入框上方，显示 `--` 或平均值。
- textarea 粘贴或输入时立即调用计算。
- 脚本暴露 `window.averageShippingTool`。

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_tools_average_shipping_template.py -q`

Expected: PASS.

### Task 3: Focused Verification And Commit

**Files:**
- Verify all changed files.

- [ ] **Step 1: Run focused tests**

Run: `pytest tests/test_tools_routes.py tests/test_tools_average_shipping_template.py -q`

Expected: PASS.

- [ ] **Step 2: Compile touched Python files**

Run: `python -m py_compile web/app.py web/routes/tools.py`

Expected: exit 0.

- [ ] **Step 3: Review diff**

Run: `git diff -- web/app.py web/routes/tools.py web/templates/layout.html web/templates/tools.html tests/test_tools_routes.py tests/test_tools_average_shipping_template.py docs/superpowers/specs/2026-04-29-tools-average-shipping-design.md docs/superpowers/plans/2026-04-29-tools-average-shipping.md`

Expected: diff only contains the small tools page, menu, tests, spec, and plan.

- [ ] **Step 4: Commit**

Run:

```bash
git add web/app.py web/routes/tools.py web/templates/layout.html web/templates/tools.html tests/test_tools_routes.py tests/test_tools_average_shipping_template.py docs/superpowers/specs/2026-04-29-tools-average-shipping-design.md docs/superpowers/plans/2026-04-29-tools-average-shipping.md
git commit -m "feat: add average shipping tool"
```

Expected: commit created on `codex/tools-average-shipping`.

### Self Review

- Spec coverage: menu, Tab, average shipping calculation, immediate display, all-user visibility, and no server-side persistence are covered.
- Placeholder scan: no `TBD`, no incomplete implementation placeholders.
- Type consistency: route endpoint is `tools.index`; JavaScript API is `window.averageShippingTool.averageText`.
