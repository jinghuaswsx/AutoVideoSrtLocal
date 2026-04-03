# 豆包翻译模型接入 & 配置页面按流程重组 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 接入豆包 2.0 Pro 作为可选翻译模型，配置页面按流程步骤重组，用户可在设置页和任务工作台选择翻译模型。

**Architecture:** 复用 OpenAI SDK 兼容接口调用豆包，`translate.py` 的 `_get_client()` 根据 provider 参数切换 base_url/api_key/model。配置页面从按模型分块改为按 pipeline 步骤分块。用户偏好和新增 key 存储在现有 `api_keys` 表中。

**Tech Stack:** Python / Flask / OpenAI SDK / Jinja2 / MySQL

---

### Task 1: config.py 新增豆包翻译默认常量

**Files:**
- Modify: `config.py:58-61`

- [ ] **Step 1: 在 config.py 中 OpenRouter 配置块后新增豆包翻译常量**

在 `config.py` 的 `# OpenRouter Claude` 块之后添加：

```python
# 豆包翻译 (火山引擎 ARK)
DOUBAO_LLM_API_KEY = _env("DOUBAO_LLM_API_KEY")
DOUBAO_LLM_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
DOUBAO_LLM_MODEL = _env("DOUBAO_LLM_MODEL", "doubao-seed-2-0-pro-260215")
```

- [ ] **Step 2: 提交**

```bash
git add config.py
git commit -m "feat: 新增豆包翻译模型默认配置常量"
```

---

### Task 2: translate.py 改造 — 支持多 provider

**Files:**
- Modify: `pipeline/translate.py`

- [ ] **Step 1: 重写 `_get_client` 和废弃 `_model_name`**

将 `pipeline/translate.py` 的头部 imports 和 `_get_client`、`_model_name` 替换为：

```python
import json
from typing import Dict, List

from openai import OpenAI

from config import (
    CLAUDE_MODEL,
    DOUBAO_LLM_API_KEY,
    DOUBAO_LLM_BASE_URL,
    DOUBAO_LLM_MODEL,
    OPENROUTER_API_KEY,
    OPENROUTER_BASE_URL,
)
from pipeline.localization import (
    LOCALIZED_TRANSLATION_RESPONSE_FORMAT,
    TTS_SCRIPT_RESPONSE_FORMAT,
    build_localized_translation_messages,
    build_tts_script_messages,
    validate_localized_translation,
    validate_tts_script,
)


def _resolve_provider_config(
    provider: str,
    user_id: int | None = None,
    api_key_override: str | None = None,
) -> tuple[OpenAI, str]:
    """Return (client, model_id) for the given provider."""
    from appcore.api_keys import resolve_extra, resolve_key

    if provider == "doubao":
        key = api_key_override or (
            resolve_key(user_id, "doubao_llm", "DOUBAO_LLM_API_KEY") if user_id else DOUBAO_LLM_API_KEY
        )
        extra = resolve_extra(user_id, "doubao_llm") if user_id else {}
        base_url = extra.get("base_url") or DOUBAO_LLM_BASE_URL
        model = extra.get("model_id") or DOUBAO_LLM_MODEL
    else:  # openrouter
        key = api_key_override or (
            resolve_key(user_id, "openrouter", "OPENROUTER_API_KEY") if user_id else OPENROUTER_API_KEY
        )
        extra = resolve_extra(user_id, "openrouter") if user_id else {}
        base_url = extra.get("base_url") or OPENROUTER_BASE_URL
        model = extra.get("model_id") or CLAUDE_MODEL

    return OpenAI(api_key=key, base_url=base_url), model
```

删除旧的 `_client` 全局变量、`_get_client()` 函数和 `_model_name()` 函数。

- [ ] **Step 2: 重写 `generate_localized_translation` 签名和实现**

```python
def generate_localized_translation(
    source_full_text_zh: str,
    script_segments: list[dict],
    variant: str = "normal",
    custom_system_prompt: str | None = None,
    *,
    provider: str = "openrouter",
    user_id: int | None = None,
    openrouter_api_key: str | None = None,
) -> dict:
    client, model = _resolve_provider_config(provider, user_id, api_key_override=openrouter_api_key)
    extra_body: dict = {"response_format": LOCALIZED_TRANSLATION_RESPONSE_FORMAT}
    if provider == "openrouter":
        extra_body["plugins"] = [{"id": "response-healing"}]

    response = client.chat.completions.create(
        model=model,
        messages=build_localized_translation_messages(
            source_full_text_zh,
            script_segments,
            variant=variant,
            custom_system_prompt=custom_system_prompt,
        ),
        temperature=0.2,
        max_tokens=4096,
        extra_body=extra_body,
    )
    payload = _parse_json_content(response.choices[0].message.content)
    return validate_localized_translation(payload)
```

