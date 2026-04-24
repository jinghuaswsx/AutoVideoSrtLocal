# OpenRouter GPT 5-mini Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `GPT 5-mini` 作为 OpenRouter 下的一个可选翻译本土化模型接入现有主线与任务工作台。

**Architecture:** 保持现有 OpenRouter 通道不变，只新增一个内部 provider key `gpt_5_mini`，并把它接到翻译模型映射、主线偏好白名单和两个 UI 入口。所有调用仍走 `pipeline.translate.resolve_provider_config()`，继续复用 OpenRouter Key 与 billing。

**Tech Stack:** Python, Flask, Jinja2, pytest

---

### Task 1: 补模型映射与白名单

**Files:**
- Modify: `pipeline/translate.py`
- Modify: `appcore/runtime.py`
- Modify: `web/routes/settings.py`
- Test: `tests/test_translate_use_case_binding.py`

- [ ] **Step 1: 写失败测试，锁定新 provider key 能被接受**

```python
def test_get_model_display_name_supports_openrouter_gpt_5_mini(monkeypatch):
    monkeypatch.setattr("pipeline.translate.OPENROUTER_API_KEY", "test-key")
```

- [ ] **Step 2: 运行相关测试确认当前不支持**

Run: `pytest tests/test_translate_use_case_binding.py -q`
Expected: 需要新增断言后失败，表现为模型名未映射或白名单不接受 `gpt_5_mini`

- [ ] **Step 3: 最小实现**

```python
_OPENROUTER_PREF_MODELS["gpt_5_mini"] = "openai/gpt-5-mini"
_VALID_TRANSLATE_PREFS = (..., "gpt_5_mini", ...)
TRANSLATE_PROVIDERS = [..., "gpt_5_mini", ...]
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_translate_use_case_binding.py tests/test_settings_routes_new.py -q`
Expected: PASS

- [ ] **Step 5: 继续下一任务**

```bash
# 本任务不单独提交，和 UI 一起验证后再统一收口
```

### Task 2: 补设置页与任务工作台选项

**Files:**
- Modify: `web/templates/settings.html`
- Modify: `web/templates/_task_workbench.html`
- Test: `tests/test_settings_routes_new.py`
- Test: `tests/test_task_routes.py`

- [ ] **Step 1: 写失败测试，锁定页面中出现新选项**

```python
assert "GPT 5-mini" in body
```

- [ ] **Step 2: 运行测试确认当前页面还没有这个选项**

Run: `pytest tests/test_settings_routes_new.py tests/test_task_routes.py -q`
Expected: FAIL，页面文本中找不到 `GPT 5-mini`

- [ ] **Step 3: 最小实现**

```html
<option value="gpt_5_mini" data-group="openrouter">GPT 5-mini</option>
<option value="gpt_5_mini">GPT 5-mini (OpenRouter)</option>
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_settings_routes_new.py tests/test_task_routes.py -q`
Expected: PASS

- [ ] **Step 5: 运行回归验证**

Run: `pytest tests/test_localization.py tests/test_translate_use_case_binding.py tests/test_settings_routes_new.py tests/test_task_routes.py -q`
Expected: PASS
