# Push Module 集成 MVP 实施计划

> **已废弃（2026-04-20 同日）**：按此计划落地的 MVP 运行后发现上游和下游服务
> CORS 都没放行，浏览器直连方案跑不通。当天下午重构为 `AutoPush/` 本地子项目
> （FastAPI 代理 + 原生前端）。本计划保留作历史，最终落地方案见
> [AutoPush/README.md](../../../AutoPush/README.md)。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有 `/pushes/` 推送管理页面里新增「推送创建」「推送载荷」两个 tab（纯前端直连外部 OpenAPI 和下游推送服务），和原有本地 DB 列表共存。

**Architecture:** 不引入 React / 构建链。后端只在 `config.py` 加 3 个环境变量 + Flask 视图把配置注入模板。前端新增单个 ES module `pushes_direct.js`，把 push-module 的 `api/materials.js` 与两个 JSX 组件手工翻成原生 DOM + 事件绑定。样式复用 `pushes.css` 的 `--oc-*` 变量。

**Tech Stack:** Flask / Jinja2 / 原生 JS (ES module) / 浏览器 `fetch`。测试用 pytest（仅覆盖 Python 后端改动），前端 MVP 走手工验证。

**Spec:** [docs/superpowers/specs/2026-04-20-push-module-mvp-design.md](../specs/2026-04-20-push-module-mvp-design.md)

---

## 文件清单

| 动作 | 路径 | 说明 |
| --- | --- | --- |
| 修改 | `config.py` | 追加 3 个 `_env(...)` 常量 |
| 修改 | `tests/test_appcore_pushes.py` 或新建 `tests/test_config_push_direct.py` | 验证常量存在、默认值、读环境变量 |
| 修改 | `web/routes/pushes.py` | `index()` 视图传 `push_direct_config` dict 给模板 |
| 新建 | `tests/test_pushes_index_view.py` | 用 `authed_client_no_db` 访问 `/pushes/`，断言响应含 `PUSH_DIRECT_CONFIG` 注入 |
| 修改 | `web/templates/pushes_list.html` | 加 tab 切换条、2 个 tab 容器、config 注入 `<script>`、`pushes_direct.js` 引入 |
| 修改 | `web/static/pushes.css` | 尾部追加 tab + 两个新页面的表单样式 |
| 新建 | `web/static/pushes_direct.js` | ES module：config 读取、三个 API 函数、载荷校验、两个渲染器、tab init |

---

## Task 1: config.py 加 3 个环境变量

**Files:**
- Modify: `config.py:63-66`
- Create: `tests/test_config_push_direct.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_config_push_direct.py`：
```python
"""验证 config.py 为 push-module 直连模式暴露的环境变量。"""
import importlib


def _reload_config(monkeypatch, env: dict[str, str]):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import config
    return importlib.reload(config)


def test_autovideo_base_url_default(monkeypatch):
    monkeypatch.delenv("AUTOVIDEO_BASE_URL", raising=False)
    cfg = _reload_config(monkeypatch, {})
    assert cfg.AUTOVIDEO_BASE_URL == "http://14.103.220.208:8888"


def test_autovideo_base_url_env_override(monkeypatch):
    cfg = _reload_config(monkeypatch, {"AUTOVIDEO_BASE_URL": "http://example.test:9999"})
    assert cfg.AUTOVIDEO_BASE_URL == "http://example.test:9999"


def test_autovideo_api_key_default(monkeypatch):
    monkeypatch.delenv("AUTOVIDEO_API_KEY", raising=False)
    cfg = _reload_config(monkeypatch, {})
    assert cfg.AUTOVIDEO_API_KEY == "autovideosrt-materials-openapi"


def test_push_medias_target_default(monkeypatch):
    monkeypatch.delenv("PUSH_MEDIAS_TARGET", raising=False)
    cfg = _reload_config(monkeypatch, {})
    assert cfg.PUSH_MEDIAS_TARGET == "http://172.17.254.77:22400/dify/shopify/medias"


def test_push_medias_target_env_override(monkeypatch):
    cfg = _reload_config(monkeypatch, {"PUSH_MEDIAS_TARGET": "http://downstream.test/push"})
    assert cfg.PUSH_MEDIAS_TARGET == "http://downstream.test/push"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_config_push_direct.py -v`
Expected: FAIL — `AttributeError: module 'config' has no attribute 'AUTOVIDEO_BASE_URL'`

- [ ] **Step 3: 实现**

在 `config.py` 第 63 行 `PUSH_TARGET_URL = _env("PUSH_TARGET_URL", "")` 之后插入：
```python
# 推送管理 - push-module 纯前端直连模式
AUTOVIDEO_BASE_URL = _env("AUTOVIDEO_BASE_URL", "http://14.103.220.208:8888")
AUTOVIDEO_API_KEY = _env("AUTOVIDEO_API_KEY", "autovideosrt-materials-openapi")
PUSH_MEDIAS_TARGET = _env(
    "PUSH_MEDIAS_TARGET",
    "http://172.17.254.77:22400/dify/shopify/medias",
)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_config_push_direct.py -v`
Expected: PASS (5 个用例)

- [ ] **Step 5: 提交**

```bash
git add config.py tests/test_config_push_direct.py
git commit -m "feat(push): 暴露 push-module 直连模式的 3 个环境变量"
```

---

## Task 2: Flask index 视图把配置注入模板

**Files:**
- Modify: `web/routes/pushes.py:29-37`
- Create: `tests/test_pushes_index_view.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_pushes_index_view.py`：
```python
"""验证 /pushes/ 视图把 push-module 直连配置注入模板。"""
import pytest


@pytest.fixture
def client(authed_client_no_db):
    return authed_client_no_db


def test_index_renders_push_direct_config(client, monkeypatch):
    monkeypatch.setenv("AUTOVIDEO_BASE_URL", "http://test-upstream:8888")
    monkeypatch.setenv("AUTOVIDEO_API_KEY", "test-key")
    monkeypatch.setenv("PUSH_MEDIAS_TARGET", "http://test-downstream/medias")

    # 重新加载 config + 相关模块，使 monkeypatch 生效
    import importlib
    import config
    importlib.reload(config)
    from web.routes import pushes
    importlib.reload(pushes)

    resp = client.get("/pushes/")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "PUSH_DIRECT_CONFIG" in html
    assert "http://test-upstream:8888" in html
    assert "test-key" in html
    assert "http://test-downstream/medias" in html
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_pushes_index_view.py -v`
Expected: FAIL — HTML 里找不到 `PUSH_DIRECT_CONFIG`

- [ ] **Step 3: 修改视图**

