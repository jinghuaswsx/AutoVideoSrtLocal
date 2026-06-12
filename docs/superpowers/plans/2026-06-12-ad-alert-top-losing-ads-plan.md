# 广告预警卡片级亏损 AD 预览 — Implementation Plan

**Goal:** Show the worst-performing ads directly on each alert card, with a per-card AI evaluate button.

**Architecture:** Add `top_losing_ads` to `AlertItem` model, backend populates it in `get_alerts()`, frontend renders on each card.

---

### Task 1: 后端数据模型和查询

**Files:**
- Modify: `appcore/ad_alerts.py`

- [ ] **Step 1: AlertItem 新增 top_losing_ads 字段**

找到 `AlertItem` dataclass（约第 79 行），在 `active_days: int = 0` 之后添加：

```python
    top_losing_ads: list[AdListItem] = field(default_factory=list)
```

- [ ] **Step 2: 新增 _get_top_losing_ads 辅助函数**

在 `get_alerts()` 函数之前（或任意位置，但放在一起方便阅读），添加：

```python
def _get_top_losing_ads(
    product_id: int,
    lang: str,
    threshold: float,
    limit: int = 3,
) -> list[AdListItem]:
    """获取某商品语言下亏损最严重的几条 AD（按 ROAS 升序）。"""
    all_ads = get_ad_list(product_id, lang)
    losing = [
        ad for ad in all_ads
        if ad.ad_roas is not None and ad.ad_roas < threshold
    ]
    losing.sort(key=lambda a: (a.ad_roas or 999))
    return losing[:limit]
```

- [ ] **Step 3: 在 get_alerts() 中填充 top_losing_ads**

找到 `get_alerts()` 中构建 `AlertItem` 的地方（约第 249-271 行），在创建 `AlertItem` 时添加 `top_losing_ads` 参数：

```python
        losing_ads = _get_top_losing_ads(
            product_id, item_lang, threshold_value, limit=3,
        )
        items.append(
            AlertItem(
                product_id=product_id,
                ...
                active_days=active_window.active_days,
                top_losing_ads=losing_ads,
            )
        )
```

- [ ] **Step 4: 提交**

```bash
git add appcore/ad_alerts.py
git commit -m "feat: attach top losing ads to each alert item

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: 路由序列化

**Files:**
- Modify: `web/routes/ad_alerts.py`

- [ ] **Step 1: _alert_item_to_dict 新增 top_losing_ads 序列化**

找到 `_alert_item_to_dict()` 函数（约第 118 行），在 `"computed_at"` 字段之后、return 之前添加：

```python
        "top_losing_ads": [
            _ad_list_item_to_dict(ad) for ad in item.top_losing_ads
        ],
```

- [ ] **Step 2: 提交**

```bash
git add web/routes/ad_alerts.py
git commit -m "feat: serialize top_losing_ads in alert item response

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: 前端卡片渲染 + AI 评估按钮

**Files:**
- Modify: `web/templates/ad_alerts.html`

- [ ] **Step 1: 修改 renderList()，在卡片上渲染亏损 AD 预览**

找到 `renderList` 函数，在 `item.estimated_loss` 渲染之后、闭合 `</button>` 之前插入：

```javascript
// --- 亏损 AD 预览行 ---
if (item.top_losing_ads && item.top_losing_ads.length > 0) {
  html += '<div class="oc-ad-alert-losing-ads">';
  item.top_losing_ads.forEach(function(ad) {
    var adRoas = ad.ad_roas === null || ad.ad_roas === undefined ? null : Number(ad.ad_roas);
    var lossAmount = (ad.total_purchase || 0) - (ad.total_spend || 0);
    var icon = (adRoas !== null && adRoas < 1.0) ? '🔴' : '🟠';
    html += '<div class="oc-ad-alert-losing-ad">'
      + icon + ' <strong>' + html(ad.country || '') + '</strong> · '
      + html(ad.ad_name || ad.normalized_ad_code || '') + ' · ROAS <strong class="oc-ad-alert-roas-' + (adRoas !== null && adRoas < 1.0 ? 'bad' : 'warn') + '">' + roas(adRoas) + '</strong>'
      + ' · 亏 <strong class="oc-ad-alert-loss">' + money(Math.abs(lossAmount), 0) + '</strong>'
      + '</div>';
  });
  html += '</div>';
}

// --- AI 评估按钮（卡片级） ---
html += '<div class="oc-ad-alert-card-actions">'
  + '<button type="button" class="oc-ad-alert-btn oc-ad-alert-btn-ai" onclick="event.stopPropagation();runCardEvaluation(\'' + html(item.product_id) + '\',\'' + html(item.lang) + '\',' + Number(state.threshold || 0) + ',this)" data-product-id="' + html(item.product_id) + '" data-lang="' + html(item.lang) + '">🤖 AI 评估</button>'
  + '</div>';
```

- [ ] **Step 2: 添加 runCardEvaluation() 函数和评估结果浮层**

在已有 script 中添加（可放在 `runAdEvaluation` 函数前后）：