注意：保留 `openrouter_api_key` 参数做向后兼容（作为 `api_key_override`），但新代码应传 `provider` + `user_id`。

- [ ] **Step 3: 重写 `generate_tts_script` 签名和实现**

```python
def generate_tts_script(
    localized_translation: dict,
    *,
    provider: str = "openrouter",
    user_id: int | None = None,
    openrouter_api_key: str | None = None,
) -> dict:
    client, model = _resolve_provider_config(provider, user_id, api_key_override=openrouter_api_key)
    extra_body: dict = {"response_format": TTS_SCRIPT_RESPONSE_FORMAT}
    if provider == "openrouter":
        extra_body["plugins"] = [{"id": "response-healing"}]

    response = client.chat.completions.create(
        model=model,
        messages=build_tts_script_messages(localized_translation),
        temperature=0.2,
        max_tokens=4096,
        extra_body=extra_body,
    )
    payload = _parse_json_content(response.choices[0].message.content)
    return validate_tts_script(payload)
```

- [ ] **Step 4: 重写 `translate_segments` 签名和实现**

```python
def translate_segments(
    segments: List[Dict],
    *,
    provider: str = "openrouter",
    user_id: int | None = None,
    openrouter_api_key: str | None = None,
) -> List[Dict]:
    if not segments:
        return segments

    client, model = _resolve_provider_config(provider, user_id, api_key_override=openrouter_api_key)

    items = [{"index": i, "text": seg["text"]} for i, seg in enumerate(segments)]
    user_prompt = f"""Translate these Chinese TikTok ad script segments to native American English.
Each segment is one spoken sentence or phrase. Keep the same count and order.

Segments:
{json.dumps(items, ensure_ascii=False, indent=2)}

Remember: output only the JSON array."""

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.7,
        max_tokens=4096,
    )

    translations = _parse_json_content(response.choices[0].message.content)
    translation_map = {item["index"]: item["translated"] for item in translations}

    result = []
    for i, seg in enumerate(segments):
        seg_copy = dict(seg)
        seg_copy["translated"] = translation_map.get(i, seg["text"])
        result.append(seg_copy)

    return result
```

- [ ] **Step 5: 新增 `get_model_display_name` 辅助函数**

在 `_resolve_provider_config` 后面添加（用于 usage_log 和前端显示）：

```python
def get_model_display_name(provider: str, user_id: int | None = None) -> str:
    """Return the model ID string for logging/display."""
    _, model = _resolve_provider_config(provider, user_id)
    return model
```

- [ ] **Step 6: 提交**

```bash
git add pipeline/translate.py
git commit -m "refactor: translate.py 支持多 provider（openrouter/doubao）"
```

---

### Task 3: runtime.py 改造 — 读取用户模型偏好

**Files:**
- Modify: `appcore/runtime.py:252-310`（`_step_translate`）
- Modify: `appcore/runtime.py:312-351`（`_step_tts`）

- [ ] **Step 1: 新增辅助函数读取用户翻译模型偏好**

在 `appcore/runtime.py` 中的 `_step_translate` 方法之前（类方法级别），添加一个模块级辅助函数：

```python
def _resolve_translate_provider(user_id: int | None) -> str:
    """Return the user's preferred translate provider, default 'openrouter'."""
    from appcore.api_keys import get_key
    if user_id is None:
        return "openrouter"
    pref = get_key(user_id, "translate_pref")
    return pref if pref in ("openrouter", "doubao") else "openrouter"
```

- [ ] **Step 2: 修改 `_step_translate` 使用 provider**

将 `_step_translate` 方法中第 256-270 行改为：

