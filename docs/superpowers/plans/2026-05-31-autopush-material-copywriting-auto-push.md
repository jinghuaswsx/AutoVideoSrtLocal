# AutoPush Material Copywriting Auto Push Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert the AutoPush material push modal into a visible two-stage workflow that pushes material first and automatically pushes localized copywriting after an MKID is available.

**Architecture:** Keep orchestration in the existing AutoPush browser frontend so each request and response can be shown live. Reuse the existing FastAPI endpoints and manual localized text tab; add small UI helpers inside `AutoPush/static/app.js` rather than introducing new modules.

**Tech Stack:** Vanilla JavaScript module, CSS, FastAPI proxy endpoints, pytest static asset tests.

---

## File Structure

- Modify `tests/test_autopush_ui_assets.py`: add static assertions for the new automatic workflow, manual tab preservation, and retry state labels.
- Modify `AutoPush/static/app.js`: add workflow state helpers, two-column workflow panes, log rendering, MKID extraction, and automatic copywriting push after material success.
- Modify `AutoPush/static/app.css`: add two-column modal layout, step cards, status chips, and scrollable log blocks.
- Reference `docs/superpowers/specs/2026-05-30-autopush-material-copywriting-auto-push-design.md` as the docs anchor for all behavior changes.

## Task 1: Static Regression Tests

**Files:**
- Modify: `tests/test_autopush_ui_assets.py`
- Test: `tests/test_autopush_ui_assets.py`

- [ ] **Step 1: Add failing tests for workflow markers**

Append these tests to `tests/test_autopush_ui_assets.py`:

```python
def test_autopush_material_push_starts_copywriting_workflow_after_mkid():
    assert "推送素材并自动推文案" in SCRIPT
    assert "function runMaterialAndCopywritingWorkflow" in SCRIPT
    assert "async function pushMaterialStep" in SCRIPT
    assert "async function pushCopywritingStep" in SCRIPT
    assert "const mkId = resolveWorkflowMkId" in SCRIPT
    assert SCRIPT.index("await pushMaterialStep()") < SCRIPT.index("await pushCopywritingStep()")


def test_autopush_workflow_logs_requests_and_responses():
    assert "function appendWorkflowLog" in SCRIPT
    assert "素材推送请求" in SCRIPT
    assert "素材推送响应" in SCRIPT
    assert "小语种文案推送请求" in SCRIPT
    assert "小语种文案推送响应" in SCRIPT
    assert "ap-workflow-log" in SCRIPT


def test_autopush_manual_localized_text_tabs_remain_available():
    assert "推送小语种文案" in SCRIPT
    assert "小语种文案JSON预览" in SCRIPT
    assert "retryCopywritingOnly" in SCRIPT
    assert "重试文案推送" in SCRIPT
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
pytest tests/test_autopush_ui_assets.py -q
```

Expected: the new tests fail because `runMaterialAndCopywritingWorkflow`, workflow logs, and retry state strings do not exist yet.

## Task 2: Frontend Workflow Implementation

**Files:**
- Modify: `AutoPush/static/app.js`
- Test: `tests/test_autopush_ui_assets.py`

- [ ] **Step 1: Add workflow helper functions near localized request helpers**

Add helpers after `buildLocalizedPushRequest`:

```javascript
function formatWorkflowDetail(detail) {
  if (detail === undefined || detail === null || detail === "") return "";
  if (typeof detail === "string") return detail;
  return JSON.stringify(detail, null, 2);
}

function resolveWorkflowMkId(pushContext, materialResponse) {
  const candidates = [
    pushContext?.mkId,
    materialResponse?.mk_id,
    materialResponse?.mkId,
    materialResponse?.data?.mk_id,
    materialResponse?.data?.mkId,
    materialResponse?.upstream?.mk_id,
    materialResponse?.upstream?.mkId,
    materialResponse?.upstream?.data?.mk_id,
    materialResponse?.upstream?.data?.mkId,
  ];
  const value = candidates.find((candidate) => candidate !== null && candidate !== undefined && String(candidate).trim());
  return value === undefined ? null : value;
}
```

- [ ] **Step 2: Replace the single response block with workflow panes**

Inside `openPushModal`, keep the existing manual panes and create two workflow columns in the confirm pane:

