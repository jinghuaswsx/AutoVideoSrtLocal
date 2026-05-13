# Omni AV Sync Audit Card Red Highlight Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Highlight problematic Omni audio-visual sync audit scorecard rows in red and make the diagnosis cell text red.

**Architecture:** Keep the change frontend-only. Add a small predicate in `_task_workbench_scripts.html` that classifies scorecard rows using the spec contract, apply row and diagnosis-cell classes in the existing table-only renderer, and style those classes in `_task_workbench_styles.html`.

**Tech Stack:** Flask/Jinja templates, vanilla JavaScript embedded in `_task_workbench_scripts.html`, CSS embedded in `_task_workbench_styles.html`, pytest asset tests.

---

### Task 1: Asset Test For Problem Row Highlighting

**Files:**
- Modify: `tests/test_prompt_inspector_assets.py`
- Test: `tests/test_prompt_inspector_assets.py`

- [ ] **Step 1: Write the failing test**

Add these assertions to `test_multi_av_sync_audit_renderer_is_table_only`:

```python
    assert "function isAvSyncAuditProblemRow" in scripts
    assert "av-sync-timeline-row table-only ${isIssue ? \"is-issue\" : \"\"}" in scripts
    assert "diagnosis-field ${isIssue ? \"is-issue\" : \"\"}" in scripts
    assert ".av-sync-timeline-field.diagnosis-field.is-issue" in styles
```

Read `styles` near the top of the test:

```python
    styles = (ROOT / "web/templates/_task_workbench_styles.html").read_text(encoding="utf-8")
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
pytest tests/test_prompt_inspector_assets.py::test_multi_av_sync_audit_renderer_is_table_only -q
```

Expected: FAIL because the predicate and diagnosis-cell class do not exist yet.

- [ ] **Step 3: Implement the minimal renderer and style changes**

In `web/templates/_task_workbench_scripts.html`, inside `renderAvSyncAuditTimelineRow`, compute:

```javascript
      const isIssue = isAvSyncAuditProblemRow(row);
```

Use the row class:

```javascript
      return `<div class="av-sync-timeline-row table-only ${isIssue ? "is-issue" : ""}">
```

Use a diagnosis-cell class:

```javascript
          ${renderAvSyncTimelineField("问题诊断", avSyncTableDiagnosis(row), `diagnosis-field ${isIssue ? "is-issue" : ""}`)}
```

Change `renderAvSyncTimelineField` to accept an optional class name:

```javascript
  function renderAvSyncTimelineField(label, value, extraClass = "") {
    const className = ["av-sync-timeline-field", extraClass].filter(Boolean).join(" ");
    return `<div class="${escapeAttr(className)}">
      <div class="preview-label">${escapeHtml(label)}</div>
      <div class="preview-original">${escapeHtml(value || "-")}</div>
    </div>`;
  }
```

Add:

```javascript
  function isAvSyncAuditProblemRow(row) {
    if (!row || typeof row !== "object") return false;
    if (String(row.diagnosis_status || "").toLowerCase() === "issue") return true;
    const numericScore = Number(row.sync_score ?? row.score);
    if (Number.isFinite(numericScore) && numericScore < 90) return true;
    const recommendation = String(row.recommendation || "").trim();
    return Boolean(recommendation && recommendation !== "无需调整。");
  }
```

In `web/templates/_task_workbench_styles.html`, add:

```css
.av-sync-timeline-field.diagnosis-field.is-issue {
  border-color: #fca5a5;
  background: rgba(248, 113, 113, 0.08);
}
.av-sync-timeline-field.diagnosis-field.is-issue .preview-label,
.av-sync-timeline-field.diagnosis-field.is-issue .preview-original {
  color: #b91c1c;
}
```

- [ ] **Step 4: Run focused tests**

Run:

```bash
pytest tests/test_prompt_inspector_assets.py::test_multi_av_sync_audit_renderer_is_table_only tests/test_prompt_inspector_assets.py::test_omni_av_sync_audit_renderer_exposes_chinese_findings -q
```

Expected: PASS.

- [ ] **Step 5: Run broader smoke tests**

Run:

```bash
pytest tests/test_prompt_inspector_assets.py tests/test_asr_normalize_render_smoke.py -q
```

Expected: PASS.
