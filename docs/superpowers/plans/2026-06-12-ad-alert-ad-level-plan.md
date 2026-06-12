# 广告预警 AD 级详情 + Gemini 评估 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add product×country×ad level drill-down to the existing ad alert detail modal, with Gemini 3.5 Flash evaluation for per-ad stop-loss recommendations.

**Architecture:** Extend `appcore/ad_alerts.py` with AD-level aggregation queries and Gemini evaluation. Two new API endpoints on existing blueprint. Same detail modal gets a new section. No new pages, no new tables.

**Tech Stack:** Python 3 / Flask / MySQL / Jinja2 / OpenRouter (google/gemini-3.5-flash) / Ocean Blue CSS

---

### Task 1: 后端数据模型和 AD 列表查询

**Files:**
- Modify: `appcore/ad_alerts.py`

- [ ] **Step 1: 在 appcore/ad_alerts.py 末尾新增 AdListItem 和 AdEvaluation 数据模型**

```python
@dataclass
class AdListItem:
    """单个 AD 级别的投放数据。"""
    country: str
    ad_name: str
    normalized_ad_code: str
    total_spend: float
    total_purchase: float
    ad_roas: float | None
    active_days: int


@dataclass
class AdEvaluation:
    """Gemini 对单个 AD 的评估结论。"""
    country: str
    ad_name: str
    roas: float
    judgment: str  # 关停 / 优化 / 观察
    reason: str
```

- [ ] **Step 2: 实现 get_ad_list() 函数**

```python
_COUNTRY_LABELS: dict[str, str] = {
    "US": "美国", "GB": "英国", "UK": "英国", "AU": "澳大利亚",
    "CA": "加拿大", "IE": "爱尔兰", "NZ": "新西兰",
    "DE": "德国", "AT": "奥地利",
    "FR": "法国",
    "ES": "西班牙",
    "IT": "意大利",
    "NL": "荷兰",
    "SE": "瑞典", "FI": "芬兰",
    "JP": "日本",
    "KR": "韩国",
    "BR": "巴西", "PT": "葡萄牙",
}


def get_ad_list(product_id: int, lang: str) -> list[AdListItem]:
    """查询某个商品语言下每条 AD 的聚合数据。

    从 meta_ad_daily_ad_metrics 按 market_country + ad_name 聚合。
    """
    lower_lang = lang.strip().lower()
    rows = query(
        """
        SELECT
          COALESCE(m.market_country, '??') AS country,
          m.ad_name,
          m.normalized_ad_code,
          COALESCE(SUM(COALESCE(m.spend_usd, 0)), 0) AS total_spend,
          COALESCE(SUM(COALESCE(m.purchase_value_usd, 0)), 0) AS total_purchase,
          COUNT(DISTINCT COALESCE(m.meta_business_date, m.report_date)) AS active_days
        FROM meta_ad_daily_ad_metrics m
        JOIN media_items i
          ON i.product_id = m.product_id
         AND i.deleted_at IS NULL
         AND LOWER(i.lang) = %(lang)s
         AND (
           m.ad_name LIKE CONCAT('%%', i.filename, '%%')
           OR m.normalized_ad_code LIKE CONCAT('%%', i.filename, '%%')
           OR (i.display_name IS NOT NULL AND i.display_name <> '' AND m.ad_name LIKE CONCAT('%%', i.display_name, '%%'))
           OR (i.display_name IS NOT NULL AND i.display_name <> '' AND m.normalized_ad_code LIKE CONCAT('%%', i.display_name, '%%'))
           OR (
             m.market_country IS NOT NULL
             AND m.market_country <> ''
             AND LOWER(i.lang) = {country_lang_case_sql}
           )
         )
        WHERE m.product_id = %(product_id)s
          AND COALESCE(m.spend_usd, 0) > 0
          AND m.market_country IS NOT NULL
          AND m.market_country <> ''
        GROUP BY m.market_country, m.ad_name, m.normalized_ad_code
        ORDER BY COALESCE(
          CASE WHEN SUM(COALESCE(m.spend_usd, 0)) > 0
            THEN SUM(COALESCE(m.purchase_value_usd, 0)) / SUM(COALESCE(m.spend_usd, 0))
          END, 999
        ) ASC
        """,
        {"product_id": product_id, "lang": lower_lang,
         "country_lang_case_sql": _COUNTRY_LANG_CASE_SQL % "m.market_country"},
    )
    
    # Oops - the SQL has a problem with the CASE SQL being a string parameter.
    # Let me fix the approach - use FORMAT instead
    items: list[AdListItem] = []
    for row in rows:
        spend = _safe_float(row.get("total_spend"))
        purchase = _safe_float(row.get("total_purchase"))
        roas = round(purchase / spend, 4) if spend > 0.01 else None
        items.append(AdListItem(
            country=_safe_str(row.get("country")),
            ad_name=_safe_str(row.get("ad_name")),
            normalized_ad_code=_safe_str(row.get("normalized_ad_code")),
            total_spend=round(spend, 2),
            total_purchase=round(purchase, 2),
            ad_roas=roas,
            active_days=int(_safe_float(row.get("active_days"))),
        ))
    return items
```