把 `web/routes/pushes.py` 的 `index()` 从：
```python
@bp.route("/")
@login_required
def index():
    return render_template(
        "pushes_list.html",
        is_admin=_is_admin(),
        push_target_configured=bool((config.PUSH_TARGET_URL or "").strip()),
    )
```
改为：
```python
@bp.route("/")
@login_required
def index():
    push_direct_config = {
        "autovideoBaseUrl": config.AUTOVIDEO_BASE_URL,
        "autovideoApiKey":  config.AUTOVIDEO_API_KEY,
        "pushMediasTarget": config.PUSH_MEDIAS_TARGET,
    }
    return render_template(
        "pushes_list.html",
        is_admin=_is_admin(),
        push_target_configured=bool((config.PUSH_TARGET_URL or "").strip()),
        push_direct_config=push_direct_config,
    )
```

同时修改 `web/templates/pushes_list.html`，在 `<script>` 块里加一行：
```html
<script>
  window.PUSH_IS_ADMIN = {{ 'true' if is_admin else 'false' }};
  window.PUSH_TARGET_CONFIGURED = {{ 'true' if push_target_configured else 'false' }};
  window.PUSH_DIRECT_CONFIG = {{ push_direct_config | tojson }};
</script>
```

（tab/容器/样式引入放到 Task 3 里做，本步只加这一行让测试通过。）

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_pushes_index_view.py tests/test_config_push_direct.py -v`
Expected: ALL PASS

- [ ] **Step 5: 提交**

```bash
git add web/routes/pushes.py web/templates/pushes_list.html tests/test_pushes_index_view.py
git commit -m "feat(push): 把 push-module 直连配置注入 /pushes/ 页面"
```

---

## Task 3: 模板加 tab 切换结构

**Files:**
- Modify: `web/templates/pushes_list.html`

前端 UI 改动无自动化测试，依赖手工验证 + Task 5 的回归核对。

- [ ] **Step 1: 改模板**

把 `web/templates/pushes_list.html` 整体改为：
```html
{% extends "layout.html" %}
{% block title %}推送管理{% endblock %}
{% block content %}
<div class="page-header">
  <h1>🚀 推送管理</h1>
  {% if not push_target_configured %}
  <div class="warning-banner">
    推送目标 URL 未配置（PUSH_TARGET_URL），推送按钮已禁用。请联系管理员在 .env 中配置后重启。
  </div>
  {% endif %}
</div>

<nav class="push-tabs" role="tablist">
  <button type="button" class="push-tab active" data-tab="list"    role="tab" aria-selected="true">推送列表</button>
  <button type="button" class="push-tab"        data-tab="create"  role="tab" aria-selected="false">推送创建</button>
  <button type="button" class="push-tab"        data-tab="payload" role="tab" aria-selected="false">推送载荷</button>
</nav>

<section class="push-tab-panel" data-panel="list" role="tabpanel">
  <div class="push-toolbar">
    <div class="filter-group">
      <label>状态</label>
      <select id="f-status" multiple size="4">
        <option value="not_ready">未就绪</option>
        <option value="pending" selected>待推送</option>
        <option value="pushed">已推送</option>
        <option value="failed">推送失败</option>
      </select>
    </div>
    <div class="filter-group">
      <label>语种</label>
      <select id="f-lang" multiple size="4"></select>
    </div>
    <div class="filter-group">
      <label>产品</label>
      <input id="f-product" type="text" placeholder="产品名或 code" />
    </div>
    <div class="filter-group">
      <label>关键词</label>
      <input id="f-keyword" type="text" placeholder="素材文件名" />
    </div>
    <div class="filter-group">
      <label>更新时间</label>
      <input id="f-date-from" type="date" />
      <span>至</span>
      <input id="f-date-to" type="date" />
    </div>
    <div class="filter-actions">
      <button id="btn-apply" type="button">筛选</button>
      <button id="btn-reset" type="button">重置</button>
    </div>
  </div>

  <table class="push-table">
    <thead>
      <tr>
        <th>缩略图</th>
        <th>产品</th>
        <th>素材</th>
        <th>语种</th>
        <th>就绪</th>
        <th>状态</th>
        <th>创建时间</th>
        {% if is_admin %}<th>操作</th>{% endif %}
      </tr>
    </thead>
    <tbody id="push-tbody"><tr><td colspan="8">加载中…</td></tr></tbody>
  </table>

  <div class="pagination" id="push-pagination"></div>

  <div id="push-log-drawer" class="drawer" hidden>
    <div class="drawer-inner">
      <h3>推送历史</h3>
      <button class="drawer-close" id="drawer-close">×</button>
      <div id="drawer-content"></div>
    </div>
  </div>
</section>

<section class="push-tab-panel" data-panel="create" role="tabpanel" hidden>
  <div id="push-create-root"></div>
</section>

<section class="push-tab-panel" data-panel="payload" role="tabpanel" hidden>
  <div id="push-payload-root"></div>
</section>

<script>
  window.PUSH_IS_ADMIN = {{ 'true' if is_admin else 'false' }};
  window.PUSH_TARGET_CONFIGURED = {{ 'true' if push_target_configured else 'false' }};
  window.PUSH_DIRECT_CONFIG = {{ push_direct_config | tojson }};
</script>
<link rel="stylesheet" href="/static/pushes.css">
<script src="/static/pushes.js"></script>
<script type="module" src="/static/pushes_direct.js"></script>
{% endblock %}
```

**关键点**：原"推送列表"的所有内容（toolbar / table / pagination / drawer）**原样**包进 `<section class="push-tab-panel" data-panel="list">`，不改 id / class，确保 `pushes.js` 继续工作。

- [ ] **Step 2: 跑已有 index 测试，确认无回归**

Run: `pytest tests/test_pushes_index_view.py -v`
Expected: PASS（HTML 里仍含 `PUSH_DIRECT_CONFIG` 及注入值）

- [ ] **Step 3: 提交**

```bash
git add web/templates/pushes_list.html
git commit -m "feat(push): 推送管理页面加 3 tab 容器（列表/创建/载荷）"
```

---

## Task 4: pushes.css 加 tab 与表单样式

**Files:**
- Modify: `web/static/pushes.css`（在文件末尾追加）

- [ ] **Step 1: 追加样式**

在 `web/static/pushes.css` 末尾追加：
```css
/* ================================================================
 * Push-module tab 切换 + 两个新页面（推送创建 / 推送载荷）样式
 * 严格遵循 Ocean Blue 规范：hue ∈ 200-240，所有颜色走 --oc-* 变量。
 * ================================================================ */

