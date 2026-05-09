# 实时大盘利润 KPI 利润率字段 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在实时大盘 KPI「总利润额」卡片下方显示利润率（= profit_with_estimate_usd / total_revenue_usd × 100），后端 API 同步暴露 `profit_with_estimate_margin_pct` 字段。

**Architecture:** 后端 `appcore/order_analytics/realtime.py` 在两个 `_build_order_profit_summary*` 出口的 rounding 循环之后追加百分比计算（含 `total_revenue_usd ≤ 0 → None` 兜底）；前端 `web/templates/order_analytics.html` 在「总利润额」KPI 卡 markup 内加一个 sub 节点，在 `renderRealtimeOrderProfitSummary` 末尾写文案 + 着色。

**Tech Stack:** Python 3 / Flask / Jinja2 / 原生 JS / pytest。

**Spec:** [docs/superpowers/specs/2026-05-10-realtime-dashboard-profit-margin.md](../specs/2026-05-10-realtime-dashboard-profit-margin.md)

---

## File Structure

| 文件 | 动作 | 责任 |
|------|------|------|
| `appcore/order_analytics/realtime.py` | Modify | `_empty_order_profit_summary` 加默认 `None`；`_build_order_profit_summary` / `_build_order_profit_summary_from_status` 计算 `profit_with_estimate_margin_pct` |
| `tests/test_order_analytics_realtime_profit_margin.py` | Create | 单元测试覆盖三态（正利润 / 零营收 / 负利润）+ `_empty` 默认值 |
| `web/templates/order_analytics.html` | Modify | KPI 卡新增 `realtimeProfitTotalMargin` sub 节点 + JS 渲染 |
| `CLAUDE.md` | Modify | 「实时大盘店铺筛选（2026-05-09 起）」章节追加锚点 cross-reference |

---

### Task 1: 后端 `_build_order_profit_summary` 主路径加利润率字段

**Files:**
- Create: `tests/test_order_analytics_realtime_profit_margin.py`
- Modify: `appcore/order_analytics/realtime.py`（`_empty_order_profit_summary` 第 151-173 行；`_build_order_profit_summary` 第 250-256 行 rounding 循环之后）

- [ ] **Step 1.1: 写失败测试**

把以下内容写入 `tests/test_order_analytics_realtime_profit_margin.py`：

```python
"""Tests for profit_with_estimate_margin_pct in realtime overview summary.

Spec: docs/superpowers/specs/2026-05-10-realtime-dashboard-profit-margin.md
"""

from __future__ import annotations

from appcore.order_analytics.realtime import (
    _build_order_profit_summary,
    _build_order_profit_summary_from_status,
    _empty_order_profit_summary,
)


def _row(
    *,
    total_revenue: float,
    purchase: float = 0.0,
    logistics: float = 0.0,
    shopify_fee: float = 0.0,
    ad_cost: float = 0.0,
) -> dict:
    return {
        "total_revenue": total_revenue,
        "refund_deduction_usd": 0.0,
        "return_reserve_usd": 0.0,
        "profit_deduction_usd": 0.0,
        "purchase_cost_usd": purchase,
        "purchase_estimate_usd": 0.0,
        "logistics_cost_usd": logistics,
        "logistics_estimate_usd": 0.0,
        "shopify_fee_total_usd": shopify_fee,
        "ad_cost_usd": ad_cost,
        "purchase_cost_missing": False,
        "logistics_cost_missing": False,
    }


def test_empty_order_profit_summary_has_margin_key_default_none():
    summary = _empty_order_profit_summary()
    assert "profit_with_estimate_margin_pct" in summary
    assert summary["profit_with_estimate_margin_pct"] is None


def test_build_order_profit_summary_positive_margin():
    rows = [_row(total_revenue=100.0, purchase=30.0, logistics=10.0, shopify_fee=5.0, ad_cost=15.0)]
    summary = _build_order_profit_summary(rows, total_ad_spend_usd=15.0)
    assert summary["total_revenue_usd"] == 100.0
    assert summary["profit_with_estimate_usd"] == 40.0
    assert summary["profit_with_estimate_margin_pct"] == 40.0


def test_build_order_profit_summary_zero_revenue_returns_none():
    summary = _build_order_profit_summary([], total_ad_spend_usd=0.0)
    assert summary["total_revenue_usd"] == 0.0
    assert summary["profit_with_estimate_margin_pct"] is None


def test_build_order_profit_summary_negative_profit_negative_margin():
    rows = [_row(total_revenue=50.0, purchase=40.0, logistics=10.0, shopify_fee=5.0, ad_cost=20.0)]
    summary = _build_order_profit_summary(rows, total_ad_spend_usd=20.0)
    assert summary["profit_with_estimate_usd"] == -25.0
    assert summary["profit_with_estimate_margin_pct"] == -50.0


def test_build_order_profit_summary_two_decimal_rounding():
    rows = [_row(total_revenue=300.0, purchase=99.999, logistics=0.0, shopify_fee=0.0, ad_cost=0.0)]
    summary = _build_order_profit_summary(rows, total_ad_spend_usd=0.0)
    margin = summary["profit_with_estimate_margin_pct"]
    assert isinstance(margin, float)
    assert margin == round(margin, 2)


def test_build_order_profit_summary_from_status_includes_margin():
    status = {
        "total_revenue_usd": 200.0,
        "purchase_cost_with_estimate_usd": 80.0,
        "shipping_cost_with_estimate_usd": 20.0,
        "unallocated_ad_spend_usd": 0.0,
        "overview": {"line_count": 3, "total_profit_usd": 50.0},
        "summary": {"ok": {}, "incomplete": {}},
        "estimated": {"lines": 0},
    }
    summary = _build_order_profit_summary_from_status(status, order_count=3)
    assert summary["total_revenue_usd"] == 200.0
    assert summary["profit_with_estimate_usd"] == 50.0
    assert summary["profit_with_estimate_margin_pct"] == 25.0


def test_build_order_profit_summary_from_status_zero_revenue_returns_none():
    status = {
        "total_revenue_usd": 0.0,
        "purchase_cost_with_estimate_usd": 0.0,
        "shipping_cost_with_estimate_usd": 0.0,
        "unallocated_ad_spend_usd": 0.0,
        "overview": {"line_count": 1, "total_profit_usd": 0.0},
        "summary": {"ok": {}, "incomplete": {}},
        "estimated": {"lines": 0},
    }
    summary = _build_order_profit_summary_from_status(status, order_count=1)
    assert summary["profit_with_estimate_margin_pct"] is None
```