```javascript
  const confirmPane = el("div", { class: "ap-modal-pane" });
  const workflowGrid = el("div", { class: "ap-workflow-grid" });
  const materialWorkflow = createWorkflowPanel("material", "素材推送", "等待载荷加载");
  const copywritingWorkflow = createWorkflowPanel("copywriting", "文案推送", "等待 MKID");
  workflowGrid.appendChild(materialWorkflow.root);
  workflowGrid.appendChild(copywritingWorkflow.root);
  confirmPane.appendChild(workflowGrid);
  const payloadBox = materialWorkflow.preview;
```

Define `createWorkflowPanel` inside `openPushModal` before the async loader:

```javascript
  function createWorkflowPanel(kind, title, initialStatus) {
    const status = el("span", { class: "ap-workflow-status waiting" }, initialStatus);
    const preview = el("div", { class: "ap-workflow-preview" });
    const log = el("div", { class: "ap-workflow-log" });
    const root = el("section", { class: `ap-workflow-panel ${kind}` }, [
      el("div", { class: "ap-workflow-head" }, [
        el("h4", {}, title),
        status,
      ]),
      preview,
      log,
    ]);
    return { root, status, preview, log };
  }
```

- [ ] **Step 3: Add status and log functions inside `openPushModal`**

Add these functions after `showResponse`:

```javascript
  function setWorkflowStatus(panel, state, text) {
    panel.status.className = `ap-workflow-status ${state}`;
    panel.status.textContent = text;
  }

  function appendWorkflowLog(panel, direction, title, detail) {
    const row = el("div", { class: `ap-workflow-log-row ${direction}` });
    row.appendChild(el("div", { class: "ap-workflow-log-meta" }, [
      el("span", {}, new Date().toLocaleTimeString("zh-CN", { hour12: false })),
      el("strong", {}, title),
    ]));
    const formatted = formatWorkflowDetail(detail);
    if (formatted) {
      row.appendChild(el("pre", { class: "ap-workflow-log-body" }, formatted));
    }
    panel.log.appendChild(row);
    panel.log.scrollTop = panel.log.scrollHeight;
  }

  function renderCopywritingPreview() {
    clear(copywritingWorkflow.preview);
    copywritingWorkflow.preview.appendChild(renderLocalizedTargetInfo(
      pushContext.mkId,
      pushContext.localizedTargetUrl,
    ));
    copywritingWorkflow.preview.appendChild(renderLocalizedTextView(buildLocalizedDisplayTexts(pushContext)));
  }
```

- [ ] **Step 4: Update load completion to populate both columns**

After payload load succeeds, replace the old `payloadBox.appendChild(renderPayloadView(payload)); renderLocalizedPane();` block with:

```javascript
      clear(payloadBox);
      payloadBox.appendChild(renderPayloadView(payload));
      setWorkflowStatus(materialWorkflow, "ready", "素材载荷已就绪");
      setWorkflowStatus(
        copywritingWorkflow,
        pushContext.mkId ? "ready" : "waiting",
        pushContext.mkId ? "文案载荷已就绪" : "等待 MKID",
      );
      renderCopywritingPreview();
      renderLocalizedPane();
```

- [ ] **Step 5: Replace the button click handler with pipeline functions**

Replace the existing `btnPush.addEventListener("click", async () => { ... });` body with named workflow functions:

```javascript
  async function pushMaterialStep() {
    const errs = validatePayload(pushContext.payload);
    if (errs.length > 0) {
      setWorkflowStatus(materialWorkflow, "error", "素材校验失败");
      appendWorkflowLog(materialWorkflow, "error", "素材 payload 校验失败", { details: errs });
      throw Object.assign(new Error("素材 payload 校验失败"), { payload: { details: errs } });
    }
    setWorkflowStatus(materialWorkflow, "running", "素材推送中");
    appendWorkflowLog(materialWorkflow, "request", "素材推送请求", {
      url: pushContext.itemId ? `/api/push-items/${pushContext.itemId}/push` : "/api/push/medias",
      body: pushContext.payload,
    });
    const body = pushContext.itemId
      ? await api.pushItem(pushContext.itemId, pushContext.payload)
      : await api.push(pushContext.payload);
    appendWorkflowLog(materialWorkflow, "response", "素材推送响应", body);
    showResponse(body, false, "素材推送响应");
    materialPushed = true;
    anyPushSucceeded = true;
    setWorkflowStatus(materialWorkflow, body.writeback_error ? "warning" : "success", body.writeback_error ? "素材已推送，写回告警" : "素材推送成功");
    return body;
  }

  async function pushCopywritingStep(materialResponse = null) {
    const mkId = resolveWorkflowMkId(pushContext, materialResponse);
    if (mkId) {
      pushContext.mkId = mkId;
      pushContext.localizedTargetUrl = buildLocalizedPushTargetUrl(mkId);
      mkIdValue.textContent = String(mkId || "-");
      localizedJsonPre.textContent = JSON.stringify(buildLocalizedRequestPreview(pushContext), null, 2);
      renderCopywritingPreview();
      renderLocalizedPane();
    }
    const localizedError = getLocalizedTextError();
    if (localizedError) {
      setWorkflowStatus(copywritingWorkflow, "blocked", localizedError);
      appendWorkflowLog(copywritingWorkflow, "info", "文案推送未启动", { message: localizedError });
      return { skipped: true, message: localizedError };
    }
    const body = buildLocalizedPushRequest(pushContext);
    setWorkflowStatus(copywritingWorkflow, "running", "文案推送中");
    appendWorkflowLog(copywritingWorkflow, "request", "小语种文案推送请求", {
      url: `/api/marketing/medias/${pushContext.mkId}/texts`,
      target_url: pushContext.localizedTargetUrl,
      body,
    });
    const response = await api.pushLocalizedTexts(pushContext.mkId, body);
    appendWorkflowLog(copywritingWorkflow, "response", "小语种文案推送响应", response);
    showResponse(response, false, "小语种文案推送响应");
    localizedTextPushed = true;
    anyPushSucceeded = true;
    setWorkflowStatus(copywritingWorkflow, "success", "文案推送成功");
    return response;
  }

  async function runMaterialAndCopywritingWorkflow() {
    const materialResponse = await pushMaterialStep();
    await pushCopywritingStep(materialResponse);
  }

  async function retryCopywritingOnly() {
    await pushCopywritingStep();
  }
```

Then wire the button:

```javascript
  btnPush.addEventListener("click", async () => {
    if (!pushContext.payload) return;
    btnPush.disabled = true;
    btnPush.textContent = "执行中...";
    btnCancel.disabled = true;
    try {
      if (isLocalizedMode()) {
        await retryCopywritingOnly();
      } else if (materialPushed && !localizedTextPushed) {
        await retryCopywritingOnly();
      } else {
        await runMaterialAndCopywritingWorkflow();
      }
    } catch (err) {
      const localizedFailure = materialPushed && !localizedTextPushed;
      showResponse(
        err.payload || { message: err.message },
        true,
        localizedFailure ? "小语种文案推送响应" : "素材推送响应",
      );
      setWorkflowStatus(
        localizedFailure ? copywritingWorkflow : materialWorkflow,
        "error",
        err.message || "推送失败",
      );
      if (localizedFailure) {
        appendWorkflowLog(copywritingWorkflow, "error", "小语种文案推送失败", err.payload || { message: err.message });
      } else {
        appendWorkflowLog(materialWorkflow, "error", "素材推送失败", err.payload || { message: err.message });
      }
    } finally {
      btnCancel.disabled = false;
      syncPushButton();
    }
  });
```

- [ ] **Step 6: Update `syncPushButton` labels**

Change `syncPushButton` so confirm mode labels match workflow states:

```javascript
    if (materialPushed && !localizedTextPushed) {
      const localizedError = getLocalizedTextError();
      btnPush.disabled = Boolean(localizedError);
      btnPush.textContent = localizedError ? "文案无法推送" : "重试文案推送";
      return;
    }
    btnPush.disabled = materialPushed || !pushContext.payload;
    btnPush.textContent = materialPushed ? "已完成" : "推送素材并自动推文案";
```

- [ ] **Step 7: Run tests and verify GREEN**

Run:

```bash
pytest tests/test_autopush_ui_assets.py -q
```

Expected: all tests in `tests/test_autopush_ui_assets.py` pass.

## Task 3: Workflow Styles

**Files:**
- Modify: `AutoPush/static/app.css`
- Test: `tests/test_autopush_ui_assets.py`