Wait — the `_COUNTRY_LANG_CASE_SQL` is a raw SQL fragment, it can't be passed as a parameter. Let me fix this by building the SQL properly.

- [ ] **Step 2 (corrected): 实现 get_ad_list() 函数**

```python
def get_ad_list(product_id: int, lang: str) -> list[AdListItem]:
    """查询某个商品语言下每条 AD 的聚合数据。

    从 meta_ad_daily_ad_metrics 按 market_country + ad_name 聚合。
    """
    lower_lang = lang.strip().lower()
    # 构建语言匹配条件 SQL
    lang_match_sql = _COUNTRY_LANG_CASE_SQL % "m.market_country"
    rows = query(
        f"""
        SELECT
          COALESCE(m.market_country, '??') AS country,
          m.ad_name,
          m.normalized_ad_code,
          COALESCE(SUM(COALESCE(m.spend_usd, 0)), 0) AS total_spend,
          COALESCE(SUM(COALESCE(m.purchase_value_usd, 0)), 0) AS total_purchase,
          COUNT(DISTINCT COALESCE(m.meta_business_date, m.report_date)) AS active_days
        FROM meta_ad_daily_ad_metrics m
        JOIN media_items i
          ON i.product_id = m.product_id
         AND i.deleted_at IS NULL
         AND LOWER(i.lang) = %(lang)s
         AND (
           m.ad_name LIKE CONCAT('%%', i.filename, '%%')
           OR m.normalized_ad_code LIKE CONCAT('%%', i.filename, '%%')
           OR (i.display_name IS NOT NULL AND i.display_name <> '' AND m.ad_name LIKE CONCAT('%%', i.display_name, '%%'))
           OR (i.display_name IS NOT NULL AND i.display_name <> '' AND m.normalized_ad_code LIKE CONCAT('%%', i.display_name, '%%'))
           OR (
             m.market_country IS NOT NULL
             AND m.market_country <> ''
             AND LOWER(i.lang) = {lang_match_sql}
           )
         )
        WHERE m.product_id = %(product_id)s
          AND COALESCE(m.spend_usd, 0) > 0
          AND m.market_country IS NOT NULL
          AND m.market_country <> ''
        GROUP BY m.market_country, m.ad_name, m.normalized_ad_code
        ORDER BY COALESCE(
          CASE WHEN SUM(COALESCE(m.spend_usd, 0)) > 0
            THEN SUM(COALESCE(m.purchase_value_usd, 0)) / SUM(COALESCE(m.spend_usd, 0))
          END, 999
        ) ASC
        """,
        {"product_id": product_id, "lang": lower_lang},
    )

    items: list[AdListItem] = []
    for row in rows:
        spend = _safe_float(row.get("total_spend"))
        purchase = _safe_float(row.get("total_purchase"))
        roas = round(purchase / spend, 4) if spend > 0.01 else None
        items.append(AdListItem(
            country=_safe_str(row.get("country")),
            ad_name=_safe_str(row.get("ad_name")),
            normalized_ad_code=_safe_str(row.get("normalized_ad_code")),
            total_spend=round(spend, 2),
            total_purchase=round(purchase, 2),
            ad_roas=roas,
            active_days=int(_safe_float(row.get("active_days"))),
        ))
    return items
```

- [ ] **Step 3: 注册 LLM Use Case 并实现 evaluate_ads()**

在 `appcore/llm_use_cases.py` 的 USE_CASES dict 末尾添加：