- [ ] **Step 1.2: 跑测试确认失败**

```bash
cd /home/cjh/.paseo/worktrees/0ubtzq57/fearless-crab
python -m pytest tests/test_order_analytics_realtime_profit_margin.py -q
```

预期：7 个测试全部 FAIL，因为 `profit_with_estimate_margin_pct` key 不存在 / 默认 0.0。

- [ ] **Step 1.3: 修改 `_empty_order_profit_summary`**

在 `appcore/order_analytics/realtime.py` 第 172 行（`"profit_with_estimate_usd": 0.0,`）之后追加一行：

```python
        "profit_with_estimate_margin_pct": None,
```

最终 `_empty_order_profit_summary()` 末尾两行长这样：

```python
        "profit_with_estimate_usd": 0.0,
        "profit_with_estimate_margin_pct": None,
    }
```

- [ ] **Step 1.4: 修改 `_build_order_profit_summary` 在 rounding 循环之后追加百分比计算**

在 `appcore/order_analytics/realtime.py` 第 250-256 行（rounding 循环）之后、`return summary` 之前插入：

```python
    total_revenue = summary["total_revenue_usd"]
    if total_revenue > 0:
        summary["profit_with_estimate_margin_pct"] = round(
            summary["profit_with_estimate_usd"] / total_revenue * 100,
            2,
        )
    else:
        summary["profit_with_estimate_margin_pct"] = None
```

修改后的尾部 block 形如：

```python
    for key, value in list(summary.items()):
        if key.endswith("_count") or key == "order_count":
            summary[key] = int(value)
        elif key.endswith("_ratio"):
            summary[key] = round(float(value), 4)
        else:
            summary[key] = round(float(value), 2)
    total_revenue = summary["total_revenue_usd"]
    if total_revenue > 0:
        summary["profit_with_estimate_margin_pct"] = round(
            summary["profit_with_estimate_usd"] / total_revenue * 100,
            2,
        )
    else:
        summary["profit_with_estimate_margin_pct"] = None
    return summary
```

注意：上一步 rounding 循环里的 `else: summary[key] = round(float(value), 2)` 会把 `profit_with_estimate_margin_pct=None` 喂给 `float(None)` 报 `TypeError`。**必须**让赋值发生在 rounding 循环之后。

- [ ] **Step 1.5: 修改 `_build_order_profit_summary_from_status` 同款追加**

在 `appcore/order_analytics/realtime.py` 第 303-309 行（fallback 路径的 rounding 循环）之后、`return summary` 之前插入相同的 block：

```python
    total_revenue = summary["total_revenue_usd"]
    if total_revenue > 0:
        summary["profit_with_estimate_margin_pct"] = round(
            summary["profit_with_estimate_usd"] / total_revenue * 100,
            2,
        )
    else:
        summary["profit_with_estimate_margin_pct"] = None
    return summary
```

- [ ] **Step 1.6: 跑新测试确认通过**

```bash
python -m pytest tests/test_order_analytics_realtime_profit_margin.py -q
```

预期：7 passed。