- [ ] **Step 1: Add CSS for the workflow panes**

Append near the modal styles:

```css
.ap-workflow-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: var(--oc-sp-4);
  align-items: start;
}
.ap-workflow-panel {
  border: 1px solid var(--oc-border);
  border-radius: var(--oc-r-lg);
  background: var(--oc-bg);
  min-width: 0;
  overflow: hidden;
}
.ap-workflow-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--oc-sp-3);
  padding: var(--oc-sp-3) var(--oc-sp-4);
  border-bottom: 1px solid var(--oc-border);
  background: var(--oc-bg-subtle);
}
.ap-workflow-head h4 {
  margin: 0;
  font-size: 13px;
  color: var(--oc-fg);
  letter-spacing: 0;
  text-transform: none;
}
.ap-workflow-status {
  flex: none;
  border-radius: var(--oc-r-full);
  padding: 2px 8px;
  font-size: 12px;
  font-weight: 500;
  background: var(--oc-bg-muted);
  color: var(--oc-fg-muted);
}
.ap-workflow-status.ready,
.ap-workflow-status.running {
  background: var(--oc-accent-subtle);
  color: var(--oc-accent);
}
.ap-workflow-status.success {
  background: var(--oc-success-bg);
  color: var(--oc-success-fg);
}
.ap-workflow-status.warning,
.ap-workflow-status.blocked,
.ap-workflow-status.error {
  background: var(--oc-danger-bg);
  color: var(--oc-danger-fg);
}
.ap-workflow-preview {
  padding: var(--oc-sp-4);
  border-bottom: 1px solid var(--oc-border);
}
.ap-workflow-log {
  max-height: 340px;
  overflow: auto;
  padding: var(--oc-sp-3);
  display: grid;
  gap: var(--oc-sp-3);
  background: var(--oc-bg-subtle);
}
.ap-workflow-log-row {
  border: 1px solid var(--oc-border);
  border-radius: var(--oc-r);
  background: var(--oc-bg);
  overflow: hidden;
}
.ap-workflow-log-row.error {
  border-color: var(--oc-danger);
}
.ap-workflow-log-meta {
  display: flex;
  justify-content: space-between;
  gap: var(--oc-sp-3);
  padding: var(--oc-sp-2) var(--oc-sp-3);
  color: var(--oc-fg-muted);
  font-size: 12px;
  border-bottom: 1px solid var(--oc-border);
}
.ap-workflow-log-body {
  margin: 0;
  padding: var(--oc-sp-3);
  max-height: 220px;
  overflow: auto;
  white-space: pre;
  font-family: "JetBrains Mono", Consolas, monospace;
  font-size: 12px;
  line-height: 1.5;
}
@media (max-width: 900px) {
  .ap-modal {
    max-width: 96vw;
  }
  .ap-workflow-grid {
    grid-template-columns: 1fr;
  }
}
```

- [ ] **Step 2: Run focused tests**

Run:

```bash
pytest tests/test_autopush_ui_assets.py -q
```

Expected: tests still pass.

## Task 4: Full Verification

**Files:**
- Verify only.

- [ ] **Step 1: Run AutoPush tests**

Run:

```bash
pytest tests/test_autopush_ui_assets.py tests/test_autopush_routes.py -q
```

Expected: all selected tests pass.

- [ ] **Step 2: Inspect final diff**

Run:

```bash
git diff -- AutoPush/static/app.js AutoPush/static/app.css tests/test_autopush_ui_assets.py docs/superpowers/specs/2026-05-30-autopush-material-copywriting-auto-push-design.md docs/superpowers/plans/2026-05-31-autopush-material-copywriting-auto-push.md
```

Expected: diff only contains the new spec, new plan, AutoPush workflow UI, styles, and focused static tests.

- [ ] **Step 3: Optional commit when requested**

If committing this branch, use:

```bash
git add docs/superpowers/specs/2026-05-30-autopush-material-copywriting-auto-push-design.md docs/superpowers/plans/2026-05-31-autopush-material-copywriting-auto-push.md AutoPush/static/app.js AutoPush/static/app.css tests/test_autopush_ui_assets.py
git commit -m "feat(autopush): auto push copywriting after mkid" -m "Docs-anchor: docs/superpowers/specs/2026-05-30-autopush-material-copywriting-auto-push-design.md"
```