```python
    "ad_alert.evaluate": _uc(
        "ad_alert.evaluate",
        "ad_alerts",
        "广告预警 AI 评估",
        "Gemini 3.5 Flash 评估亏损广告投放数据，给出关停/优化/观察建议",
        "openrouter",
        "google/gemini-3.5-flash",
        "openrouter",
        "tokens",
    ),
```

在 `appcore/ad_alerts.py` 末尾添加：

```python
import json as json_module


def evaluate_ads(
    product_id: int,
    lang: str,
    threshold: float | None = None,
    user_id: int | None = None,
) -> list[AdEvaluation] | None:
    """调用 Gemini 3.5 Flash 评估某商品语言下亏损 AD 列表。

    :returns: AdEvaluation 列表，或 None（调用失败时）
    """
    if threshold is None:
        threshold = get_threshold()

    # 获取该语言下的 AD 列表
    ad_list = get_ad_list(product_id, lang)
    if not ad_list:
        return []

    # 只送亏损的 AD 给 LLM（ROAS < threshold）
    losing_ads = [ad for ad in ad_list if ad.ad_roas is not None and ad.ad_roas < threshold]
    if not losing_ads:
        return []

    # 获取商品信息
    product_row = query_one(
        "SELECT product_code, name FROM media_products WHERE id = %(product_id)s AND deleted_at IS NULL",
        {"product_id": product_id},
    )
    product_code = _safe_str(product_row.get("product_code")) if product_row else str(product_id)
    product_name = _safe_str(product_row.get("name")) if product_row else product_code
    lang_label = _LANG_LABELS.get(lang.strip().lower(), lang)

    # 构造广告数据文本
    ad_lines = []
    for ad in losing_ads:
        ad_lines.append(
            f"- 国家: {ad.country} | AD名称: {ad.ad_name} | "
            f"花费: ${ad.total_spend:.2f} | 购买价值: ${ad.total_purchase:.2f} | "
            f"ROAS: {ad.ad_roas:.2f} | 活跃天数: {ad.active_days}"
        )
    ad_list_text = "\n".join(ad_lines)

    from appcore import llm_client

    messages = [
        {
            "role": "system",
            "content": (
                "你是一个 Meta 广告优化分析师。你的任务是根据广告投放数据"
                "分析一组广告的表现，给出每条广告的关停建议。"
                "重点关注亏损（ROAS < 保本线）但仍持续投放的广告。\n\n"
                "输出格式：纯 JSON 数组，不要 markdown 包裹，不要其他说明文字。"
                "每个元素包含:\n"
                '- "country": 国家代码\n'
                '- "ad_name": 广告名\n'
                '- "roas": ROAS 数值（数字）\n'
                '- "judgment": 建议，只能是"关停"、"优化"或"观察"之一\n'
                '- "reason": 简短理由（中文字符）'
            ),
        },
        {
            "role": "user",
            "content": (
                f"以下是商品「{product_name}」(编码: {product_code}) 在 "
                f"{lang_label} 语言下的广告投放数据，"
                f"保本 ROAS 为 {threshold}。请分析并给出建议：\n\n"
                f"广告列表：\n{ad_list_text}"
            ),
        },
    ]

    try:
        result = llm_client.invoke_chat(
            "ad_alert.evaluate",
            messages=messages,
            user_id=user_id,
            temperature=0.1,
            response_format={"type": "json_object"},
        )
    except Exception:
        log.warning("ad_alert.evaluate LLM call failed", exc_info=True)
        return None

    raw_text = result.get("json") or result.get("text", "")
    if not raw_text:
        return None

    # 解析 JSON（兼容可能被 markdown 包裹的情况）
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        # 去掉 ```json ... ``` 包裹
        first_newline = cleaned.find("\n")
        if first_newline != -1:
            cleaned = cleaned[first_newline:]
        cleaned = cleaned.strip()
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3].strip()

    try:
        parsed = json_module.loads(cleaned)
    except json_module.JSONDecodeError:
        log.warning("ad_alert.evaluate: failed to parse Gemini response as JSON")
        return None

    if not isinstance(parsed, list):
        parsed = [parsed]

    evaluations: list[AdEvaluation] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        judgment = str(item.get("judgment", "")).strip()
        if judgment not in ("关停", "优化", "观察"):
            judgment = "观察"
        evaluations.append(AdEvaluation(
            country=str(item.get("country", "")),
            ad_name=str(item.get("ad_name", "")),
            roas=float(item.get("roas", 0)),
            judgment=judgment,
            reason=str(item.get("reason", "")),
        ))
    return evaluations
```