```python
    def _step_translate(self, task_id: str) -> None:
        task = task_state.get(task_id)
        task_dir = task["task_dir"]
        self._set_step(task_id, "translate", "running", "正在生成整段本土化翻译...")
        from pipeline.localization import build_source_full_text_zh
        from pipeline.translate import generate_localized_translation, get_model_display_name

        provider = _resolve_translate_provider(self.user_id)
        script_segments = task.get("script_segments", [])
        source_full_text_zh = build_source_full_text_zh(script_segments)

        variant = "normal"
        custom_prompt = task.get("custom_translate_prompt")
        localized_translation = generate_localized_translation(
            source_full_text_zh, script_segments, variant=variant,
            custom_system_prompt=custom_prompt,
            provider=provider, user_id=self.user_id,
        )
```

同时将第 294-296 行的 usage_log 调用改为：

```python
        from appcore.usage_log import record as _log_usage
        from pipeline.translate import get_model_display_name
        _log_usage(self.user_id, task_id, provider, model_name=get_model_display_name(provider, self.user_id), success=True)
```

注意：删除原先的 `from appcore.api_keys import resolve_key` 和 `openrouter_api_key = resolve_key(...)` 这两行，以及 `from pipeline.translate import _model_name as _get_model_name` 这行。

- [ ] **Step 3: 修改 `_step_tts` 使用 provider**

将 `_step_tts` 方法中第 322 行和第 339 行改为：

```python
        provider = _resolve_translate_provider(self.user_id)
        elevenlabs_api_key = resolve_key(self.user_id, "elevenlabs", "ELEVENLABS_API_KEY")
```

第 339 行改为：

```python
        tts_script = generate_tts_script(localized_translation, provider=provider, user_id=self.user_id)
```

删除原先的 `openrouter_api_key = resolve_key(self.user_id, "openrouter", "OPENROUTER_API_KEY")` 这行。

- [ ] **Step 4: 提交**

```bash
git add appcore/runtime.py
git commit -m "refactor: runtime 翻译/TTS 步骤使用用户模型偏好"
```

---

### Task 4: retranslate API 支持 model_provider 参数

**Files:**
- Modify: `web/routes/task.py:365-425`

- [ ] **Step 1: 修改 retranslate 路由接收 model_provider**

将 `web/routes/task.py` 中 `retranslate` 函数的第 377-405 行改为：

```python
    body = request.get_json(silent=True) or {}
    prompt_text = (body.get("prompt_text") or "").strip()
    prompt_id = body.get("prompt_id")
    model_provider = body.get("model_provider", "").strip()

    if not prompt_text and prompt_id:
        row = db_query_one(
            "SELECT prompt_text FROM user_prompts WHERE id = %s AND user_id = %s",
            (prompt_id, current_user.id),
        )
        if row:
            prompt_text = row["prompt_text"]

    if not prompt_text:
        return jsonify({"error": "需要提供 prompt_text 或有效的 prompt_id"}), 400

    # Resolve provider: explicit param > user pref > default
    if model_provider not in ("openrouter", "doubao"):
        from appcore.api_keys import get_key
        model_provider = get_key(current_user.id, "translate_pref") or "openrouter"

    from pipeline.translate import generate_localized_translation
    from pipeline.localization import build_source_full_text_zh

    script_segments = task.get("script_segments") or []
    source_full_text_zh = build_source_full_text_zh(script_segments)

    try:
        result = generate_localized_translation(
            source_full_text_zh, script_segments, variant="normal",
            custom_system_prompt=prompt_text,
            provider=model_provider, user_id=current_user.id,
        )
    except Exception as exc:
        return jsonify({"error": f"翻译失败: {exc}"}), 500
```

删除原先的 `from appcore.api_keys import resolve_key` 和 `openrouter_api_key = resolve_key(...)` 这两行。

同时在第 411 行的 `translation_history.append` 中加入 `model_provider`：

```python
    translation_history.append({
        "prompt_text": prompt_text,
        "prompt_id": prompt_id,
        "model_provider": model_provider,
        "result": result,
    })
```

- [ ] **Step 2: 提交**

```bash
git add web/routes/task.py
git commit -m "feat: retranslate API 支持 model_provider 参数"
```

---

### Task 5: settings 后端 — 新增 doubao_llm 和 translate_pref

**Files:**
- Modify: `web/routes/settings.py`

- [ ] **Step 1: 重写 SERVICES 列表和 POST 处理**

将 `web/routes/settings.py` 完整替换为：