- [ ] **Step 1.7: 跑既有 realtime 测试确认无回归**

```bash
python -m pytest \
  tests/test_order_analytics_realtime_profit_details.py \
  tests/test_order_analytics_realtime_site_filter.py \
  tests/test_order_analytics_responses_service.py \
  tests/test_order_analytics_dashboard.py \
  tests/characterization/test_order_analytics_baseline.py \
  -q
```

预期：全部 passed。如果 baseline 特征测试因为新增字段触发断言变化，根据测试报错信息更新 baseline 期望集合（通常 `>=` 包含关系不会破，等号 schema 才会破）。

- [ ] **Step 1.8: Commit**

```bash
git add tests/test_order_analytics_realtime_profit_margin.py appcore/order_analytics/realtime.py docs/superpowers/specs/2026-05-10-realtime-dashboard-profit-margin.md docs/superpowers/plans/2026-05-10-realtime-dashboard-profit-margin.md
git commit -m "$(cat <<'EOF'
feat(realtime): add profit_with_estimate_margin_pct to KPI summary

后端 _empty_order_profit_summary / _build_order_profit_summary /
_build_order_profit_summary_from_status 输出新字段
profit_with_estimate_margin_pct = profit_with_estimate_usd /
total_revenue_usd * 100；total_revenue_usd <= 0 时返回 None。

Docs-anchor: docs/superpowers/specs/2026-05-10-realtime-dashboard-profit-margin.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: 前端 KPI 卡片 markup 与 JS 渲染

**Files:**
- Modify: `web/templates/order_analytics.html`（KPI markup 第 1414-1418 行；`renderRealtimeOrderProfitSummary` 第 3732-3795 行）

- [ ] **Step 2.1: 在 KPI 卡片 markup 增加 margin sub 节点**

在 `web/templates/order_analytics.html` 第 1414-1418 行：

```html
          <div class="oar-profit-summary-item">
            <div class="oar-profit-summary-label">总利润额</div>
            <div class="oar-profit-summary-value" id="realtimeProfitTotal">$0.00</div>
            <div class="oar-profit-summary-note">含缺失成本估算 + 未分摊广告费</div>
          </div>
```

改为：

```html
          <div class="oar-profit-summary-item">
            <div class="oar-profit-summary-label">总利润额</div>
            <div class="oar-profit-summary-value" id="realtimeProfitTotal">$0.00</div>
            <div class="oar-profit-summary-note" id="realtimeProfitTotalMargin">利润率 -</div>
            <div class="oar-profit-summary-note">含缺失成本估算 + 未分摊广告费</div>
          </div>
```

利润率小字放在「含缺失成本估算...」说明之上：紧跟数字、视觉链路最短。复用既有 `oar-profit-summary-note` class，符合 Ocean Blue Admin 设计系统、不引入新 CSS。

- [ ] **Step 2.2: 修改 `renderRealtimeOrderProfitSummary` 末尾追加利润率渲染**

在 `web/templates/order_analytics.html` 第 3769-3773 行：

```javascript
    var profitEl = document.getElementById('realtimeProfitTotal');
    if (profitEl) {
      profitEl.classList.toggle('oar-profit-loss', profit < 0);
      profitEl.classList.toggle('oar-profit-ok', profit >= 0);
    }
```

之后立刻追加一段（仍在 `renderRealtimeOrderProfitSummary` 函数内、后续「对账提示」block 之前）：

```javascript
    var marginPct = s.profit_with_estimate_margin_pct;
    var marginText;
    var marginIsNumber = (typeof marginPct === 'number') && isFinite(marginPct);
    if (marginIsNumber) {
      marginText = '利润率 ' + Number(marginPct).toFixed(2) + '%';
    } else {
      marginText = '利润率 -';
    }
    setRealtimeProfitText('realtimeProfitTotalMargin', marginText);
    var marginEl = document.getElementById('realtimeProfitTotalMargin');
    if (marginEl) {
      marginEl.classList.toggle(
        'oar-profit-loss',
        marginIsNumber && marginPct < 0
      );
      marginEl.classList.toggle(
        'oar-profit-ok',
        marginIsNumber && marginPct >= 0
      );
    }
```

着色与「总利润额」数字保持同步：profit < 0 时利润率也红，profit ≥ 0 时利润率绿；缺数据时不挂任何 class、显示中性灰「利润率 -」。

- [ ] **Step 2.3: dev server 起在空闲端口端到端验证**

按 [CLAUDE.md「本机部署到线上的标准流程」第 1 步](../../../CLAUDE.md) 启 dev server：

```bash
cd /home/cjh/.paseo/worktrees/0ubtzq57/fearless-crab
PORT=5090 python -m web.app &
sleep 5
```

浏览器或 curl 访问 `http://127.0.0.1:5090/order-analytics?tab=realtime-overview`：