.push-tabs {
  display: flex;
  gap: 0;
  border-bottom: 1px solid var(--oc-border);
  margin-bottom: var(--oc-sp-5);
}
.push-tab {
  height: 40px;
  padding: 0 var(--oc-sp-4);
  border: none;
  background: transparent;
  color: var(--oc-fg-muted);
  font-family: inherit;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
  transition: color var(--oc-dur-fast) var(--oc-ease),
              border-color var(--oc-dur-fast) var(--oc-ease);
}
.push-tab:hover { color: var(--oc-fg); }
.push-tab.active {
  color: var(--oc-accent);
  border-bottom-color: var(--oc-accent);
}
.push-tab:focus-visible {
  outline: none;
  box-shadow: 0 0 0 2px var(--oc-accent-ring);
  border-radius: var(--oc-r-sm);
}

.push-tab-panel[hidden] { display: none; }

.push-form-card {
  background: var(--oc-bg);
  border: 1px solid var(--oc-border);
  border-radius: var(--oc-r-lg);
  padding: var(--oc-sp-5);
  margin-bottom: var(--oc-sp-5);
  box-shadow: var(--oc-shadow-sm);
}
.push-form-card + .push-form-card { margin-top: 0; }
.push-form-card h3 {
  margin: 0 0 var(--oc-sp-4);
  font-size: 15px;
  font-weight: 600;
  color: var(--oc-fg);
}

.push-form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--oc-sp-4);
}
@media (max-width: 768px) {
  .push-form-grid { grid-template-columns: 1fr; }
}

.push-input-group {
  display: grid;
  gap: var(--oc-sp-1);
}
.push-input-label {
  font-size: 12px;
  font-weight: 500;
  color: var(--oc-fg-muted);
  letter-spacing: 0.02em;
}
.push-text-input,
.push-text-area {
  width: 100%;
  padding: 8px 10px;
  border: 1px solid var(--oc-border-strong);
  border-radius: var(--oc-r);
  background: var(--oc-bg);
  color: var(--oc-fg);
  font-family: inherit;
  font-size: 13px;
  outline: none;
  transition: border-color var(--oc-dur-fast) var(--oc-ease),
              box-shadow var(--oc-dur-fast) var(--oc-ease);
}
.push-text-input { height: 32px; }
.push-text-area {
  min-height: 88px;
  resize: vertical;
  font-family: "JetBrains Mono", "Geist Mono", ui-monospace,
               "SF Mono", Consolas, monospace;
  line-height: 1.5;
}
.push-text-area.short { min-height: 64px; }
.push-text-input:focus,
.push-text-area:focus {
  border-color: var(--oc-accent);
  box-shadow: 0 0 0 2px var(--oc-accent-ring);
}
.push-text-input[readonly],
.push-text-area[readonly] {
  background: var(--oc-bg-subtle);
  color: var(--oc-fg-muted);
}

.push-query-row {
  display: flex;
  gap: var(--oc-sp-3);
  align-items: center;
}
.push-query-row .push-text-input { flex: 1; min-width: 0; }

.push-btn-primary,
.push-btn-success,
.push-btn-ghost {
  height: 32px;
  padding: 0 14px;
  border: 1px solid transparent;
  border-radius: var(--oc-r);
  font-family: inherit;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: background var(--oc-dur-fast) var(--oc-ease),
              border-color var(--oc-dur-fast) var(--oc-ease),
              color var(--oc-dur-fast) var(--oc-ease);
}
.push-btn-primary {
  background: var(--oc-accent);
  color: #fff;
  border-color: var(--oc-accent);
}
.push-btn-primary:hover:not(:disabled) {
  background: var(--oc-accent-hover);
  border-color: var(--oc-accent-hover);
}
.push-btn-success {
  background: var(--oc-success);
  color: #fff;
  border-color: var(--oc-success);
}
.push-btn-success:hover:not(:disabled) { filter: brightness(0.94); }
.push-btn-ghost {
  background: var(--oc-bg);
  color: var(--oc-fg-muted);
  border-color: var(--oc-border-strong);
}
.push-btn-ghost:hover:not(:disabled) {
  background: var(--oc-bg-muted);
  color: var(--oc-fg);
}
.push-btn-primary:disabled,
.push-btn-success:disabled,
.push-btn-ghost:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.push-error-banner,
.push-info-banner {
  margin-top: var(--oc-sp-3);
  padding: var(--oc-sp-2) var(--oc-sp-3);
  border-radius: var(--oc-r);
  font-size: 13px;
  line-height: 1.5;
  white-space: pre-wrap;
}
.push-error-banner {
  background: var(--oc-danger-bg);
  color: var(--oc-danger-fg);
}
.push-info-banner {
  background: var(--oc-success-bg);
  color: var(--oc-success-fg);
}

.push-array-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--oc-sp-3);
  margin-bottom: var(--oc-sp-3);
}
.push-array-item {
  display: grid;
  gap: var(--oc-sp-3);
  border: 1px solid var(--oc-border);
  border-radius: var(--oc-r-md);
  padding: var(--oc-sp-3);
  background: var(--oc-bg-subtle);
  margin-bottom: var(--oc-sp-3);
}
.push-array-item-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.push-array-item-title {
  font-size: 12px;
  font-weight: 500;
  color: var(--oc-fg-muted);
  letter-spacing: 0.02em;
}
.push-array-row {
  display: flex;
  gap: var(--oc-sp-2);
  align-items: flex-end;
  margin-bottom: var(--oc-sp-2);
}
.push-array-row .push-input-group { flex: 1; min-width: 0; }
.push-empty-state {
  margin: 0;
  padding: var(--oc-sp-3);
  color: var(--oc-fg-subtle);
  font-size: 13px;
  text-align: center;
}

.push-media-list {
  display: grid;
  gap: var(--oc-sp-4);
  margin-top: var(--oc-sp-4);
}
.push-media-row {
  display: flex;
  gap: var(--oc-sp-4);
  flex-wrap: wrap;
}
.push-media-item {
  display: grid;
  gap: var(--oc-sp-2);
}
.push-media-frame {
  width: 220px;
  height: 392px;
  border-radius: var(--oc-r-md);
  border: 1px solid var(--oc-border);
  background: oklch(15% 0.02 235);
  object-fit: contain;
}
.push-media-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--oc-bg-muted);
  color: var(--oc-fg-subtle);
  font-size: 12px;
}

.push-json-preview {
  margin: 0;
  padding: var(--oc-sp-3);
  background: var(--oc-bg-subtle);
  border: 1px solid var(--oc-border);
  color: var(--oc-fg);
  border-radius: var(--oc-r);
  font-family: "JetBrains Mono", "Geist Mono", ui-monospace,
               "SF Mono", Consolas, monospace;
  font-size: 12px;
  line-height: 1.55;
  max-height: 360px;
  overflow: auto;
  white-space: pre;
}