```python
from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from appcore.api_keys import DEFAULT_JIANYING_PROJECT_ROOT, get_all, set_key

bp = Blueprint("settings", __name__)

SERVICES = [
    ("doubao_asr", "豆包 ASR", ["key_value", "app_id", "cluster"]),
    ("openrouter", "OpenRouter", ["key_value", "base_url", "model_id"]),
    ("doubao_llm", "豆包翻译", ["key_value", "base_url", "model_id"]),
    ("elevenlabs", "ElevenLabs", ["key_value"]),
]

TRANSLATE_PROVIDERS = ["openrouter", "doubao"]


@bp.route("/settings", methods=["GET", "POST"])
@login_required
def index():
    if request.method == "POST":
        for service, _, fields in SERVICES:
            key_value = request.form.get(f"{service}_key", "").strip()
            extra = {}
            for field in fields[1:]:
                value = request.form.get(f"{service}_{field}", "").strip()
                if value:
                    extra[field] = value
            if key_value or extra:
                set_key(current_user.id, service, key_value, extra or None)

        # 保存默认翻译模型偏好
        translate_pref = request.form.get("translate_pref", "openrouter").strip()
        if translate_pref in TRANSLATE_PROVIDERS:
            set_key(current_user.id, "translate_pref", translate_pref)

        jianying_project_root = request.form.get("jianying_project_root", "").strip() or DEFAULT_JIANYING_PROJECT_ROOT
        set_key(current_user.id, "jianying", "", {"project_root": jianying_project_root})

        flash("配置已保存")
        return redirect(url_for("settings.index"))

    keys = get_all(current_user.id)
    jianying_project_root = keys.get("jianying", {}).get("extra", {}).get("project_root") or DEFAULT_JIANYING_PROJECT_ROOT
    translate_pref = keys.get("translate_pref", {}).get("key_value", "") or "openrouter"
    return render_template(
        "settings.html",
        keys=keys,
        services=SERVICES,
        jianying_project_root=jianying_project_root,
        default_jianying_project_root=DEFAULT_JIANYING_PROJECT_ROOT,
        translate_pref=translate_pref,
    )
```

关键改动：
- `SERVICES` 中 `openrouter` 和新增的 `doubao_llm` 都有 `base_url`、`model_id` 字段
- POST 时即使 `key_value` 为空但 `extra` 有值（如只改了 base_url），也要保存
- 新增 `translate_pref` 的读写
- 传 `translate_pref` 给模板

- [ ] **Step 2: 提交**

```bash
git add web/routes/settings.py
git commit -m "feat: settings 后端支持 doubao_llm 和翻译模型偏好"
```

---

### Task 6: settings.html 模板 — 按流程步骤重组

**Files:**
- Modify: `web/templates/settings.html`

- [ ] **Step 1: 完整重写 settings.html**