- [ ] **Step 4: 提交**

```bash
git add appcore/ad_alerts.py appcore/llm_use_cases.py
git commit -m "feat: add AD-level alert data + Gemini evaluation

- New dataclasses: AdListItem, AdEvaluation
- get_ad_list(): aggregate per-country, per-ad from daily_ad_metrics
- evaluate_ads(): Gemini 3.5 Flash evaluation via LLM use case
- Register ad_alert.evaluate use case

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: 路由新增

**Files:**
- Modify: `web/routes/ad_alerts.py`

- [ ] **Step 1: 在 api_detail 路由之后新增两个路由**

在 `web/routes/ad_alerts.py` 的 `api_set_threshold` 之前添加：

```python
@bp.route("/api/ad-list")
@login_required
@admin_required
def api_ad_list():
    """获取某商品语言下每条 AD 的投放数据列表。"""
    try:
        product_id = int(request.args.get("product_id") or 0)
    except (TypeError, ValueError):
        return jsonify({"error": "invalid product_id"}), 400
    if product_id <= 0:
        return jsonify({"error": "invalid product_id"}), 400

    lang = (request.args.get("lang") or "").strip().lower()
    if not lang:
        return jsonify({"error": "lang required"}), 400

    ads = ad_alerts.get_ad_list(product_id, lang)
    return jsonify({
        "ads": [
            {
                "country": ad.country,
                "ad_name": ad.ad_name,
                "normalized_ad_code": ad.normalized_ad_code,
                "total_spend": ad.total_spend,
                "total_purchase": ad.total_purchase,
                "ad_roas": ad.ad_roas,
                "active_days": ad.active_days,
            }
            for ad in ads
        ],
        "total": len(ads),
    })


@bp.route("/api/evaluate", methods=["POST"])
@login_required
@admin_required
def api_evaluate():
    """调用 Gemini 评估某商品语言下亏损 AD。"""
    body = request.get_json(silent=True) or {}
    try:
        product_id = int(body.get("product_id") or 0)
    except (TypeError, ValueError):
        return jsonify({"error": "invalid product_id"}), 400
    if product_id <= 0:
        return jsonify({"error": "invalid product_id"}), 400

    lang = (body.get("lang") or "").strip().lower()
    if not lang:
        return jsonify({"error": "lang required"}), 400

    user_id = getattr(request, "user_id", None)
    if user_id is None:
        from flask_login import current_user
        user_id = getattr(current_user, "id", None)

    evaluations = ad_alerts.evaluate_ads(
        product_id, lang, user_id=user_id,
    )
    if evaluations is None:
        return jsonify({"error": "evaluation failed"}), 500

    return jsonify({
        "evaluations": [
            {
                "country": ev.country,
                "ad_name": ev.ad_name,
                "roas": ev.roas,
                "judgment": ev.judgment,
                "reason": ev.reason,
            }
            for ev in evaluations
        ],
        "total": len(evaluations),
    })
```

- [ ] **Step 2: 提交**

```bash
git add web/routes/ad_alerts.py
git commit -m "feat: add ad-list and evaluate API endpoints

- GET /api/ad-list returns per-country, per-ad aggregated data
- POST /api/evaluate calls Gemini to evaluate losing ads

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: 前端详情弹窗扩展

**Files:**
- Modify: `web/templates/ad_alerts.html`

- [ ] **Step 1: 在详情弹窗的累计数据表下方、研判结论上方插入 AD 列表和 AI 评估区域**

找到 `renderDetail` 函数中研判结论部分的上方，在累计数据表 `</table>` 之后添加 AD 列表的 HTML 和 JS 逻辑。