```javascript
function runCardEvaluation(productId, lang, threshold, btn) {
  var card = btn.closest('.oc-ad-alert-card');
  var existingResult = card.querySelector('.oc-ad-alert-card-eval');
  if (existingResult) {
    existingResult.remove();
    return;
  }
  var resultEl = document.createElement('div');
  resultEl.className = 'oc-ad-alert-card-eval';
  resultEl.innerHTML = '<div class="oc-ad-alert-state" style="padding:8px;">分析中...</div>';
  btn.parentNode.appendChild(resultEl);

  fetch('/ad-alerts/api/evaluate', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Accept': 'application/json',
      'X-CSRFToken': csrfToken()
    },
    body: JSON.stringify({
      product_id: Number(productId),
      lang: String(lang || '').toLowerCase(),
      threshold: Number(threshold || 0)
    })
  })
    .then(function(resp) {
      return resp.json().then(function(data) {
        if (!resp.ok) throw new Error(data.error || 'evaluate_failed');
        return data;
      });
    })
    .then(function(data) {
      var evaluations = data.evaluations || [];
      if (!evaluations.length) {
        resultEl.innerHTML = '<div class="oc-ad-alert-state" style="padding:8px;">暂无亏损广告需评估</div>';
        return;
      }
      var htmlParts = ['<div class="oc-ad-alert-evaluation" style="font-size:12px;">'];
      var groups = {'关停':[], '优化':[], '观察':[]};
      evaluations.forEach(function(ev) {
        var key = groups[ev.judgment] ? ev.judgment : '观察';
        groups[key].push(ev);
      });
      [
        {key:'关停',color:'var(--oc-ad-alert-red)'},
        {key:'优化',color:'var(--oc-ad-alert-orange)'},
        {key:'观察',color:'var(--oc-ad-alert-muted)'}
      ].forEach(function(grp) {
        var items = groups[grp.key] || [];
        if (!items.length) return;
        htmlParts.push('<div style="margin-bottom:6px;"><strong style="color:' + grp.color + ';font-size:12px;">' + grp.key + '</strong>');
        items.forEach(function(item) {
          htmlParts.push('<div style="padding:2px 0 2px 8px;font-size:12px;"><strong>' + html(item.country) + '</strong> · ' + html(item.ad_name) + ' · ROAS ' + roas(item.roas) + '：' + html(item.reason) + '</div>');
        });
        htmlParts.push('</div>');
      });
      htmlParts.push('</div>');
      resultEl.innerHTML = htmlParts.join('');
    })
    .catch(function(error) {
      resultEl.innerHTML = '<div class="oc-ad-alert-state" style="padding:8px;color:var(--oc-ad-alert-red);">评估失败</div>';
    });
}
```

- [ ] **Step 3: 添加 CSS 样式（在 `<style>` 块中追加）**

```css
.oc-ad-alert-losing-ads {
  margin: 8px 12px 0;
  padding: 8px 10px;
  background: var(--oc-ad-alert-subtle);
  border-radius: 6px;
  font-size: 12px;
  line-height: 1.6;
}
.oc-ad-alert-losing-ad {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-wrap: wrap;
}
.oc-ad-alert-roas-bad { color: var(--oc-ad-alert-red); }
.oc-ad-alert-roas-warn { color: var(--oc-ad-alert-orange); }
.oc-ad-alert-loss { color: var(--oc-ad-alert-red); }
.oc-ad-alert-card-actions {
  margin: 6px 12px 0;
  display: flex;
  gap: 8px;
}
.oc-ad-alert-btn-ai {
  font-size: 12px;
  min-height: 28px;
  padding: 0 10px;
  border: 1px solid var(--oc-ad-alert-blue);
  border-radius: 6px;
  background: transparent;
  color: var(--oc-ad-alert-blue);
  cursor: pointer;
}
.oc-ad-alert-btn-ai:hover {
  background: #eff6ff;
}
.oc-ad-alert-card-eval {
  margin: 6px 12px 0;
  padding: 8px 10px;
  background: var(--oc-ad-alert-subtle);
  border-radius: 6px;
  font-size: 12px;
}
```

- [ ] **Step 4: 提交**

```bash
git add web/templates/ad_alerts.html
git commit -m "feat: show top losing ads on alert cards + per-card AI evaluate button

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### 验证

1. ✅ 打开 `/ad-alerts`，每张卡片上显示该语言下最亏的 2-3 条 AD
2. ✅ 亏损 AD 行显示：图标 + 国家 + AD名 + ROAS + 亏损金额
3. ✅ ROAS < 1.0 红色，1.0-1.3 橙色
4. ✅ 点击「AI 评估」直接在卡片下展开评估结果
5. ✅ 再次点击隐藏结果
6. ✅ 详情弹窗内的 AD 列表 + AI 评估不受影响

## Spec 对照检查

| 要求 | Task |
|------|------|
| `AlertItem` 新增 `top_losing_ads` | Task 1 |
| `get_alerts()` 填充亏损 AD | Task 1 |
| 序列化新字段 | Task 2 |
| 卡片上渲染亏损 AD 预览 | Task 3 CSS + JS |
| 每卡「AI 评估」按钮 | Task 3 JS |
| 卡片级评估结果浮层 | Task 3 JS |
