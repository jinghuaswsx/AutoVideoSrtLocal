# API 账单日期范围实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 API 账单页面改成单个日期范围选择器，并支持四个快捷时间按钮的自动加载。

**Architecture:** 保持后端 `from/to` 参数和查询逻辑不变，只改 `admin_ai_billing.html` 的筛选 UI 与内联脚本。通过隐藏字段承接原有 GET 参数，前端自维护双月历状态并在范围选定后自动提交。

**Tech Stack:** Flask/Jinja 模板、原生 JavaScript、pytest

---

### Task 1: 锁定模板回归测试

**Files:**
- Modify: `tests/test_ai_billing_routes.py`
- Test: `tests/test_ai_billing_routes.py`

- [ ] **Step 1: 写失败测试**

```python
def test_ai_billing_template_uses_single_date_range_picker_and_quick_ranges():
    template = (ROOT / "web" / "templates" / "admin_ai_billing.html").read_text(encoding="utf-8")

    assert 'data-billing-date-range-trigger' in template
    assert 'name="from"' in template
    assert 'name="to"' in template
    assert 'data-range-shortcut="today"' in template
    assert 'data-range-shortcut="yesterday"' in template
    assert 'data-range-shortcut="last7"' in template
    assert 'data-range-shortcut="last30"' in template
    assert "initBillingDateRangePicker" in template
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_ai_billing_routes.py::test_ai_billing_template_uses_single_date_range_picker_and_quick_ranges -q`

Expected: FAIL，因为模板里还没有新的范围控件和快捷按钮。

- [ ] **Step 3: 写最小实现让测试可通过**

```html
<button type="button" data-billing-date-range-trigger>...</button>
<input type="hidden" name="from" value="{{ filters.date_from }}">
<input type="hidden" name="to" value="{{ filters.date_to }}">
<button type="button" data-range-shortcut="today">今天</button>
```

- [ ] **Step 4: 重新运行测试确认通过**

Run: `pytest tests/test_ai_billing_routes.py::test_ai_billing_template_uses_single_date_range_picker_and_quick_ranges -q`

Expected: PASS

- [ ] **Step 5: 提交阶段性变更**

```bash
git add tests/test_ai_billing_routes.py web/templates/admin_ai_billing.html
git commit -m "feat: add ai billing date range picker"
```

### Task 2: 实现单个日期范围控件与自动提交

**Files:**
- Modify: `web/templates/admin_ai_billing.html`
- Test: `tests/test_ai_billing_routes.py`

- [ ] **Step 1: 在模板中加入日期范围触发器和隐藏字段**

```html
<div class="billing-range" data-billing-date-range>
  <input type="hidden" name="from" value="{{ filters.date_from }}">
  <input type="hidden" name="to" value="{{ filters.date_to }}">
  <button type="button" class="billing-range-trigger" data-billing-date-range-trigger></button>
</div>
```

- [ ] **Step 2: 增加双月历面板与快捷按钮结构**

```html
<div class="billing-range-shortcuts">
  <button type="button" data-range-shortcut="today">今天</button>
  <button type="button" data-range-shortcut="yesterday">昨天</button>
  <button type="button" data-range-shortcut="last7">近七天</button>
  <button type="button" data-range-shortcut="last30">近一个月</button>
</div>
```

- [ ] **Step 3: 加入最小脚本实现开始/结束选择与自动提交**

```javascript
function initBillingDateRangePicker() {
  // 初始化 from/to、渲染日历、第二次点选后自动 form.submit()
}
```

- [ ] **Step 4: 运行整组路由测试**

Run: `pytest tests/test_ai_billing_routes.py -q`

Expected: PASS

### Task 3: 页面验证、提交、合并与发布

**Files:**
- Modify: `docs/superpowers/specs/2026-04-24-ai-billing-date-range-design.md`
- Modify: `docs/superpowers/plans/2026-04-24-ai-billing-date-range.md`

- [ ] **Step 1: 运行页面相关验证**

Run: `pytest tests/test_ai_billing_routes.py -q`

Expected: PASS

- [ ] **Step 2: 检查工作区差异**

Run: `git status --short`

Expected: 仅包含本次相关文件。

- [ ] **Step 3: 提交分支**

```bash
git add docs/superpowers/specs/2026-04-24-ai-billing-date-range-design.md docs/superpowers/plans/2026-04-24-ai-billing-date-range.md tests/test_ai_billing_routes.py web/templates/admin_ai_billing.html
git commit -m "feat: improve ai billing date range filters"
```

- [ ] **Step 4: 合并回 master**

```bash
git checkout master
git pull --ff-only origin master
git merge --ff-only codex/ai-billing-date-range
```

- [ ] **Step 5: 发布线上并检查**

```bash
# 按项目既有发布方式把 master 发布到 /opt/autovideosrt
# 然后检查 autovideosrt.service 与首页 / API 账单页可达性
```