```html
{% extends "layout.html" %}
{% block title %}API 配置 - AutoVideoSrt{% endblock %}
{% block page_title %}API 配置{% endblock %}
{% block extra_style %}
.settings-card { background: #fff; border: 1.5px solid #e5e7eb; border-radius: 14px; padding: 24px; margin-bottom: 16px; }
.settings-card h2 { font-size: 15px; font-weight: 700; color: #111827; margin-bottom: 4px; }
.settings-card .step-desc { font-size: 13px; color: #6b7280; margin-bottom: 16px; }
.service-tag { display: inline-block; font-size: 12px; font-weight: 600; padding: 4px 12px; border-radius: 6px; margin-bottom: 10px; }
.service-tag.blue { background: #f0f4ff; color: #4361ee; }
.service-tag.orange { background: #fff3e0; color: #e65100; }
.service-tag.green { background: #e8f5e9; color: #2e7d32; }
label { display: block; color: #6b7280; font-size: 12px; font-weight: 600; text-transform: uppercase; margin-bottom: 6px; margin-top: 14px; }
input[type=text], input[type=password], select { width: 100%; background: #f9fafb; border: 1.5px solid #e5e7eb; border-radius: 10px; color: #111827; padding: 10px 12px; font-size: 14px; font-family: inherit; outline: none; }
input[type=text]:focus, input[type=password]:focus, select:focus { border-color: #7c6fe0; background: #fff; }
.save-btn { margin-top: 24px; }
.success { background: #f0fdf4; border: 1px solid #bbf7d0; color: #16a34a; font-size: 13px; padding: 10px 12px; border-radius: 8px; margin-bottom: 16px; }
.hint { color: #9ca3af; font-size: 12px; margin-top: 4px; }
.divider { border: none; border-top: 1px dashed #e5e7eb; margin: 16px 0; }
{% endblock %}
{% block content %}
<h1 style="font-size:20px;font-weight:700;color:#111827;margin-bottom:20px">API Key 配置</h1>
{% with messages = get_flashed_messages() %}
  {% if messages %}<p class="success">{{ messages[0] }}</p>{% endif %}
{% endwith %}
<form method="post">

  {# ── 第一步：语音识别 ── #}
  <div class="settings-card">
    <h2>第一步：语音识别</h2>
    <p class="step-desc">将视频中的中文语音转为文字</p>
    <span class="service-tag blue">豆包 ASR</span>
    <label>API Key</label>
    <input type="password" name="doubao_asr_key" placeholder="留空则保持当前配置"
           value="{{ keys.get('doubao_asr', {}).get('key_value', '') }}">
    <label>App ID</label>
    <input type="text" name="doubao_asr_app_id" placeholder="可选"
           value="{{ keys.get('doubao_asr', {}).get('extra', {}).get('app_id', '') }}">
    <label>Cluster</label>
    <input type="text" name="doubao_asr_cluster" placeholder="可选，默认 volc.seedasr.auc"
           value="{{ keys.get('doubao_asr', {}).get('extra', {}).get('cluster', '') }}">
  </div>

  {# ── 第二步：翻译与本土化 ── #}
  <div class="settings-card">
    <h2>第二步：翻译与本土化</h2>
    <p class="step-desc">将中文内容翻译为地道的英文</p>

    <label>默认翻译模型</label>
    <select name="translate_pref">
      <option value="openrouter" {{ 'selected' if translate_pref == 'openrouter' }}>Claude Sonnet (OpenRouter)</option>
      <option value="doubao" {{ 'selected' if translate_pref == 'doubao' }}>豆包 2.0 Pro</option>
    </select>
    <p class="hint">每次翻译时也可临时切换</p>

    <hr class="divider">
    <span class="service-tag blue">Claude Sonnet · via OpenRouter</span>
    <label>API Key</label>
    <input type="password" name="openrouter_key" placeholder="留空则保持当前配置"
           value="{{ keys.get('openrouter', {}).get('key_value', '') }}">
    <label>请求 URL</label>
    <input type="text" name="openrouter_base_url" placeholder="https://openrouter.ai/api/v1"
           value="{{ keys.get('openrouter', {}).get('extra', {}).get('base_url', '') }}">
    <label>模型 ID</label>
    <input type="text" name="openrouter_model_id" placeholder="anthropic/claude-sonnet-4-5"
           value="{{ keys.get('openrouter', {}).get('extra', {}).get('model_id', '') }}">

    <hr class="divider">
    <span class="service-tag orange">豆包 2.0 Pro · via 火山引擎 ARK</span>
    <label>API Key</label>
    <input type="password" name="doubao_llm_key" placeholder="留空则保持当前配置"
           value="{{ keys.get('doubao_llm', {}).get('key_value', '') }}">
    <label>请求 URL</label>
    <input type="text" name="doubao_llm_base_url" placeholder="https://ark.cn-beijing.volces.com/api/v3"
           value="{{ keys.get('doubao_llm', {}).get('extra', {}).get('base_url', '') }}">
    <label>模型 ID</label>
    <input type="text" name="doubao_llm_model_id" placeholder="doubao-seed-2-0-pro-260215"
           value="{{ keys.get('doubao_llm', {}).get('extra', {}).get('model_id', '') }}">
  </div>

  {# ── 第三步：配音合成 ── #}
  <div class="settings-card">
    <h2>第三步：配音合成</h2>
    <p class="step-desc">生成英文配音音频</p>
    <span class="service-tag green">ElevenLabs</span>
    <label>API Key</label>
    <input type="password" name="elevenlabs_key" placeholder="留空则保持当前配置"
           value="{{ keys.get('elevenlabs', {}).get('key_value', '') }}">
  </div>

  {# ── 第四步：导出 ── #}
  <div class="settings-card">
    <h2>第四步：导出</h2>
    <p class="step-desc">导出到剪映项目</p>
    <label>剪映项目根目录</label>
    <input type="text" name="jianying_project_root"
           value="{{ jianying_project_root }}">
    <p class="hint">导出的 CapCut 工程会把素材路径指向这个本机剪映项目根目录。默认值：{{ default_jianying_project_root }}</p>
  </div>

  <button class="btn btn-primary save-btn" type="submit">保存配置</button>
</form>
{% endblock %}
```