```javascript
// -----------------------------------------------------------------------
// 在 renderDetail 函数中，累计数据表之后、研判结论之前添加：
// -----------------------------------------------------------------------

// AD 列表区域
html += '<div style="margin-bottom:16px;">';
html += '  <h4 style="font-size:14px;font-weight:600;margin-bottom:8px;display:flex;align-items:center;gap:8px;">';
html += '    📋 该语言下的广告投放列表 <span id="adListCount" style="font-weight:400;font-size:12px;color:var(--oc-fg-muted);"></span>';
html += '  </h4>';
html += '  <div id="adListContainer" style="overflow-x:auto;">';
html += '    <div style="padding:12px;text-align:center;color:var(--oc-fg-muted);font-size:13px;">加载 AD 数据...</div>';
html += '  </div>';
html += '  <div style="margin-top:10px;text-align:right;">';
html += '    <button type="button" class="oc-btn primary" id="aiEvaluateBtn" style="font-size:13px;" onclick="runAdEvaluation(' + detail.product_id + ')">🤖 AI 评估</button>';
html += '  </div>';
html += '</div>';

// AI 评估结果容器
html += '<div id="aiEvaluationContainer" style="margin-bottom:16px;display:none;"></div>';

// 替换掉旧的 "html += '</div>'; // 关闭 oc-form" 并添加新的 close
```

然后新增 JS 函数到模板的 `<script>` 标签中：

```javascript
// --- AD 列表加载 ---
function loadAdList(productId, lang) {
  var container = document.getElementById('adListContainer');
  var countEl = document.getElementById('adListCount');
  if (!container) return;

  fetch('/ad-alerts/api/ad-list?product_id=' + productId + '&lang=' + encodeURIComponent(lang))
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (!data.ads || data.ads.length === 0) {
        container.innerHTML = '<div style="padding:12px;text-align:center;color:var(--oc-fg-muted);font-size:13px;">暂无 AD 级数据</div>';
        if (countEl) countEl.textContent = '';
        document.getElementById('aiEvaluateBtn').disabled = true;
        return;
      }
      if (countEl) countEl.textContent = '（共 ' + data.total + ' 条广告）';
      document.getElementById('aiEvaluateBtn').disabled = false;

      var html = '<table class="oc-table oc-table-compact" style="font-size:13px;">';
      html += '<thead><tr>';
      html += '  <th>国家</th><th>AD 名称</th><th style="text-align:right;">花费</th>';
      html += '  <th style="text-align:right;">购买</th><th style="text-align:right;">ROAS</th><th style="text-align:right;">活跃天数</th>';
      html += '</tr></thead><tbody>';
      data.ads.forEach(function(ad) {
        var roasColor = ad.ad_roas !== null && ad.ad_roas < 1.0 ? 'var(--oc-danger)' :
                        (ad.ad_roas !== null && ad.ad_roas < 1.5 ? '#ea580c' : 'var(--oc-success)');
        html += '<tr>';
        html += '  <td><strong>' + escHtml(ad.country) + '</strong></td>';
        html += '  <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="' + escHtml(ad.ad_name) + '">' + escHtml(ad.ad_name) + '</td>';
        html += '  <td style="text-align:right;">$' + ad.total_spend.toFixed(0) + '</td>';
        html += '  <td style="text-align:right;">$' + ad.total_purchase.toFixed(0) + '</td>';
        html += '  <td style="text-align:right;font-weight:600;color:' + roasColor + ';">' + (ad.ad_roas !== null ? ad.ad_roas.toFixed(2) : 'N/A') + '</td>';
        html += '  <td style="text-align:right;">' + ad.active_days + '天</td>';
        html += '</tr>';
      });
      html += '</tbody></table>';
      container.innerHTML = html;
    })
    .catch(function() {
      container.innerHTML = '<div style="padding:12px;text-align:center;color:var(--oc-danger);font-size:13px;">加载 AD 数据失败</div>';
    });
}

// --- AI 评估 ---
window.runAdEvaluation = function(productId) {
  var btn = document.getElementById('aiEvaluateBtn');
  var container = document.getElementById('aiEvaluationContainer');
  if (!btn || !container) return;

  btn.disabled = true;
  btn.textContent = '⏳ 正在分析...';
  container.style.display = 'block';
  container.innerHTML = '<div style="padding:12px;text-align:center;color:var(--oc-fg-muted);">正在调用 Gemini 3.5 Flash 分析广告数据，请稍候...</div>';

  var lang = document.querySelector('#detailModalBody .oc-roas-head-actions .oc-badge');
  var langCode = lang ? lang.textContent.trim().toLowerCase() : '';
  // fallback: get lang from detail data
  if (!langCode) langCode = 'en';

  fetch('/ad-alerts/api/evaluate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ product_id: productId, lang: langCode }),
  })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (data.error || !data.evaluations) {
        container.innerHTML = '<div style="padding:12px;color:var(--oc-danger);">评估失败：' + (data.error || '未知错误') + '</div>';
        return;
      }
      renderEvaluations(data.evaluations, container);
    })
    .catch(function() {
      container.innerHTML = '<div style="padding:12px;color:var(--oc-danger);">网络错误，评估失败</div>';
    })
    .finally(function() {
      btn.disabled = false;
      btn.textContent = '🤖 AI 评估';
    });
};

function renderEvaluations(evaluations, container) {
  if (!evaluations || evaluations.length === 0) {
    container.innerHTML = '<div style="padding:12px;text-align:center;color:var(--oc-fg-muted);">暂无需要评估的亏损广告</div>';
    return;
  }

  var html = '<div style="background:var(--oc-bg-subtle);border-radius:8px;padding:12px;">';
  html += '  <div style="font-size:13px;font-weight:600;margin-bottom:10px;">🤖 Gemini 3.5 Flash 评估结果</div>';

  // Group by judgment
  var groups = { '关停': [], '优化': [], '观察': [] };
  evaluations.forEach(function(ev) {
    var g = groups[ev.judgment] || groups['观察'];
    g.push(ev);
  });

  var icons = { '关停': '🚫', '优化': '⚠️', '观察': '👁' };
  var colors = { '关停': 'var(--oc-danger)', '优化': '#ea580c', '观察': 'var(--oc-fg-muted)' };

  ['关停', '优化', '观察'].forEach(function(label) {
    var items = groups[label] || [];
    if (items.length === 0) return;
    html += '  <div style="margin-bottom:8px;">';
    html += '    <div style="font-weight:600;font-size:13px;color:' + (colors[label] || 'var(--oc-fg)') + ';margin-bottom:4px;">' + (icons[label] || '') + ' 建议' + label + '</div>';
    items.forEach(function(ev) {
      html += '    <div style="font-size:12px;padding:4px 0 4px 16px;color:var(--oc-fg-muted);">';
      html += '      <strong>' + escHtml(ev.country) + '</strong> · ' + escHtml(ev.ad_name) + ' — ' + escHtml(ev.reason);
      html += '    </div>';
    });
    html += '  </div>';
  });

  html += '</div>';
  container.innerHTML = html;
}

// --- 修改 openDetail 函数，在加载详情后加载 AD 列表 ---
// 找到 window.openDetail 函数，在 fetch('/ad-alerts/api/detail?...') 的 .then 中，
// 在 renderDetail 调用之后添加：
//    loadAdList(productId, lang);

// 具体修改方式：找到：
//   .then(function(data) { renderDetail(data.detail); })
// 改为：
//   .then(function(data) { renderDetail(data.detail); loadAdList(productId, lang); })
```

