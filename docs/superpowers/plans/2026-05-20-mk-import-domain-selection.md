# Mk Import Domain Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a final publish-domain selection step to the Mingkong “加入素材库” progress modal and save the choice into the existing product link-domain management logic.

**Architecture:** Keep all domain behavior on the existing product-level link-domain APIs. The Mingkong import modal only loads options after `POST /mk-import/video` returns a product id, lets the operator choose domains, posts `enabled_domain_ids`, and only then opens the follow-up action buttons.

**Tech Stack:** Flask routes, Jinja template JavaScript, pytest route/static assertions, existing `appcore.product_link_domains` service.

---

## File Map

- Modify `web/templates/mk_selection.html`: add the domain step, modal domain panel, fetch/save helpers, and action gating.
- Modify `tests/test_xuanpin_routes.py`: assert the Mingkong page contains the domain selection UI and the save flow.
- Modify `tests/test_media_product_link_domains_routes.py`: assert the existing POST endpoint accepts an empty list, because the UI allows canceling all default domains.
- No backend route or schema change is expected.

## Task 1: Route Contract Test For Empty Domain Selection

**Files:**
- Modify: `tests/test_media_product_link_domains_routes.py`

- [ ] **Step 1: Add the failing test**

Append this test after `test_medias_product_link_domains_post_saves_enabled_ids`:

```python
def test_medias_product_link_domains_post_allows_empty_enabled_ids(
    authed_client_no_db, monkeypatch
):
    product = {"id": 10, "product_code": "demo-rjc"}
    captured = {}

    monkeypatch.setattr("web.routes.medias.medias.get_product", lambda pid: product)
    monkeypatch.setattr(
        "web.routes.medias.product_link_domains.set_product_domain_enabled_ids",
        lambda product_id, ids: captured.update({"product_id": product_id, "ids": ids}),
        raising=False,
    )
    monkeypatch.setattr(
        "web.routes.medias.product_link_domains.list_product_domain_options",
        lambda product_id: [],
        raising=False,
    )

    resp = authed_client_no_db.post(
        "/medias/api/products/10/product-link-domains",
        json={"enabled_domain_ids": []},
    )

    assert resp.status_code == 200
    assert captured == {"product_id": 10, "ids": []}
    assert resp.get_json() == {"ok": True, "domains": []}
```

- [ ] **Step 2: Run the route test**

Run:

```bash
pytest tests/test_media_product_link_domains_routes.py -q
```

Expected: the new test should pass already. If it fails, keep the endpoint contract as `enabled_domain_ids: []` and fix only the parser/route behavior.

- [ ] **Step 3: Commit the route contract**

Run:

```bash
git add tests/test_media_product_link_domains_routes.py
git commit -m "test: allow empty product link domain selection"
```

## Task 2: Mingkong Modal Static Tests

**Files:**
- Modify: `tests/test_xuanpin_routes.py`

- [ ] **Step 1: Add static assertions for the new modal domain step**

Append this test near the existing Mingkong import button tests:

```python
def test_xuanpin_mk_import_progress_includes_publish_domain_step(authed_client_no_db):
    resp = authed_client_no_db.get("/xuanpin/mk")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "{key: 'domains', title: '选择发布域名'" in body
    assert "mkiImportProgressDomains" in body
    assert "mkiImportProgressRenderDomainRows" in body
    assert "product-link-domains" in body
    assert "enabled_domain_ids" in body
    assert "确定发布域名" in body
```

- [ ] **Step 2: Add static assertions for action gating**

Append this test next to the previous one:

```python
def test_xuanpin_mk_import_progress_waits_for_domain_save_before_next_actions(
    authed_client_no_db,
):
    resp = authed_client_no_db.get("/xuanpin/mk")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "function mkiImportProgressHideNextActions()" in body
    assert "function mkiImportProgressShowNextActions()" in body
    assert "mkiImportProgressHideNextActions();" in body
    assert "mkiImportProgressSetStep('domains', 'done', domainDetail);" in body
    assert "mkiImportProgressSetStep('next', 'done', '发布域名已确认，可以继续后续流程');" in body
```

- [ ] **Step 3: Run the static tests and confirm they fail**

Run:

```bash
pytest tests/test_xuanpin_routes.py::test_xuanpin_mk_import_progress_includes_publish_domain_step tests/test_xuanpin_routes.py::test_xuanpin_mk_import_progress_waits_for_domain_save_before_next_actions -q
```

Expected before implementation: FAIL because the new strings do not exist.

## Task 3: Implement Domain Selection In `mk_selection.html`

**Files:**
- Modify: `web/templates/mk_selection.html`

- [ ] **Step 1: Add CSS for the embedded domain panel**

Add CSS near the existing `.mki-progress-result` styles:

```css
.mki-progress-domains {
  margin-top: 14px; padding: 12px; border: 1px solid var(--oc-border);
  border-radius: var(--oc-r); background: #fff;
}
.mki-progress-domains[hidden] { display: none; }
.mki-progress-domain-head {
  display: flex; align-items: flex-start; justify-content: space-between; gap: 10px; margin-bottom: 10px;
}
.mki-progress-domain-title { font-size: 13px; font-weight: 800; color: var(--oc-fg); }
.mki-progress-domain-subtitle { margin-top: 3px; font-size: 12px; color: var(--oc-fg-muted); line-height: 1.45; }
.mki-progress-domain-list { display: flex; flex-direction: column; gap: 8px; }
.mki-progress-domain-row {
  display: flex; align-items: center; justify-content: space-between; gap: 10px;
  padding: 8px 10px; border: 1px solid var(--oc-border); border-radius: var(--oc-r); background: var(--oc-bg-muted);
}
.mki-progress-domain-row label { display: inline-flex; align-items: center; gap: 8px; min-width: 0; font-size: 13px; cursor: pointer; }
.mki-progress-domain-row input { margin: 0; }
.mki-progress-domain-name { overflow-wrap: anywhere; }
.mki-progress-domain-state {
  flex: 0 0 auto; padding: 2px 7px; border-radius: 999px; font-size: 12px;
  background: var(--oc-bg); color: var(--oc-fg-muted); border: 1px solid var(--oc-border);
}
.mki-progress-domain-state.active { background: var(--oc-success-bg); color: var(--oc-success-fg); border-color: transparent; }
.mki-progress-domain-state.disabled { background: oklch(95% .01 260); color: var(--oc-fg-muted); }
.mki-progress-domain-notice {
  margin-top: 10px; padding: 9px 10px; border-radius: var(--oc-r);
  font-size: 12px; line-height: 1.45; background: var(--oc-warning-bg); color: var(--oc-warning-fg);
}
.mki-progress-domain-notice.danger { background: oklch(97% .04 25); color: oklch(42% .14 25); }
.mki-progress-domain-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 12px; }
```

- [ ] **Step 2: Extend the progress step list**

Change `MKI_IMPORT_STEPS` to include the final domain selection step before `next`:

```javascript
const MKI_IMPORT_STEPS = [
  {key: 'prepare', title: '准备素材信息', detail: '等待读取明空卡片字段'},
  {key: 'product', title: '检查产品与链接', detail: '等待服务端创建或复用产品'},
  {key: 'download', title: '下载明空原视频', detail: '等待服务端下载原视频'},
  {key: 'store', title: '写入素材库', detail: '等待写入英文素材库'},
  {key: 'domains', title: '选择发布域名', detail: '入库完成后选择该产品要发布到哪些域名'},
  {key: 'next', title: '后续任务入口', detail: '发布域名确认后继续创建小语种任务'}
];
```

- [ ] **Step 3: Add modal state and helper functions**

Add these functions before `mkiImportProgressOpen(meta)`:

```javascript
let mkiImportProgressProductId = null;

function mkiImportProgressHeaders(extra = {}) {
  const headers = Object.assign({}, extra);
  const csrfMeta = document.querySelector('meta[name="csrf-token"]');
  if (csrfMeta) headers['X-CSRFToken'] = csrfMeta.getAttribute('content');
  return headers;
}

function mkiImportProgressHideNextActions() {
  ['mkiImportProgressContinue', 'mkiImportProgressTasks', 'mkiImportProgressMedias'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.hidden = true;
  });
}

function mkiImportProgressShowNextActions(btn) {
  const continueBtn = document.getElementById('mkiImportProgressContinue');
  const tasksBtn = document.getElementById('mkiImportProgressTasks');
  const mediasBtn = document.getElementById('mkiImportProgressMedias');
  if (continueBtn) continueBtn.hidden = false;
  if (tasksBtn) tasksBtn.hidden = false;
  mkiImportProgressSetMediasHref(btn || mkiImportProgressButton);
  if (mediasBtn) mediasBtn.hidden = false;
}
```

- [ ] **Step 4: Add domain rendering and save helpers**

Add these functions before `mkiImportProgressComplete(data, btn)`:

```javascript
function mkiImportProgressSetDomainNotice(message, kind = '') {
  const notice = document.getElementById('mkiImportProgressDomainNotice');
  if (!notice) return;
  notice.textContent = message || '';
  notice.classList.toggle('danger', kind === 'danger');
  notice.hidden = !message;
}

function mkiImportProgressRenderDomainRows(domains) {
  if (!Array.isArray(domains) || !domains.length) {
    return '<div class="mki-progress-domain-notice">暂无可用域名，请先到系统设置新增域名。</div>';
  }
  return domains.map(item => {
    const domainId = Number(item.id || 0);
    const globallyEnabled = !!item.enabled;
    const checked = !!item.product_enabled && globallyEnabled;
    const status = globallyEnabled
      ? (checked ? '<span class="mki-progress-domain-state active">已启用</span>' : '<span class="mki-progress-domain-state">未启用</span>')
      : '<span class="mki-progress-domain-state disabled">全局停用</span>';
    return `<div class="mki-progress-domain-row">
      <label>
        <input type="checkbox" value="${escapeHtml(domainId)}" data-mki-progress-domain-checkbox ${checked ? 'checked' : ''} ${globallyEnabled ? '' : 'disabled'}>
        <span class="mki-progress-domain-name">${escapeHtml(item.domain || '')}</span>
      </label>
      ${status}
    </div>`;
  }).join('');
}

function mkiImportProgressSelectedDomainIds() {
  return Array.from(document.querySelectorAll('[data-mki-progress-domain-checkbox]:checked'))
    .map(node => Number(node.value))
    .filter(value => Number.isFinite(value) && value > 0);
}

async function mkiImportProgressLoadDomains(productId) {
  const panel = document.getElementById('mkiImportProgressDomains');
  const list = document.getElementById('mkiImportProgressDomainList');
  const submit = document.getElementById('mkiImportProgressDomainSubmit');
  const retry = document.getElementById('mkiImportProgressDomainRetry');
  if (!panel || !list || !submit) return;
  panel.hidden = false;
  list.innerHTML = '<div class="mki-progress-domain-notice">正在加载域名...</div>';
  submit.disabled = true;
  if (retry) retry.hidden = true;
  mkiImportProgressSetDomainNotice('');
  try {
    const rsp = await fetch(`/medias/api/products/${encodeURIComponent(productId)}/product-link-domains`);
    const data = await rsp.json().catch(() => ({}));
    if (!rsp.ok) throw new Error(data.message || data.error || rsp.statusText);
    list.innerHTML = mkiImportProgressRenderDomainRows(data.domains || []);
    submit.disabled = false;
    mkiImportProgressSetStep('domains', 'running', '请确认该产品后续要发布到哪些域名');
  } catch (err) {
    const message = err.message || '域名加载失败';
    list.innerHTML = '';
    mkiImportProgressSetStep('domains', 'error', message);
    mkiImportProgressSetDomainNotice('域名加载失败：' + message, 'danger');
    if (retry) retry.hidden = false;
  }
}

async function mkiImportProgressSaveDomains() {
  const productId = mkiImportProgressProductId;
  const submit = document.getElementById('mkiImportProgressDomainSubmit');
  if (!productId || !submit) return;
  const enabledIds = mkiImportProgressSelectedDomainIds();
  submit.disabled = true;
  submit.textContent = '保存中...';
  mkiImportProgressSetDomainNotice(enabledIds.length ? '' : '当前未勾选任何发布域名，保存后该产品后续不会进入任何域名的推送。');
  try {
    const rsp = await fetch(`/medias/api/products/${encodeURIComponent(productId)}/product-link-domains`, {
      method: 'POST',
      headers: mkiImportProgressHeaders({'Content-Type': 'application/json'}),
      body: JSON.stringify({enabled_domain_ids: enabledIds}),
    });
    const data = await rsp.json().catch(() => ({}));
    if (!rsp.ok) throw new Error(data.message || data.error || rsp.statusText);
    const list = document.getElementById('mkiImportProgressDomainList');
    if (list) list.innerHTML = mkiImportProgressRenderDomainRows(data.domains || []);
    const domainDetail = enabledIds.length
      ? `已同步 ${enabledIds.length} 个发布域名到链接管理`
      : '已同步为空发布域名配置';
    mkiImportProgressSetStep('domains', 'done', domainDetail);
    mkiImportProgressSetStep('next', 'done', '发布域名已确认，可以继续后续流程');
    mkiImportProgressShowNextActions();
    mkiImportProgressSetDomainNotice(enabledIds.length ? '发布域名已同步到产品链接管理。' : '已保存为空发布域名配置。');
  } catch (err) {
    const message = err.message || '保存失败';
    mkiImportProgressSetStep('domains', 'error', message);
    mkiImportProgressSetDomainNotice('发布域名保存失败：' + message, 'danger');
  } finally {
    submit.disabled = false;
    submit.textContent = '确定发布域名';
  }
}

function mkiImportProgressRetryDomains() {
  if (mkiImportProgressProductId) mkiImportProgressLoadDomains(mkiImportProgressProductId);
}
```

- [ ] **Step 5: Reset domain state when opening or failing the modal**

Inside `mkiImportProgressOpen(meta)`, reset the product id, hide the domain panel, and use `mkiImportProgressHideNextActions()`:

```javascript
mkiImportProgressProductId = null;
mkiImportProgressHideNextActions();
const domainsPanel = document.getElementById('mkiImportProgressDomains');
if (domainsPanel) domainsPanel.hidden = true;
const domainList = document.getElementById('mkiImportProgressDomainList');
if (domainList) domainList.innerHTML = '';
mkiImportProgressSetDomainNotice('');
```

Inside `mkiImportProgressFail(message)`, add:

```javascript
mkiImportProgressHideNextActions();
const domainsPanel = document.getElementById('mkiImportProgressDomains');
if (domainsPanel) domainsPanel.hidden = true;
```

- [ ] **Step 6: Gate completion on domain save**

In `mkiImportProgressComplete(data, btn)`, set store done, set `domains` running, set `next` pending, show the result text, keep follow-up actions hidden, then load the domains:

```javascript
const numericProductId = Number(data?.media_product_id || data?.product_id || 0);
mkiImportProgressProductId = numericProductId > 0 ? numericProductId : null;
mkiImportProgressSetStep('store', 'done', '英文素材已写入素材库，素材 ID：' + itemId);
mkiImportProgressSetStep('domains', 'running', '正在加载产品发布域名');
mkiImportProgressSetStep('next', 'pending', '确认发布域名后开放下一步入口');
mkiImportProgressHideNextActions();
mkiImportProgressButton = btn || mkiImportProgressButton;
if (mkiImportProgressProductId) {
  mkiImportProgressLoadDomains(mkiImportProgressProductId);
} else {
  mkiImportProgressSetStep('domains', 'error', '服务端未返回产品 ID，无法配置发布域名');
  mkiImportProgressSetDomainNotice('服务端未返回产品 ID，请去素材管理手动确认链接管理。', 'danger');
}
```

- [ ] **Step 7: Add the modal domain panel HTML**

Add this block after `mkiImportProgressResult` and before `.mki-progress-actions`:

```html
<div id="mkiImportProgressDomains" class="mki-progress-domains" hidden>
  <div class="mki-progress-domain-head">
    <div>
      <div class="mki-progress-domain-title">选择发布域名</div>
      <div class="mki-progress-domain-subtitle">保存后会同步到该产品的链接管理，后续推送只按启用域名执行。</div>
    </div>
  </div>
  <div id="mkiImportProgressDomainList" class="mki-progress-domain-list"></div>
  <div id="mkiImportProgressDomainNotice" class="mki-progress-domain-notice" hidden></div>
  <div class="mki-progress-domain-actions">
    <button id="mkiImportProgressDomainRetry" type="button" class="oc-btn oc-btn--ghost" onclick="mkiImportProgressRetryDomains()" hidden>重新加载域名</button>
    <button id="mkiImportProgressDomainSubmit" type="button" class="oc-btn oc-btn--primary" onclick="mkiImportProgressSaveDomains()">确定发布域名</button>
  </div>
</div>
```

- [ ] **Step 8: Run the static tests**

Run:

```bash
pytest tests/test_xuanpin_routes.py::test_xuanpin_mk_import_progress_includes_publish_domain_step tests/test_xuanpin_routes.py::test_xuanpin_mk_import_progress_waits_for_domain_save_before_next_actions -q
```

Expected: PASS.

- [ ] **Step 9: Commit the frontend change**

Run:

```bash
git add web/templates/mk_selection.html tests/test_xuanpin_routes.py
git commit -m "feat(mk): choose product domains after import"
```

## Task 4: Focused Regression

**Files:**
- No source edits unless a regression fails.

- [ ] **Step 1: Run focused route and import tests**

Run:

```bash
pytest tests/test_xuanpin_routes.py tests/test_media_product_link_domains_routes.py tests/test_mk_selection_routes.py tests/test_mk_import_routes.py -q
```

Expected: PASS.

- [ ] **Step 2: Run appcore link-domain tests**

Run:

```bash
pytest tests/test_product_link_domains.py tests/test_appcore_shopify_image_tasks.py::test_evaluate_candidate_returns_all_enabled_domain_link_urls tests/test_shopify_image_worker_loop.py::test_run_worker_once_runs_each_link_domain_with_selected_domain -q
```

Expected: PASS.

- [ ] **Step 3: Check formatting whitespace**

Run:

```bash
git diff --check
```

Expected: no whitespace errors. CRLF warnings are acceptable in this repo on Windows.

- [ ] **Step 4: Final commit if fixes were needed**

If Task 4 required edits, commit them:

```bash
git add <changed-files>
git commit -m "fix(mk): stabilize import domain selection"
```

## Self-Review

- Spec coverage: The plan covers domain list display, default checked state via existing `product_enabled`, save as last step, sync to link management, no new backend contract, empty domain selection, errors, and follow-up button gating.
- Placeholder scan: No placeholder markers or undefined future step remains.
- Type consistency: Product id is numeric in modal state, domain ids are numeric arrays, and API body uses existing `enabled_domain_ids`.