- [ ] **Step 2: 提交**

```bash
git add web/templates/settings.html
git commit -m "feat: 配置页面按流程步骤重组，新增豆包翻译和模型选择"
```

---

### Task 7: 任务工作台 — 翻译面板新增模型下拉

**Files:**
- Modify: `web/templates/_task_workbench.html:125-148`
- Modify: `web/templates/_task_workbench_scripts.html` (doRetranslate 函数)

- [ ] **Step 1: 在翻译面板 HTML 中新增模型选择下拉**

在 `web/templates/_task_workbench.html` 第 125-128 行之间，`<div class="prompt-selector">` 之前插入模型选择器：

在第 125 行 `<div class="translate-prompt-panel hidden" id="translatePromptPanel">` 之后、第 126 行 `<div class="prompt-selector">` 之前插入：

```html
        <div class="prompt-selector" style="margin-bottom:12px;">
          <label>翻译模型</label>
          <select id="translateModelSelect" style="width:auto;min-width:200px;">
            <option value="openrouter">Claude Sonnet (OpenRouter)</option>
            <option value="doubao">豆包 2.0 Pro</option>
          </select>
        </div>
```

- [ ] **Step 2: 修改 doRetranslate 传递 model_provider**

在 `web/templates/_task_workbench_scripts.html` 中，将 `doRetranslate` 函数（第 974-997 行）中第 980-983 行的 fetch body 改为：

```javascript
      const modelProvider = document.getElementById("translateModelSelect").value;
      const res = await fetch(`/api/tasks/${taskId}/retranslate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt_id: _selectedPromptId, prompt_text: promptText, model_provider: modelProvider }),
      });
```

- [ ] **Step 3: 初始化模型下拉默认值**

在 `_task_workbench_scripts.html` 中，在 `showTranslatePromptPanel` 函数（第 954-958 行）内添加一行，使下拉默认选中用户偏好：

```javascript
  function showTranslatePromptPanel() {
    const panel = document.getElementById("translatePromptPanel");
    if (panel) panel.classList.remove("hidden");
    const modelSelect = document.getElementById("translateModelSelect");
    if (modelSelect && window._userTranslatePref) modelSelect.value = window._userTranslatePref;
    bootPrompts();
  }
```

然后需要在页面加载时把用户偏好传到前端。在 `_task_workbench.html` 的 `<script>` 区域或 `_task_workbench_scripts.html` 顶部添加：

检查 `_task_workbench.html` 如何传递数据到 JS。找到 `taskId` 变量的定义位置，在旁边加一行：

```javascript
  window._userTranslatePref = "{{ translate_pref | default('openrouter') }}";
```

同时需要在渲染 `_task_workbench.html` 的路由中传递 `translate_pref`。在 `web/routes/task.py` 中找到渲染 workbench 的位置，确保传递 `translate_pref`：

```python
from appcore.api_keys import get_key
translate_pref = get_key(current_user.id, "translate_pref") or "openrouter"
```

并在 `render_template` 调用中加入 `translate_pref=translate_pref`。

- [ ] **Step 4: 提交**

```bash
git add web/templates/_task_workbench.html web/templates/_task_workbench_scripts.html web/routes/task.py
git commit -m "feat: 任务工作台翻译面板新增模型选择下拉"
```

---

### Task 8: 端到端验证

- [ ] **Step 1: 启动应用验证配置页面**

```bash
python app.py
```

打开浏览器访问 `/settings`，验证：
- 页面按四个流程步骤分块显示
- "翻译与本土化"区块包含默认模型下拉、两组服务配置（各含 API Key / 请求 URL / 模型 ID）
- 保存配置后刷新，值正确回显

- [ ] **Step 2: 验证翻译功能**

在任务工作台中：
- 选择 OpenRouter 模型执行翻译，确认翻译正常返回
- 切换到豆包 2.0 Pro 模型执行重新翻译，确认翻译正常返回
- 检查翻译历史中 model_provider 字段正确记录

- [ ] **Step 3: 提交验证通过**

```bash
git add -A
git commit -m "test: 端到端验证翻译模型切换功能"
```