- [ ] **Step 2: 提交**

```bash
git add web/templates/ad_alerts.html
git commit -m "feat: add AD-level data table and Gemini evaluation to alert detail modal

- loadAdList(): fetch per-country, per-ad data into detail modal
- runAdEvaluation(): call Gemini 3.5 Flash, display grouped results
- Render evaluations grouped by 关停/优化/观察

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### 验证

1. 打开 `/ad-alerts`，点击任意预警卡片进入详情弹窗
2. ✅ 详情弹窗底部出现「该语言下的广告投放列表」表格
3. ✅ 表格列：国家、AD 名称、花费、购买、ROAS、活跃天数
4. ✅ 点击「AI 评估」按钮
5. ✅ 显示加载状态
6. ✅ 成功后按「关停/优化/观察」分组展示结果
7. ✅ 如 Gemini 调用失败，显示错误信息

## Spec 对照检查

| Spec 要求 | Task 覆盖 |
|-----------|-----------|
| AD 级数据聚合（product×country×ad） | Task 1 (get_ad_list) |
| Gemini 3.5 Flash 评估 | Task 1 (evaluate_ads + use case) |
| 路由：GET /api/ad-list | Task 2 |
| 路由：POST /api/evaluate | Task 2 |
| 详情弹窗扩展：AD 表格 | Task 3 (loadAdList) |
| 详情弹窗扩展：AI 评估按钮 + 结果 | Task 3 (runAdEvaluation, renderEvaluations) |
| 只建议不动作 | 代码无任何 Meta API 调用 |
| 不新增表 | 所有数据从已有表聚合 |