1. admin 登录（凭据见 [testuser.md](../../../testuser.md)）。
2. 访问实时大盘，确认「总利润额」KPI 下显示「利润率 XX.XX%」。
3. 用 devtools Network 面板抓 `/order-analytics/realtime-overview`：JSON `order_profit_summary.profit_with_estimate_margin_pct` 字段存在；与「利润 / 营收 × 100」目测对齐。
4. 切到 `?site_code=newjoy`，确认筛选后利润率随分店数据刷新。
5. 切到一个无订单的旧业务日，确认 KPI 显示「利润率 -」、不报错、不 NaN。

完成后 `kill %1` 关闭 dev server。

- [ ] **Step 2.4: Commit**

```bash
git add web/templates/order_analytics.html
git commit -m "$(cat <<'EOF'
feat(realtime): show profit margin under realtime KPI total profit card

KPI 卡新增 #realtimeProfitTotalMargin sub 节点；
renderRealtimeOrderProfitSummary 渲染「利润率 XX.XX%」并复用
oar-profit-loss / oar-profit-ok 配色与上方利润数字同步着色。

Docs-anchor: docs/superpowers/specs/2026-05-10-realtime-dashboard-profit-margin.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: CLAUDE.md 锚点 cross-reference

**Files:**
- Modify: `CLAUDE.md` 「## 实时大盘店铺筛选（2026-05-09 起）」章节末尾

- [ ] **Step 3.1: 在「实时大盘店铺筛选」章节最后一行 bullet 之后追加 cross-reference**

定位 `CLAUDE.md` 的「## 实时大盘店铺筛选（2026-05-09 起）」章节，在它的最后一项 bullet（关于 `pytest tests/test_order_analytics_realtime_site_filter.py ...` 那条）**之后**、章节结束分隔线（`---`）之前，追加一行：

```markdown
- KPI「总利润额」下方利润率字段 `profit_with_estimate_margin_pct` 见 [docs/superpowers/specs/2026-05-10-realtime-dashboard-profit-margin.md](docs/superpowers/specs/2026-05-10-realtime-dashboard-profit-margin.md)。改 KPI 卡 markup / `renderRealtimeOrderProfitSummary` / `_build_order_profit_summary*` 这条链路时同步看该 spec。
```

- [ ] **Step 3.2: Commit**

```bash
git add CLAUDE.md
git commit -m "$(cat <<'EOF'
docs(claude-md): anchor realtime profit margin spec under store filter section

Docs-anchor: docs/superpowers/specs/2026-05-10-realtime-dashboard-profit-margin.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: 全量回归 + push 到 GitHub master

**Files:** 无新改动；只跑测试与发布。

- [ ] **Step 4.1: 跑 spec 列出的完整测试集**

```bash
cd /home/cjh/.paseo/worktrees/0ubtzq57/fearless-crab
python -m pytest \
  tests/test_order_analytics_realtime_profit_details.py \
  tests/test_order_analytics_realtime_site_filter.py \
  tests/test_order_analytics_responses_service.py \
  tests/test_order_analytics_dashboard.py \
  tests/characterization/test_order_analytics_baseline.py \
  tests/test_order_analytics_realtime_profit_margin.py \
  -q
```

预期：全部 passed。任一 fail 必须停下定位，不能跳过。

- [ ] **Step 4.2: rebase 到最新 master**

```bash
git fetch origin master
git rebase origin/master
```

如果有冲突，手工解决再 `git rebase --continue`。

- [ ] **Step 4.3: push 到 GitHub master**

```bash
git push origin HEAD:master
```

若报 `non-fast-forward`：再次 `git fetch origin master && git rebase origin/master`，然后重试 push。

- [ ] **Step 4.4: 发布前等用户确认**

**停下，不要直接发布到线上**。按 CLAUDE.md「未经许可不得 restart 服务」硬规则：等用户明确说「发布线上」/「发测试」再继续。可以告知用户：「代码已 push 到 GitHub master，如需发布线上 / 测试，请明确指令。」

---

## Self-Review

- ✅ Spec 覆盖：Task 1 ↔ spec「字段定义」+「后端实现要点」；Task 2 ↔ spec「前端实现要点」+「端到端验证」；Task 3 ↔ spec「文档锚点更新」；Task 4 ↔ spec「修改顺序」第 5-7 步。
- ✅ Placeholder 扫描：无 TBD / TODO / 「fill in details」；每一步代码块均给完整可粘贴片段。
- ✅ 类型一致：后端字段名 `profit_with_estimate_margin_pct` 在 spec / 测试 / 主路径 / fallback 路径 / 前端 JS / KPI markup id (`realtimeProfitTotalMargin`) 全程一致。
- ✅ 任务粒度：每个 step 2-5 分钟可完成，TDD 红→绿→commit 闭环；端到端验证有明确 URL 与凭据来源。