.push-response-text {
  min-height: 180px;
}
```

- [ ] **Step 2: 提交**

```bash
git add web/static/pushes.css
git commit -m "feat(push): 加 push-module MVP 的 tab + 表单样式（Ocean Blue）"
```

---

## Task 5: 新建 pushes_direct.js —— config 读取 + 3 个 API 函数 + 载荷校验

**Files:**
- Create: `web/static/pushes_direct.js`

这个 Task 先把"数据层"搭完，UI 渲染放 Task 6。

- [ ] **Step 1: 新建文件骨架**

创建 `web/static/pushes_direct.js`，内容：
```javascript
/*
 * 推送管理 - push-module 直连模式。
 *
 * 来源：push-module/frontend/api/materials.js 与两个 JSX 组件的原生 JS 改写。
 * 不经过本项目后端，浏览器直接 fetch:
 *   - AutoVideo OpenAPI  (CFG.autovideoBaseUrl)
 *   - 下游推送服务        (CFG.pushMediasTarget)
 *
 * 依赖 window.PUSH_DIRECT_CONFIG (由 pushes_list.html 注入)。
 */

const CFG = window.PUSH_DIRECT_CONFIG || {};

/* ---------- 工具 ---------- */

function asText(value) {
  return value === null || value === undefined ? "" : String(value);
}

function createEl(tag, attrs = {}, children = []) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") el.className = v;
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (k.startsWith("on") && typeof v === "function") {
      el.addEventListener(k.slice(2).toLowerCase(), v);
    } else if (v === true) el.setAttribute(k, "");
    else if (v === false || v === null || v === undefined) continue;
    else el.setAttribute(k, v);
  }
  for (const c of [].concat(children)) {
    if (c === null || c === undefined || c === false) continue;
    el.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  }
  return el;
}

/* ---------- AutoVideo 上游：素材 / 推送载荷 ---------- */

function normalizeMaterialsResponse(raw) {
  const product = raw?.product ?? {};
  const covers = raw?.covers ?? {};
  const copywritings = raw?.copywritings ?? {};
  const items = raw?.items ?? [];
  return {
    product: {
      id: product.id ?? null,
      productCode: asText(product.product_code),
      name: asText(product.name),
      archived: Boolean(product.archived),
      createdAt: product.created_at ?? null,
      updatedAt: product.updated_at ?? null,
    },
    covers: Object.entries(covers).map(([lang, cover]) => ({
      lang,
      objectKey: asText(cover?.object_key),
      downloadUrl: asText(cover?.download_url),
      expiresIn: cover?.expires_in ?? raw?.expires_in ?? null,
    })),
    copywritings: Object.fromEntries(
      Object.entries(copywritings).map(([lang, list]) => [
        lang,
        (list ?? []).map((item, index) => ({
          id: `${lang}-${index}`,
          title: asText(item?.title),
          body: asText(item?.body),
          description: asText(item?.description),
          adCarrier: asText(item?.ad_carrier),
          adCopy: asText(item?.ad_copy),
          adKeywords: asText(item?.ad_keywords),
        })),
      ]),
    ),
    items: items.map((item) => ({
      id: item?.id ?? null,
      lang: asText(item?.lang),
      filename: asText(item?.filename),
      displayName: asText(item?.display_name || item?.filename),
      objectKey: asText(item?.object_key),
      videoDownloadUrl: asText(item?.video_download_url),
      coverObjectKey: asText(item?.cover_object_key),
      videoCoverDownloadUrl: asText(item?.video_cover_download_url),
      durationSeconds: item?.duration_seconds ?? 0,
      fileSize: item?.file_size ?? 0,
      createdAt: item?.created_at ?? null,
    })),
    expiresIn: raw?.expires_in ?? null,
  };
}

function mapUpstreamError(status) {
  if (status === 401) return "接口认证失败，请检查 API Key";
  if (status === 404) return "未找到该产品，请确认 product_code";
  return "查询失败，请稍后重试";
}

async function requestUpstream(url) {
  let response;
  try {
    response = await fetch(url, {
      method: "GET",
      headers: { "X-API-Key": CFG.autovideoApiKey || "" },
    });
  } catch (networkError) {
    const error = new Error(
      "网络请求失败：" + (networkError.message ?? "未知错误") +
      "（可能是 CORS 未放行或地址不可达）",
    );
    error.cause = networkError;
    throw error;
  }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(payload.error || mapUpstreamError(response.status));
    error.status = response.status;
    error.detail = payload.error ?? "";
    error.payload = payload;
    throw error;
  }
  return payload;
}

async function fetchMaterials(productCode) {
  const url = `${CFG.autovideoBaseUrl}/openapi/materials/${encodeURIComponent(productCode)}`;
  const raw = await requestUpstream(url);
  return normalizeMaterialsResponse(raw);
}

async function fetchPushPayload(productCode, lang) {
  const url =
    `${CFG.autovideoBaseUrl}/openapi/materials/${encodeURIComponent(productCode)}/push-payload` +
    `?lang=${encodeURIComponent(lang)}`;
  return requestUpstream(url);
}

