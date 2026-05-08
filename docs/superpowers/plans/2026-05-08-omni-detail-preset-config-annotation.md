# Omni Detail Preset Config Annotation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `/omni-translate/<id>` 顶部工具条显示当前任务实际使用的 `plugin_config` preset 摘要。

**Architecture:** 路由层生成只读展示 payload，模板只负责渲染。展示数据优先来自任务 `state.plugin_config` 快照，缺失时沿用 omni 运行时默认兜底。样式复用 `_task_workbench_styles.html` 的 Ocean Blue token。

**Tech Stack:** Flask/Jinja2, pytest, existing `appcore.omni_plugin_config` validator.

---

### Task 1: Route Display Payload

**Files:**
- Modify: `web/routes/omni_translate.py`
- Test: `tests/test_omni_translate_routes.py`

- [ ] **Step 1: Write failing tests**

Add tests that call `web.routes.omni_translate._build_plugin_config_annotation()` directly:

```python
def test_build_plugin_config_annotation_names_omni_current():
    from web.routes.omni_translate import _build_plugin_config_annotation
    annotation = _build_plugin_config_annotation("t-1", {"plugin_config": CFG_ASR_CLEAN})
    assert annotation["name"] == "omni-current"
    assert annotation["source"] == "snapshot"
    assert "ASR 原样清洗" in annotation["summary"]

def test_build_plugin_config_annotation_marks_custom_config():
    from web.routes.omni_translate import _build_plugin_config_annotation
    cfg = {**CFG_ASR_CLEAN, "voice_separation": False, "loudness_match": False}
    annotation = _build_plugin_config_annotation("t-1", {"plugin_config": cfg})
    assert annotation["name"] == "自定义配置"
    assert "人声分离关闭" in annotation["summary"]
```

- [ ] **Step 2: Verify RED**

Run:

```bash
pytest tests/test_omni_translate_routes.py::test_build_plugin_config_annotation_names_omni_current tests/test_omni_translate_routes.py::test_build_plugin_config_annotation_marks_custom_config -q
```

Expected: fails because `_build_plugin_config_annotation` is missing.

- [ ] **Step 3: Implement helper**

Add a small helper near existing omni route helpers. It validates config, matches four built-in baselines, and returns `{name, source, summary}`.

- [ ] **Step 4: Pass payload to template**

In `detail()`, pass `plugin_config_annotation=_build_plugin_config_annotation(task_id, state)`.

### Task 2: Template And Style

**Files:**
- Modify: `web/templates/_translate_detail_shell.html`
- Modify: `web/templates/_task_workbench_styles.html`
- Test: `tests/test_translate_detail_shell_templates.py`

- [ ] **Step 1: Write failing template tests**

Assert the shared shell contains `omni-preset-summary` and uses `plugin_config_annotation`.

- [ ] **Step 2: Verify RED**

Run:

```bash
pytest tests/test_translate_detail_shell_templates.py::test_omni_detail_shell_contains_preset_summary_slot -q
```

Expected: fails because the slot is missing.

- [ ] **Step 3: Render summary**

Inside `.detail-topbar`, after `.source-lang-picker`, render the annotation only when it is defined.

- [ ] **Step 4: Add Ocean Blue styles**

Add compact flex styles for `.omni-preset-summary`, using OKLCH hue 200-240 and existing token variables.

### Task 3: Verification

**Files:**
- No production edits.

- [ ] Run focused tests:

```bash
pytest tests/test_omni_translate_routes.py tests/test_translate_detail_shell_templates.py tests/test_runtime_omni_dispatch.py -q
```

- [ ] Confirm no local MySQL was used.