async function pushMedias(payload) {
  let response;
  try {
    response = await fetch(CFG.pushMediasTarget, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch (networkError) {
    const error = new Error(
      "推送服务不可达：" + (networkError.message ?? "未知错误") +
      "（可能是 CORS 未放行或地址不可达）",
    );
    error.cause = networkError;
    throw error;
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(body.message || "推送失败");
    error.status = response.status;
    error.detail = body.detail ?? body ?? "";
    error.payload = body;
    throw error;
  }
  return body;
}

/* ---------- 载荷校验 ---------- */

function validatePayload(payload) {
  const errors = [];
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    return ["payload 为空或不是对象"];
  }
  const isStr = (v) => typeof v === "string";
  const isNum = (v) => typeof v === "number" && !Number.isNaN(v);
  const isArr = Array.isArray;

  const check = (key, ok, typeName) => {
    if (!ok(payload[key])) errors.push(`字段 ${key} 必须是 ${typeName}`);
  };
  check("mode", isStr, "string");
  check("product_name", isStr, "string");
  check("texts", isArr, "array");
  check("product_links", isArr, "array");
  check("videos", isArr, "array");
  check("source", isNum, "number");
  check("level", isNum, "number");
  check("author", isStr, "string");
  check("push_admin", isStr, "string");
  check("roas", isNum, "number");
  check("platforms", isArr, "array");
  check("selling_point", isStr, "string");
  check("tags", isArr, "array");

  if (isArr(payload.texts)) {
    payload.texts.forEach((t, i) => {
      if (!t || typeof t !== "object") {
        errors.push(`texts[${i}] 不是对象`);
        return;
      }
      ["title", "message", "description"].forEach((k) => {
        if (!isStr(t[k])) errors.push(`texts[${i}].${k} 必须是 string`);
      });
    });
  }
  if (isArr(payload.product_links)) {
    payload.product_links.forEach((l, i) => {
      if (!isStr(l)) errors.push(`product_links[${i}] 必须是 string`);
    });
  }
  if (isArr(payload.platforms)) {
    payload.platforms.forEach((p, i) => {
      if (!isStr(p)) errors.push(`platforms[${i}] 必须是 string`);
    });
  }
  if (isArr(payload.videos)) {
    payload.videos.forEach((v, i) => {
      if (!v || typeof v !== "object") {
        errors.push(`videos[${i}] 不是对象`);
        return;
      }
      ["name", "url", "image_url"].forEach((k) => {
        if (!isStr(v[k])) errors.push(`videos[${i}].${k} 必须是 string`);
      });
      ["size", "width", "height"].forEach((k) => {
        if (!isNum(v[k])) errors.push(`videos[${i}].${k} 必须是 number`);
      });
    });
  }
  return errors;
}

/* ---------- 渲染器占位（Task 6 填充） ---------- */

function renderPushCreate(container) {
  container.textContent = "（推送创建渲染器待实现 - Task 6）";
}

function renderPushPayload(container) {
  container.textContent = "（推送载荷渲染器待实现 - Task 6）";
}

/* ---------- tab 切换 ---------- */

function initTabs() {
  const tabs = document.querySelectorAll(".push-tab");
  const panels = document.querySelectorAll(".push-tab-panel");
  if (!tabs.length) return;

  let createInit = false;
  let payloadInit = false;

  function activate(name) {
    tabs.forEach((t) => {
      const active = t.dataset.tab === name;
      t.classList.toggle("active", active);
      t.setAttribute("aria-selected", active ? "true" : "false");
    });
    panels.forEach((p) => {
      p.hidden = p.dataset.panel !== name;
    });
    if (name === "create" && !createInit) {
      renderPushCreate(document.getElementById("push-create-root"));
      createInit = true;
    }
    if (name === "payload" && !payloadInit) {
      renderPushPayload(document.getElementById("push-payload-root"));
      payloadInit = true;
    }
  }

  tabs.forEach((t) => {
    t.addEventListener("click", () => activate(t.dataset.tab));
  });
}

initTabs();
```

- [ ] **Step 2: 手工验证（最小烟测）**

```
1. 启动开发服: flask --app web.app run 或项目既有的启动命令
2. 浏览器打开 /pushes/
3. 打开 DevTools Console，确认没有 "Failed to load module script" 或语法错
4. 点「推送创建」tab：主区域出现"（推送创建渲染器待实现 - Task 6）"
5. 点「推送载荷」tab：主区域出现"（推送载荷渲染器待实现 - Task 6）"
6. 点回「推送列表」tab：原列表仍能正常加载
7. Console 执行 `window.PUSH_DIRECT_CONFIG` 能看到 3 个字段
```

- [ ] **Step 3: 提交**

```bash
git add web/static/pushes_direct.js
git commit -m "feat(push): 新增 pushes_direct.js 的数据层 + tab 切换骨架"
```

---

## Task 6: 实现推送载荷渲染器（先做这个，简单且能验证端到端）

**Files:**
- Modify: `web/static/pushes_direct.js`（替换 `renderPushPayload` 占位）

- [ ] **Step 1: 实现 renderPushPayload**

把 `pushes_direct.js` 里的
```javascript
function renderPushPayload(container) {
  container.textContent = "（推送载荷渲染器待实现 - Task 6）";
}
```
替换为：
```javascript
function renderPushPayload(container) {
  const state = {
    productCode: "3d-curved-screen-magnifier-for-smartphones",
    lang: "de",
    fetching: false,
    errorMessage: "",
    responseText: "",
    videos: [],
    payloadData: null,
    pushing: false,
    pushError: "",
    pushResult: "",
  };

  container.innerHTML = "";
  const root = createEl("section", { class: "push-form-card" });
  container.appendChild(root);

  // 顶部输入
  const inputs = createEl("div", { class: "push-form-grid" });
  const codeGroup = createEl("label", { class: "push-input-group" }, [
    createEl("span", { class: "push-input-label" }, "product_code"),
  ]);
  const codeInput = createEl("input", {
    class: "push-text-input",
    type: "text",
    value: state.productCode,
    placeholder: "例如：3d-curved-screen-magnifier-for-smartphones",
  });
  codeInput.addEventListener("input", (e) => { state.productCode = e.target.value; });
  codeGroup.appendChild(codeInput);

  const langGroup = createEl("label", { class: "push-input-group" }, [
    createEl("span", { class: "push-input-label" }, "lang（de/fr/es/it/ja/pt 等）"),
  ]);
  const langInput = createEl("input", {
    class: "push-text-input",
    type: "text",
    value: state.lang,
    placeholder: "例如：de",
  });
  langInput.addEventListener("input", (e) => { state.lang = e.target.value; });
  langGroup.appendChild(langInput);

  inputs.appendChild(codeGroup);
  inputs.appendChild(langGroup);
  root.appendChild(inputs);

  // 按钮
  const btnRow = createEl("div", {
    class: "push-array-row",
    style: "margin-top: 16px; gap: 12px;",
  });
  const btnFetch = createEl("button", {
    type: "button", class: "push-btn-primary",
  }, "加载数据");
  const btnPush = createEl("button", {
    type: "button", class: "push-btn-success", disabled: true,
  }, "推送");
  btnRow.appendChild(btnFetch);
  btnRow.appendChild(btnPush);
  root.appendChild(btnRow);

  // 错误/信息条 + 响应文本区
  const errBanner = createEl("p", { class: "push-error-banner", hidden: true });
  root.appendChild(errBanner);

  const respGroup = createEl("label", {
    class: "push-input-group",
    style: "margin-top: 16px;",
  }, [createEl("span", { class: "push-input-label" }, "返回报文（JSON）")]);
  const respText = createEl("textarea", {
    class: "push-text-area push-response-text",
    readonly: true,
    placeholder: "点击“加载数据”后，这里会显示上游返回的完整 JSON 报文",
  });
  respGroup.appendChild(respText);
  root.appendChild(respGroup);

  const pushErrBanner = createEl("pre", {
    class: "push-error-banner",
    style: "white-space: pre-wrap;",
    hidden: true,
  });
  root.appendChild(pushErrBanner);

  const pushResultGroup = createEl("label", {
    class: "push-input-group",
    style: "margin-top: 16px;",
    hidden: true,
  }, [createEl("span", { class: "push-input-label" }, "推送响应")]);
  const pushResultText = createEl("textarea", {
    class: "push-text-area push-response-text",
    readonly: true,
  });
  pushResultGroup.appendChild(pushResultText);
  root.appendChild(pushResultGroup);

  // 视频预览容器
  const mediaList = createEl("div", { class: "push-media-list", hidden: true });
  root.appendChild(mediaList);

  function syncUI() {
    btnFetch.disabled = state.fetching;
    btnFetch.textContent = state.fetching ? "加载中..." : "加载数据";
    btnPush.disabled = state.pushing || !state.payloadData;
    btnPush.textContent = state.pushing ? "推送中..." : "推送";
    codeInput.disabled = state.fetching;
    langInput.disabled = state.fetching;

    errBanner.hidden = !state.errorMessage;
    errBanner.textContent = state.errorMessage;
    respText.value = state.responseText;

    pushErrBanner.hidden = !state.pushError;
    pushErrBanner.textContent = state.pushError;
    pushResultGroup.hidden = !state.pushResult;
    pushResultText.value = state.pushResult;

    // 视频预览
    mediaList.innerHTML = "";
    mediaList.hidden = state.videos.length === 0;
    state.videos.forEach((video, i) => {
      const row = createEl("div", { class: "push-media-row" });

      const coverItem = createEl("div", { class: "push-media-item" }, [
        createEl("span", { class: "push-input-label" }, `videos[${i}].image_url`),
      ]);
      if (video.image_url) {
        coverItem.appendChild(createEl("img", {
          class: "push-media-frame",
          src: video.image_url,
          alt: video.name ?? `cover-${i}`,
        }));
      } else {
        coverItem.appendChild(createEl("div", {
          class: "push-media-frame push-media-empty",
        }, "无封面"));
      }

      const videoItem = createEl("div", { class: "push-media-item" }, [
        createEl("span", { class: "push-input-label" }, `videos[${i}].url`),
      ]);
      if (video.url) {
        videoItem.appendChild(createEl("video", {
          class: "push-media-frame",
          src: video.url,
          poster: video.image_url || null,
          controls: true,
          preload: "metadata",
        }));
      } else {
        videoItem.appendChild(createEl("div", {
          class: "push-media-frame push-media-empty",
        }, "无视频"));
      }

      row.appendChild(coverItem);
      row.appendChild(videoItem);
      mediaList.appendChild(row);
    });
  }

  async function handleFetch() {
    const code = state.productCode.trim();
    const lang = state.lang.trim();
    if (!code) { state.errorMessage = "请输入 product_code"; syncUI(); return; }
    if (!lang) { state.errorMessage = "请输入 lang"; syncUI(); return; }

    Object.assign(state, {
      fetching: true, errorMessage: "", responseText: "",
      videos: [], payloadData: null, pushError: "", pushResult: "",
    });
    syncUI();
    try {
      const payload = await fetchPushPayload(code, lang);
      state.responseText = JSON.stringify(payload, null, 2);
      state.videos = Array.isArray(payload?.videos) ? payload.videos : [];
      state.payloadData = payload;
    } catch (error) {
      state.errorMessage = error.message ?? "查询失败，请稍后重试";
      const errPayload = error.payload ?? {
        message: error.message, detail: error.detail, status: error.status,
      };
      state.responseText = JSON.stringify(errPayload, null, 2);
    } finally {
      state.fetching = false;
      syncUI();
    }
  }

  async function handlePush() {
    if (!state.payloadData) {
      state.pushError = "请先点击“加载数据”获取到有效报文再推送";
      state.pushResult = ""; syncUI(); return;
    }
    const errors = validatePayload(state.payloadData);
    if (errors.length > 0) {
      state.pushError = "数据格式校验失败：\n- " + errors.join("\n- ");
      state.pushResult = ""; syncUI(); return;
    }
    Object.assign(state, { pushing: true, pushError: "", pushResult: "" });
    syncUI();
    try {
      const body = await pushMedias(state.payloadData);
      state.pushResult = JSON.stringify(body, null, 2);
    } catch (error) {
      state.pushError = error.message ?? "推送失败";
      const errPayload = error.payload ?? {
        message: error.message, detail: error.detail, status: error.status,
      };
      state.pushResult = JSON.stringify(errPayload, null, 2);
    } finally {
      state.pushing = false;
      syncUI();
    }
  }

  btnFetch.addEventListener("click", handleFetch);
  btnPush.addEventListener("click", handlePush);

  syncUI();
}
```

- [ ] **Step 2: 手工验证**

```
1. 刷新 /pushes/，切到「推送载荷」tab
2. product_code 框预填 3d-curved-screen-magnifier-for-smartphones，lang=de
3. 点「加载数据」：
   - 通情况：下方 JSON 框显示完整 payload，视频预览出现
   - CORS/401 情况：红色 banner 显示错误；JSON 框显示错误详情
4. 点「推送」：
   - 通情况：底部「推送响应」框显示下游返回
   - CORS 情况：红色 banner 显示网络错误
5. 切回「推送列表」tab，确认原列表仍正常
6. 再切回「推送载荷」tab，确认之前的输入和响应仍保留（单次 session 内的 state）
```

- [ ] **Step 3: 提交**

```bash
git add web/static/pushes_direct.js
git commit -m "feat(push): 实现推送载荷 tab（加载上游 payload + 浏览器直推）"
```

---

## Task 7: 实现推送创建渲染器（表单 + JSON 预览）

**Files:**
- Modify: `web/static/pushes_direct.js`（替换 `renderPushCreate` 占位）

- [ ] **Step 1: 实现 renderPushCreate**

替换 `renderPushCreate` 占位为下面实现。把所有字段渲成表单，支持增删数组项，右下角实时 JSON 预览。

```javascript
function renderPushCreate(container) {
  const defaultForm = {
    mode: "create",
    product_name: "液体慢喂狗碗",
    texts: [
      {
        title: "🐾 Too cold for long walks? Keep them busy indoors.",
        message: "Winter days mean less time outside, but your dog still has energy to burn.",
        description: "Shop Now & Beat Winter Boredom",
      },
    ],
    product_links: [""],
    videos: [
      {
        name: "sample.mp4",
        size: "20539247",
        width: "1440",
        height: "2560",
        url: "",
        image_url: "",
      },
    ],
    source: "0",
    level: "3",
    author: "李文龙",
    push_admin: "陈绍坤",
    roas: "1.55",
    platforms: ["shop"],
    selling_point: "",
    tags: [],
  };
  const emptyText = { title: "", message: "", description: "" };
  const emptyVideo = { name: "", size: "", width: "", height: "", url: "", image_url: "" };

  const state = {
    form: JSON.parse(JSON.stringify(defaultForm)),
    productCode: "",
    fetching: false,
    fetchError: "",
    fetchInfo: "",
    responseText: "",
  };

  container.innerHTML = "";

  // --- 查询区 ---
  const queryCard = createEl("section", { class: "push-form-card" });
  const queryRow = createEl("div", { class: "push-query-row" });
  const codeInput = createEl("input", {
    class: "push-text-input", type: "text",
    placeholder: "输入产品 ID / product_code",
  });
  codeInput.addEventListener("input", (e) => { state.productCode = e.target.value; });
  const btnFetch = createEl("button", { type: "button", class: "push-btn-primary" }, "获取");
  btnFetch.addEventListener("click", handleFetch);
  queryRow.appendChild(codeInput);
  queryRow.appendChild(btnFetch);
  queryCard.appendChild(queryRow);
  const fetchErr = createEl("p", { class: "push-error-banner", hidden: true });
  const fetchInfo = createEl("p", { class: "push-info-banner", hidden: true });
  queryCard.appendChild(fetchErr);
  queryCard.appendChild(fetchInfo);
  const respGroup = createEl("label", {
    class: "push-input-group", style: "margin-top: 16px;",
  }, [createEl("span", { class: "push-input-label" }, "返回报文（JSON）")]);
  const respText = createEl("textarea", {
    class: "push-text-area push-response-text",
    readonly: true,
    placeholder: "点击“获取”后，这里会显示上游返回的完整 JSON 报文",
  });
  respGroup.appendChild(respText);
  queryCard.appendChild(respGroup);
  container.appendChild(queryCard);

  // --- 表单卡片们 ---
  const basicCard = buildBasicCard();
  const textsCard = buildArrayObjectCard("texts", emptyText, renderTextItem);
  const linksCard = buildArrayStringCard("product_links", "链接");
  const videosCard = buildArrayObjectCard("videos", emptyVideo, renderVideoItem);
  const platformsCard = buildArrayStringCard("platforms", "");
  const tagsCard = buildArrayStringCard("tags", "");
  const jsonCard = createEl("section", { class: "push-form-card" }, [
    createEl("h3", {}, "JSON 预览"),
  ]);
  const jsonPre = createEl("pre", { class: "push-json-preview" });
  jsonCard.appendChild(jsonPre);
  container.appendChild(basicCard);
  container.appendChild(textsCard);
  container.appendChild(linksCard);
  container.appendChild(videosCard);
  container.appendChild(platformsCard);
  container.appendChild(tagsCard);
  container.appendChild(jsonCard);

  function buildBasicCard() {
    const card = createEl("section", { class: "push-form-card" }, [
      createEl("h3", {}, "基本信息"),
    ]);
    const grid = createEl("div", { class: "push-form-grid" });
    ["mode", "product_name", "source", "level", "author", "push_admin", "roas", "selling_point"]
      .forEach((key) => {
        const group = createEl("label", { class: "push-input-group" }, [
          createEl("span", { class: "push-input-label" }, key),
        ]);
        const input = createEl("input", { class: "push-text-input", type: "text" });
        input.value = state.form[key] ?? "";
        input.addEventListener("input", (e) => {
          state.form[key] = e.target.value;
          refreshJson();
        });
        group.appendChild(input);
        grid.appendChild(group);
      });
    card.appendChild(grid);
    return card;
  }

  function buildArrayObjectCard(key, template, itemRenderer) {
    const card = createEl("section", { class: "push-form-card" });
    const header = createEl("div", { class: "push-array-header" }, [
      createEl("h3", {}, key),
      createEl("button", {
        type: "button", class: "push-btn-ghost",
        onclick: () => {
          state.form[key] = [...state.form[key], JSON.parse(JSON.stringify(template))];
          rerenderArrayCard(card, key, itemRenderer);
        },
      }, "添加"),
    ]);
    card.appendChild(header);
    card.appendChild(createEl("div", { dataset: { list: "1" } }));
    rerenderArrayCard(card, key, itemRenderer);
    return card;
  }

  function rerenderArrayCard(card, key, itemRenderer) {
    const list = card.querySelector("[data-list]");
    list.innerHTML = "";
    const items = state.form[key] || [];
    if (items.length === 0) {
      list.appendChild(createEl("p", { class: "push-empty-state" }, "（空）"));
      return;
    }
    items.forEach((item, index) => {
      const wrapper = createEl("div", { class: "push-array-item" });
      const head = createEl("div", { class: "push-array-item-header" }, [
        createEl("span", { class: "push-array-item-title" }, `${key}[${index}]`),
        createEl("button", {
          type: "button", class: "push-btn-ghost",
          onclick: () => {
            state.form[key] = state.form[key].filter((_, i) => i !== index);
            rerenderArrayCard(card, key, itemRenderer);
            refreshJson();
          },
        }, "删除"),
      ]);
      wrapper.appendChild(head);
      itemRenderer(wrapper, key, index, item);
      list.appendChild(wrapper);
    });
  }

  function renderTextItem(wrapper, key, index) {
    ["title", "message", "description"].forEach((field) => {
      const group = createEl("label", { class: "push-input-group" }, [
        createEl("span", { class: "push-input-label" }, field),
      ]);
      const ta = createEl("textarea", { class: "push-text-area short" });
      ta.value = state.form[key][index][field] ?? "";
      ta.addEventListener("input", (e) => {
        state.form[key][index][field] = e.target.value;
        refreshJson();
      });
      group.appendChild(ta);
      wrapper.appendChild(group);
    });
  }

  function renderVideoItem(wrapper, key, index) {
    const grid = createEl("div", { class: "push-form-grid" });
    ["name", "size", "width", "height"].forEach((field) => {
      const group = createEl("label", { class: "push-input-group" }, [
        createEl("span", { class: "push-input-label" }, field),
      ]);
      const input = createEl("input", { class: "push-text-input", type: "text" });
      input.value = state.form[key][index][field] ?? "";
      input.addEventListener("input", (e) => {
        state.form[key][index][field] = e.target.value;
        refreshJson();
      });
      group.appendChild(input);
      grid.appendChild(group);
    });
    wrapper.appendChild(grid);
    ["url", "image_url"].forEach((field) => {
      const group = createEl("label", { class: "push-input-group" }, [
        createEl("span", { class: "push-input-label" }, field),
      ]);
      const ta = createEl("textarea", { class: "push-text-area short" });
      ta.value = state.form[key][index][field] ?? "";
      ta.addEventListener("input", (e) => {
        state.form[key][index][field] = e.target.value;
        refreshJson();
      });
      group.appendChild(ta);
      wrapper.appendChild(group);
    });
  }

  function buildArrayStringCard(key /* , placeholder */) {
    const card = createEl("section", { class: "push-form-card" });
    const header = createEl("div", { class: "push-array-header" }, [
      createEl("h3", {}, key),
      createEl("button", {
        type: "button", class: "push-btn-ghost",
        onclick: () => {
          state.form[key] = [...state.form[key], ""];
          rerenderStringArrayCard(card, key);
          refreshJson();
        },
      }, "添加"),
    ]);
    card.appendChild(header);
    card.appendChild(createEl("div", { dataset: { list: "1" } }));
    rerenderStringArrayCard(card, key);
    return card;
  }

  function rerenderStringArrayCard(card, key) {
    const list = card.querySelector("[data-list]");
    list.innerHTML = "";
    const items = state.form[key] || [];
    if (items.length === 0) {
      list.appendChild(createEl("p", { class: "push-empty-state" }, "（空）"));
      return;
    }
    items.forEach((value, index) => {
      const row = createEl("div", { class: "push-array-row" });
      const group = createEl("label", { class: "push-input-group" }, [
        createEl("span", { class: "push-input-label" }, `${key}[${index}]`),
      ]);
      const input = createEl("input", { class: "push-text-input", type: "text" });
      input.value = value ?? "";
      input.addEventListener("input", (e) => {
        state.form[key][index] = e.target.value;
        refreshJson();
      });
      group.appendChild(input);
      row.appendChild(group);
      row.appendChild(createEl("button", {
        type: "button", class: "push-btn-ghost",
        onclick: () => {
          state.form[key] = state.form[key].filter((_, i) => i !== index);
          rerenderStringArrayCard(card, key);
          refreshJson();
        },
      }, "删除"));
      list.appendChild(row);
    });
  }

  function refreshJson() {
    jsonPre.textContent = JSON.stringify(state.form, null, 2);
  }

  function syncFetchBanners() {
    fetchErr.hidden = !state.fetchError;
    fetchErr.textContent = state.fetchError;
    fetchInfo.hidden = !state.fetchInfo;
    fetchInfo.textContent = state.fetchInfo;
    respText.value = state.responseText;
    btnFetch.disabled = state.fetching;
    btnFetch.textContent = state.fetching ? "获取中..." : "获取";
    codeInput.disabled = state.fetching;
  }

  async function handleFetch() {
    const trimmed = state.productCode.trim();
    if (!trimmed) {
      state.fetchError = "请输入 product_code";
      state.fetchInfo = "";
      syncFetchBanners();
      return;
    }
    Object.assign(state, {
      fetching: true, fetchError: "", fetchInfo: "", responseText: "",
    });
    syncFetchBanners();
    try {
      const payload = await fetchMaterials(trimmed);
      const name = payload?.product?.name ?? "";
      if (name) {
        state.form.product_name = name;
        // 重新渲染基本信息卡片里的 product_name 输入框
        const inputs = container.querySelectorAll("input.push-text-input");
        inputs.forEach((inp) => {
          const label = inp.previousSibling?.textContent;
          if (label === "product_name") inp.value = name;
        });
        refreshJson();
      }
      state.fetchInfo =
        `已获取：${name || "未命名产品"}（product_code: ${payload?.product?.productCode ?? trimmed}）`;
      state.responseText = JSON.stringify(payload, null, 2);
    } catch (error) {
      state.fetchError = error.message ?? "查询失败，请稍后重试";
      const errPayload = error.payload ?? {
        message: error.message, detail: error.detail, status: error.status,
      };
      state.responseText = JSON.stringify(errPayload, null, 2);
    } finally {
      state.fetching = false;
      syncFetchBanners();
    }
  }

  refreshJson();
}
```

- [ ] **Step 2: 手工验证**

```
1. 刷新 /pushes/，切到「推送创建」tab
2. 看到所有字段默认值都在（mode, product_name, texts, product_links, videos, platforms, tags 等）
3. 修改任何字段，右下角 JSON 预览实时更新
4. 「texts / videos / product_links / platforms / tags」能「添加」「删除」
5. 顶部输入一个 product_code，点「获取」：
   - 通：下方 JSON 返回，product_name 自动回填
   - 失败：错误 banner 显示
6. 切回「推送列表」tab，确认原表格仍工作
```

- [ ] **Step 3: 提交**

```bash
git add web/static/pushes_direct.js
git commit -m "feat(push): 实现推送创建 tab（表单 + 数组增删 + JSON 预览）"
```

---

## Task 8: 全量回归 + 最终提交

- [ ] **Step 1: 跑完整测试**

Run: `pytest tests/test_config_push_direct.py tests/test_pushes_index_view.py tests/test_appcore_pushes.py -v`
Expected: ALL PASS

- [ ] **Step 2: 手工完整路径验证**

```
1. 打开 /pushes/，默认「推送列表」tab 工作正常（能看到列表、筛选、推送按钮）
2. 切到「推送创建」：表单完整，JSON 预览随输入更新；product_code 获取功能可用
3. 切到「推送载荷」：加载数据 + 视频预览 + 推送按钮走通
4. 切回「推送列表」：仍正常
5. DevTools Console 无报错
6. DevTools Network 可见：
   - 获取素材 → GET http://14.103.220.208:8888/openapi/materials/{code}
   - 加载推送载荷 → GET http://14.103.220.208:8888/openapi/materials/{code}/push-payload?lang=...
   - 推送 → POST http://172.17.254.77:22400/dify/shopify/medias
```

- [ ] **Step 3: 如果 CORS 失败**

不修改代码，在 spec 对应的"风险 A"栏记一笔实际现象（哪个地址挂了、具体报错），方便后续决策。

- [ ] **Step 4: 如果全部通过，打最终 tag（可选）**

```bash
git log --oneline -n 10
```
确认 6 个 commit（Task 1-7）齐全，然后可以合并/推送（按项目发布流程，不在本计划内）。

---

## 自检（Self-Review）

**1. Spec coverage**：
- 3.1 页面结构 → Task 3 ✓
- 3.2 文件清单 → Task 1-7 分别覆盖 ✓
- 3.3 pushes_direct.js 结构 → Task 5（数据层） + 6（PushPayload 渲染） + 7（PushCreate 渲染） ✓
- 3.4 配置注入 → Task 1 + 2 ✓
- 3.5 样式 → Task 4 ✓
- 4.1 推送创建行为 → Task 7 手工验证覆盖 ✓
- 4.2 推送载荷行为 → Task 6 手工验证覆盖 ✓
- 4.3 CORS 失败态 → Task 5 `requestUpstream` / `pushMedias` 的 try-catch ✓
- 5.1/5.2/5.3 验证标准 → Task 8 ✓
- 6 风险 → Task 8 Step 3 预留了记录入口 ✓
- 7 非本期 → 明确排除 ✓

**2. Placeholder scan**：无 TBD / "implement later"；每步有完整代码。Task 5 的渲染器占位在 Task 6/7 明确替换。

**3. Type consistency**：
- `fetchMaterials / fetchPushPayload / pushMedias` 三个函数在 Task 5 定义，Task 6/7 使用，签名一致
- `validatePayload` 在 Task 5 定义，Task 6 使用
- `createEl` 签名在 Task 5 定义，Task 6/7 使用一致
- `state.form` / `state.payloadData` 等 state 字段在各 Task 内部闭包，无跨任务共享风险
- CSS class 名（`push-form-card` / `push-btn-primary` 等）在 Task 4 定义，Task 5/6/7 使用一致
