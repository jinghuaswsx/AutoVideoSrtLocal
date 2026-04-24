# OpenRouter OpenAI Image 2 图片翻译接入 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不改图片翻译任务结构的前提下，为 `openrouter` 通道新增可配置启用的 OpenAI Image 2 三档质量模型，并把它接入系统设置、图片翻译模型备选项、商品详情图一键翻译和 OpenRouter 调用链路。

**Architecture:** 保持现有“任务只存一个 `model_id` 字符串”的架构不变。新增的 OpenAI Image 2 能力通过两个 `system_settings` 配置项控制是否启用和默认质量；当功能开启时，`appcore.gemini_image` 在 `openrouter` 通道里追加三个虚拟模型 ID，并在运行时把它们解析为真实模型 `openai/gpt-5.4-image-2` 和 `quality=low|medium|high` 请求参数。设置页、图片翻译路由和 `medias` 默认模型逻辑全部继续依赖同一套 `image_translate_settings + gemini_image` 辅助函数，避免重复判断。

**Tech Stack:** Python / Flask / Jinja2 / pytest / OpenAI SDK（OpenRouter 兼容接口）/ `system_settings`

---

## 文件结构

### 修改文件

- `appcore/image_translate_settings.py`
  负责新增 OpenAI Image 2 开关与默认质量读写，保证 `openrouter` 默认模型回退行为统一。
- `appcore/gemini_image.py`
  负责注册 OpenRouter OpenAI Image 2 三档模型、解析虚拟模型 ID、在 OpenRouter 请求里附加质量参数。
- `web/routes/settings.py`
  负责设置页 GET/POST，把新配置项传给模板并保存，同时在功能关闭时回退默认模型。
- `web/templates/settings.html`
  负责在图片翻译全局配置卡片中展示“启用 OpenAI Image 2”和“默认质量”控件，并让默认模型下拉跟随开关与通道联动。
- `web/routes/image_translate.py`
  负责 `/api/image-translate/models` 返回受开关影响的模型列表，以及新建任务时的模型合法性校验。
- `web/routes/medias.py`
  负责商品详情图一键翻译的默认模型选择继续与全局配置保持一致。
- `tests/test_image_translate_settings.py`
  覆盖新配置项、默认模型回退和质量合法值。
- `tests/test_gemini_image.py`
  覆盖 OpenAI Image 2 模型注册、虚拟模型 ID 解析和 OpenRouter 请求参数映射。
- `tests/test_settings_routes_new.py`
  覆盖设置页展示和 POST 保存新配置项。
- `tests/test_image_translate_routes.py`
  覆盖图片翻译模型列表、新建任务合法性和 `medias` 默认模型联动。

### 不改但要回归验证的文件

- `web/templates/_image_translate_scripts.html`
  新模型会经由已有 `/api/image-translate/models` 返回给这里，无需改 JS 结构，但要确认旧逻辑仍正确选默认项。
- `appcore/image_translate_runtime.py`
  不改 runtime；依赖现有 `generate_image(..., model=task["model_id"])` 调用继续工作。

---

### Task 1: 补齐配置层和设置页开关

**Files:**
- Modify: `appcore/image_translate_settings.py`
- Modify: `web/routes/settings.py`
- Modify: `web/templates/settings.html`
- Test: `tests/test_image_translate_settings.py`
- Test: `tests/test_settings_routes_new.py`

- [ ] **Step 1: 写配置层失败测试**

在 `tests/test_image_translate_settings.py` 末尾追加下面三个用例：

```python
def test_openrouter_openai_image2_defaults(monkeypatch):
    from appcore import image_translate_settings as its

    _patch_store(monkeypatch, {})

    assert its.is_openrouter_openai_image2_enabled() is False
    assert its.get_openrouter_openai_image2_default_quality() == "mid"


def test_openrouter_openai_image2_settings_round_trip(monkeypatch):
    from appcore import image_translate_settings as its

    store = {}
    _patch_store(monkeypatch, store)

    its.set_openrouter_openai_image2_enabled(True)
    its.set_openrouter_openai_image2_default_quality("high")

    assert store["image_translate.openrouter_openai_image2_enabled"] == "1"
    assert store["image_translate.openrouter_openai_image2_default_quality"] == "high"
    assert its.is_openrouter_openai_image2_enabled() is True
    assert its.get_openrouter_openai_image2_default_quality() == "high"


def test_openrouter_openai_image2_quality_rejects_invalid_value(monkeypatch):
    from appcore import image_translate_settings as its

    _patch_store(monkeypatch, {})

    with pytest.raises(ValueError):
        its.set_openrouter_openai_image2_default_quality("ultra")
```

- [ ] **Step 2: 运行配置测试确认失败**

Run:

```bash
pytest tests/test_image_translate_settings.py -q
```

Expected:

- 现有用例通过
- 新增用例失败，报 `AttributeError` 或 `NameError`，提示缺少 `is_openrouter_openai_image2_enabled` 等函数

- [ ] **Step 3: 在 `appcore/image_translate_settings.py` 实现新配置函数**

在常量区和 `get_default_model()` 之前插入下面代码：

```python
from appcore.settings import get_setting, set_setting

_OPENROUTER_OPENAI_IMAGE2_ENABLED_KEY = "image_translate.openrouter_openai_image2_enabled"
_OPENROUTER_OPENAI_IMAGE2_DEFAULT_QUALITY_KEY = "image_translate.openrouter_openai_image2_default_quality"
_OPENROUTER_OPENAI_IMAGE2_QUALITIES = ("low", "mid", "high")


def is_openrouter_openai_image2_enabled() -> bool:
    raw = (get_setting(_OPENROUTER_OPENAI_IMAGE2_ENABLED_KEY) or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def set_openrouter_openai_image2_enabled(value: bool) -> None:
    set_setting(_OPENROUTER_OPENAI_IMAGE2_ENABLED_KEY, "1" if value else "0")


def get_openrouter_openai_image2_default_quality() -> str:
    raw = (get_setting(_OPENROUTER_OPENAI_IMAGE2_DEFAULT_QUALITY_KEY) or "").strip().lower()
    return raw if raw in _OPENROUTER_OPENAI_IMAGE2_QUALITIES else "mid"


def set_openrouter_openai_image2_default_quality(value: str) -> None:
    normalized = (value or "").strip().lower()
    if normalized not in _OPENROUTER_OPENAI_IMAGE2_QUALITIES:
        raise ValueError(f"unsupported openrouter openai image2 quality: {value}")
    set_setting(_OPENROUTER_OPENAI_IMAGE2_DEFAULT_QUALITY_KEY, normalized)
```

注意：不要删除现有 `_read()` / `_write()` 逻辑；这四个函数直接走 `appcore.settings` 即可，避免和 prompt 存储混淆。

- [ ] **Step 4: 重新运行配置测试**

Run:

```bash
pytest tests/test_image_translate_settings.py -q
```

Expected:

- 新增 3 个用例通过
- 总体通过数比修改前增加 3

- [ ] **Step 5: 写设置页失败测试**

在 `tests/test_settings_routes_new.py` 里追加下面两个测试：

```python
def test_settings_get_renders_openai_image2_controls_for_openrouter(admin_no_db_client):
    with patch("web.routes.settings.get_all", return_value={}), \
         patch("web.routes.settings.llm_bindings.list_all", return_value=[]), \
         patch("web.routes.settings.get_image_translate_channel", return_value="openrouter"), \
         patch("web.routes.settings.get_image_translate_default_model",
               return_value="gemini-3-pro-image-preview"), \
         patch("web.routes.settings.is_openrouter_openai_image2_enabled", return_value=True), \
         patch("web.routes.settings.get_openrouter_openai_image2_default_quality", return_value="high"):
        resp = admin_no_db_client.get("/settings?tab=providers")

    body = resp.get_data(as_text=True)
    assert "启用 OpenAI Image 2" in body
    assert 'name="openrouter_openai_image2_enabled"' in body
    assert 'name="openrouter_openai_image2_default_quality"' in body
    assert 'value="high" selected' in body


def test_settings_post_providers_saves_openai_image2_controls(admin_no_db_client):
    with patch("web.routes.settings.set_image_translate_channel"), \
         patch("web.routes.settings.set_image_translate_default_model"), \
         patch("web.routes.settings.set_openrouter_openai_image2_enabled") as m_enabled, \
         patch("web.routes.settings.set_openrouter_openai_image2_default_quality") as m_quality:
        resp = admin_no_db_client.post("/settings", data={
            "tab": "providers",
            "translate_pref": "vertex_gemini_31_flash_lite",
            "jianying_project_root": "/custom/path",
            "image_translate_channel": "openrouter",
            "image_translate_default_model": "gemini-3-pro-image-preview",
            "openrouter_openai_image2_enabled": "1",
            "openrouter_openai_image2_default_quality": "high",
        })

    assert resp.status_code in (302, 303)
    m_enabled.assert_called_once_with(True)
    m_quality.assert_called_once_with("high")
```

- [ ] **Step 6: 运行设置页测试确认失败**

Run:

```bash
pytest tests/test_settings_routes_new.py -q
```

Expected:

- 新增测试失败
- GET 失败点是模板里没有新控件
- POST 失败点是 `web.routes.settings` 未导入或未调用新的 setter

- [ ] **Step 7: 在 `web/routes/settings.py` 接入新配置**

先补 import：

```python
from appcore.image_translate_settings import (
    CHANNEL_LABELS as IMAGE_TRANSLATE_CHANNEL_LABELS,
    CHANNELS as IMAGE_TRANSLATE_CHANNELS,
    get_channel as get_image_translate_channel,
    get_default_model as get_image_translate_default_model,
    get_openrouter_openai_image2_default_quality,
    is_openrouter_openai_image2_enabled,
    set_channel as set_image_translate_channel,
    set_default_model as set_image_translate_default_model,
    set_openrouter_openai_image2_default_quality,
    set_openrouter_openai_image2_enabled,
)
```

在 `render_template(...)` 参数里追加：

```python
        openrouter_openai_image2_enabled=is_openrouter_openai_image2_enabled(),
        openrouter_openai_image2_default_quality=get_openrouter_openai_image2_default_quality(),
```

在 `_handle_providers_post()` 末尾、`image_translate_channel` 分支里追加：

```python
    enabled = request.form.get("openrouter_openai_image2_enabled") == "1"
    quality = (request.form.get("openrouter_openai_image2_default_quality") or "mid").strip().lower()
    set_openrouter_openai_image2_enabled(enabled)
    set_openrouter_openai_image2_default_quality(quality)
```

注意：这里先不做“关闭后回退默认模型”的处理，那部分放到 Task 3 跟模型注册一起做，避免 setter 调用顺序先后打架。

- [ ] **Step 8: 在 `web/templates/settings.html` 增加控件与联动容器**

在“全局配置”卡片、默认模型 `<select>` 后面追加下面 HTML：

```html
    <div id="openrouterOpenaiImage2Controls" {% if image_translate_channel != 'openrouter' %}hidden{% endif %}>
      <label style="margin-top:12px;">OpenRouter 附加能力</label>
      <label style="display:flex; align-items:center; gap:8px; margin-top:8px;">
        <input type="checkbox"
               id="openrouterOpenaiImage2Enabled"
               name="openrouter_openai_image2_enabled"
               value="1"
               {{ 'checked' if openrouter_openai_image2_enabled }}>
        <span>启用 OpenAI Image 2</span>
      </label>
      <label>OpenAI Image 2 默认质量</label>
      <select id="openrouterOpenaiImage2DefaultQuality"
              name="openrouter_openai_image2_default_quality">
        <option value="low" {{ 'selected' if openrouter_openai_image2_default_quality == 'low' }}>Low</option>
        <option value="mid" {{ 'selected' if openrouter_openai_image2_default_quality == 'mid' }}>Mid</option>
        <option value="high" {{ 'selected' if openrouter_openai_image2_default_quality == 'high' }}>High</option>
      </select>
      <p class="hint">关闭时不在图片翻译模型备选中展示 OpenAI Image 2；开启后会在 OpenRouter 通道下追加 Low / Mid / High 三档。</p>
    </div>
```

并把原有卡片内联脚本替换成下面版本：

```html
    <script>
      (function() {
        var channelEl = document.getElementById('imageTranslateChannel');
        var modelEl = document.getElementById('imageTranslateDefaultModel');
        var dataEl = document.getElementById('imageTranslateModelOptions');
        var extraWrap = document.getElementById('openrouterOpenaiImage2Controls');
        var extraToggle = document.getElementById('openrouterOpenaiImage2Enabled');
        var qualityEl = document.getElementById('openrouterOpenaiImage2DefaultQuality');
        if (!channelEl || !modelEl || !dataEl) return;
        var modelsByChannel = {};
        try { modelsByChannel = JSON.parse(dataEl.textContent || '{}'); } catch (e) { modelsByChannel = {}; }

        function syncExtraVisibility() {
          var isOpenrouter = channelEl.value === 'openrouter';
          if (extraWrap) extraWrap.hidden = !isOpenrouter;
          if (qualityEl) qualityEl.disabled = !isOpenrouter || !(extraToggle && extraToggle.checked);
        }

        function renderModels(channel, preferred) {
          var models = modelsByChannel[channel] || [];
          var picked = preferred && models.some(function(m) { return m.id === preferred; })
            ? preferred
            : (models[0] && models[0].id) || '';
          modelEl.innerHTML = models.map(function(m) {
            var selected = m.id === picked ? ' selected' : '';
            return '<option value="' + m.id + '"' + selected + '>' + m.label + '</option>';
          }).join('');
        }

        channelEl.addEventListener('change', function() {
          syncExtraVisibility();
          renderModels(channelEl.value, '');
        });
        if (extraToggle) {
          extraToggle.addEventListener('change', function() {
            syncExtraVisibility();
          });
        }
        syncExtraVisibility();
      })();
    </script>
```

- [ ] **Step 9: 运行设置页测试确认通过**

Run:

```bash
pytest tests/test_settings_routes_new.py -q
```

Expected:

- 新增两个测试通过
- 旧有 settings 路由测试保持通过

- [ ] **Step 10: Commit**

```bash
git add appcore/image_translate_settings.py web/routes/settings.py web/templates/settings.html tests/test_image_translate_settings.py tests/test_settings_routes_new.py
git commit -m "feat(settings): add openrouter openai image2 controls"
```

---

### Task 2: 扩展模型注册和 OpenRouter 质量映射

**Files:**
- Modify: `appcore/gemini_image.py`
- Test: `tests/test_gemini_image.py`

- [ ] **Step 1: 写模型注册和质量映射失败测试**

在 `tests/test_gemini_image.py` 里追加下面四个测试：

```python
def test_list_image_models_openrouter_appends_openai_image2_when_enabled():
    from appcore import gemini_image

    with patch("appcore.image_translate_settings.is_openrouter_openai_image2_enabled", return_value=True):
        models = gemini_image.list_image_models("openrouter")

    ids = [mid for mid, _ in models]
    assert "openai/gpt-5.4-image-2:low" in ids
    assert "openai/gpt-5.4-image-2:mid" in ids
    assert "openai/gpt-5.4-image-2:high" in ids


def test_list_image_models_openrouter_hides_openai_image2_when_disabled():
    from appcore import gemini_image

    with patch("appcore.image_translate_settings.is_openrouter_openai_image2_enabled", return_value=False):
        ids = [mid for mid, _ in gemini_image.list_image_models("openrouter")]

    assert "openai/gpt-5.4-image-2:mid" not in ids


def test_parse_openrouter_openai_image2_model_maps_mid_to_medium():
    from appcore import gemini_image

    assert gemini_image.parse_openrouter_openai_image2_model("openai/gpt-5.4-image-2:mid") == (
        "openai/gpt-5.4-image-2",
        "medium",
    )


def test_generate_image_openrouter_image2_passes_quality_to_openrouter():
    from appcore import gemini_image

    raw = b"PNG"
    data_url = f"data:image/png;base64,{base64.b64encode(raw).decode()}"
    or_resp = MagicMock()
    choice = MagicMock()
    choice.finish_reason = "stop"
    image_obj = MagicMock()
    image_obj.image_url = MagicMock(url=data_url)
    choice.message = MagicMock(images=[image_obj])
    or_resp.choices = [choice]
    or_resp.usage = MagicMock(prompt_tokens=5, completion_tokens=0, cost="0.12")

    created = {}

    class _FakeOpenAI:
        def __init__(self, *, api_key, base_url):
            self.chat = MagicMock()
            self.chat.completions = MagicMock()

            def _create(**kwargs):
                created.update(kwargs)
                return or_resp

            self.chat.completions.create = _create

    with patch("openai.OpenAI", _FakeOpenAI), \
         patch.object(gemini_image, "resolve_config", return_value=("IGNORED", "openai/gpt-5.4-image-2:mid")), \
         patch.object(gemini_image, "_resolve_channel", return_value="openrouter"), \
         patch.object(gemini_image, "OPENROUTER_API_KEY", "OR-KEY"), \
         patch("appcore.image_translate_settings.is_openrouter_openai_image2_enabled", return_value=True):
        out, mime = gemini_image.generate_image(
            prompt="翻译",
            source_image=b"SRC",
            source_mime="image/jpeg",
            model="openai/gpt-5.4-image-2:mid",
        )

    assert out == raw
    assert mime == "image/png"
    assert created["model"] == "openai/gpt-5.4-image-2"
    assert created["extra_body"]["quality"] == "medium"
```

- [ ] **Step 2: 运行图像模型测试确认失败**

Run:

```bash
pytest tests/test_gemini_image.py -q
```

Expected:

- 新增测试失败
- 失败点包括 `parse_openrouter_openai_image2_model` 不存在，以及模型列表中没有 `openai/gpt-5.4-image-2:*`

- [ ] **Step 3: 在 `appcore/gemini_image.py` 增加 OpenAI Image 2 常量和解析函数**

在 `IMAGE_MODELS_BY_CHANNEL` 上方追加：

```python
_OPENROUTER_OPENAI_IMAGE2_MODEL = "openai/gpt-5.4-image-2"
_OPENROUTER_OPENAI_IMAGE2_MODEL_IDS = {
    "low": f"{_OPENROUTER_OPENAI_IMAGE2_MODEL}:low",
    "mid": f"{_OPENROUTER_OPENAI_IMAGE2_MODEL}:mid",
    "high": f"{_OPENROUTER_OPENAI_IMAGE2_MODEL}:high",
}
_OPENROUTER_OPENAI_IMAGE2_LABELS = {
    "low": "OpenAI Image 2（Low）",
    "mid": "OpenAI Image 2（Mid）",
    "high": "OpenAI Image 2（High）",
}
_OPENROUTER_OPENAI_IMAGE2_QUALITY_MAP = {
    "low": "low",
    "mid": "medium",
    "high": "high",
}
```

在 `coerce_image_model()` 上方追加：

```python
def is_openrouter_openai_image2_model(model_id: str | None) -> bool:
    normalized = (model_id or "").strip()
    return normalized in _OPENROUTER_OPENAI_IMAGE2_MODEL_IDS.values()


def parse_openrouter_openai_image2_model(model_id: str | None) -> tuple[str, str] | None:
    normalized = (model_id or "").strip()
    for quality, virtual_id in _OPENROUTER_OPENAI_IMAGE2_MODEL_IDS.items():
        if normalized == virtual_id:
            return _OPENROUTER_OPENAI_IMAGE2_MODEL, _OPENROUTER_OPENAI_IMAGE2_QUALITY_MAP[quality]
    return None


def _openrouter_models_with_optional_openai_image2() -> list[tuple[str, str]]:
    models = list(IMAGE_MODELS_BY_CHANNEL["openrouter"])
    try:
        from appcore.image_translate_settings import is_openrouter_openai_image2_enabled
    except Exception:
        is_openrouter_openai_image2_enabled = lambda: False
    if is_openrouter_openai_image2_enabled():
        models.extend([
            (_OPENROUTER_OPENAI_IMAGE2_MODEL_IDS["low"], _OPENROUTER_OPENAI_IMAGE2_LABELS["low"]),
            (_OPENROUTER_OPENAI_IMAGE2_MODEL_IDS["mid"], _OPENROUTER_OPENAI_IMAGE2_LABELS["mid"]),
            (_OPENROUTER_OPENAI_IMAGE2_MODEL_IDS["high"], _OPENROUTER_OPENAI_IMAGE2_LABELS["high"]),
        ])
    return models
```

- [ ] **Step 4: 让 `list_image_models()` 和 `default_image_model()` 走新模型集合**

把 `list_image_models()` 和 `default_image_model()` 改成下面形式：

```python
def list_image_models(channel: str | None = None) -> list[tuple[str, str]]:
    normalized = normalize_image_channel(channel)
    if normalized == "openrouter":
        return _openrouter_models_with_optional_openai_image2()
    return list(IMAGE_MODELS_BY_CHANNEL[normalized])


def default_image_model(channel: str | None = None) -> str:
    normalized = normalize_image_channel(channel)
    models = list_image_models(normalized)
    if normalized == "openrouter":
        try:
            from appcore.image_translate_settings import (
                get_openrouter_openai_image2_default_quality,
                is_openrouter_openai_image2_enabled,
            )
            if is_openrouter_openai_image2_enabled():
                quality = get_openrouter_openai_image2_default_quality()
                preferred = _OPENROUTER_OPENAI_IMAGE2_MODEL_IDS[quality]
                if any(mid == preferred for mid, _ in models):
                    return preferred
        except Exception:
            pass
    return models[0][0] if models else "gemini-3.1-flash-image-preview"
```

- [ ] **Step 5: 在 `_generate_via_openrouter()` 里透传质量参数**

把 `_generate_via_openrouter()` 开头改成下面形式：

```python
    parsed = parse_openrouter_openai_image2_model(model_id)
    if parsed is not None:
        or_model, image_quality = parsed
    else:
        or_model = _to_openrouter_model(model_id)
        image_quality = None

    extra_body = {"usage": {"include": True}}
    if image_quality is not None:
        extra_body["quality"] = image_quality

    try:
        resp = client.chat.completions.create(
            model=or_model,
            messages=messages,
            modalities=["image", "text"],
            extra_body=extra_body,
        )
```

并把 fallback 分支也同步改成：

```python
        resp = client.chat.completions.create(
            model=or_model,
            messages=messages,
            extra_body={"modalities": ["image", "text"], **extra_body},
        )
```

注意：保留已有 Gemini OpenRouter 路径，不能让普通 `gemini-3-pro-image-preview` 也被当成 OpenAI Image 2。

- [ ] **Step 6: 运行图像模型测试确认通过**

Run:

```bash
pytest tests/test_gemini_image.py -q
```

Expected:

- 新增 4 个用例通过
- 现有 OpenRouter Gemini、Cloud、Doubao 测试保持通过

- [ ] **Step 7: Commit**

```bash
git add appcore/gemini_image.py tests/test_gemini_image.py
git commit -m "feat(image): add openrouter openai image2 quality variants"
```

---

### Task 3: 打通图片翻译路由和 `medias` 默认模型联动

**Files:**
- Modify: `web/routes/image_translate.py`
- Modify: `web/routes/medias.py`
- Modify: `web/routes/settings.py`
- Test: `tests/test_image_translate_routes.py`

- [ ] **Step 1: 写路由失败测试**

在 `tests/test_image_translate_routes.py` 靠前的模型相关测试后面追加下面四个测试：

```python
def test_models_endpoint_returns_openai_image2_variants_when_enabled(authed_client_no_db, monkeypatch):
    from web.routes import image_translate as r

    monkeypatch.setattr(r.its, "get_channel", lambda: "openrouter")
    monkeypatch.setattr(r.its, "is_openrouter_openai_image2_enabled", lambda: True)
    monkeypatch.setattr(r.its, "get_openrouter_openai_image2_default_quality", lambda: "high")

    resp = authed_client_no_db.get("/api/image-translate/models")

    assert resp.status_code == 200
    data = resp.get_json()
    ids = [item["id"] for item in data["items"]]
    assert "openai/gpt-5.4-image-2:low" in ids
    assert "openai/gpt-5.4-image-2:mid" in ids
    assert "openai/gpt-5.4-image-2:high" in ids
    assert data["default_model_id"] == "openai/gpt-5.4-image-2:high"


def test_upload_complete_rejects_openai_image2_when_disabled(authed_client_no_db, monkeypatch):
    from web.routes import image_translate as r

    _patch_tos_and_runner(monkeypatch)
    _patch_lang(monkeypatch)
    _patch_task_state(monkeypatch)
    monkeypatch.setattr(r.its, "get_channel", lambda: "openrouter")
    monkeypatch.setattr(r.its, "is_openrouter_openai_image2_enabled", lambda: False)

    b = authed_client_no_db.post("/api/image-translate/upload/bootstrap", json={
        "count": 1,
        "files": [{"filename": "a.jpg", "size": 1, "content_type": "image/jpeg"}],
    }).get_json()

    resp = authed_client_no_db.post("/api/image-translate/upload/complete", json={
        "task_id": b["task_id"],
        "preset": "cover",
        "target_language": "de",
        "model_id": "openai/gpt-5.4-image-2:mid",
        "prompt": "x",
        "product_name": "demo",
        "uploaded": [{"idx": 0, "object_key": b["uploads"][0]["object_key"], "filename": "a.jpg", "size": 1}],
    })

    assert resp.status_code == 400
    assert resp.get_json()["error"] == "unsupported model"


def test_upload_complete_accepts_openai_image2_when_enabled(authed_client_no_db, monkeypatch):
    from web.routes import image_translate as r

    _patch_tos_and_runner(monkeypatch)
    _patch_lang(monkeypatch)
    created = _patch_task_state(monkeypatch)
    monkeypatch.setattr(r.its, "get_channel", lambda: "openrouter")
    monkeypatch.setattr(r.its, "is_openrouter_openai_image2_enabled", lambda: True)

    b = authed_client_no_db.post("/api/image-translate/upload/bootstrap", json={
        "count": 1,
        "files": [{"filename": "a.jpg", "size": 1, "content_type": "image/jpeg"}],
    }).get_json()

    resp = authed_client_no_db.post("/api/image-translate/upload/complete", json={
        "task_id": b["task_id"],
        "preset": "cover",
        "target_language": "de",
        "model_id": "openai/gpt-5.4-image-2:mid",
        "prompt": "x",
        "product_name": "demo",
        "uploaded": [{"idx": 0, "object_key": b["uploads"][0]["object_key"], "filename": "a.jpg", "size": 1}],
    })

    assert resp.status_code == 201
    assert created[b["task_id"]]["model_id"] == "openai/gpt-5.4-image-2:mid"


def test_medias_default_image_model_uses_openai_image2_when_enabled(authed_client_no_db, monkeypatch):
    from web.routes import medias as r

    created = {}
    monkeypatch.setattr(r.medias, "get_product", lambda pid: {"id": pid, "user_id": 1, "name": "demo"})
    monkeypatch.setattr(r, "_can_access_product", lambda product: True)
    monkeypatch.setattr(
        r.medias,
        "list_detail_images",
        lambda pid, lang: [{"id": 11, "object_key": "1/medias/1/a.jpg"}] if lang == "en" else [],
    )
    monkeypatch.setattr(r.medias, "is_valid_language", lambda code: code in {"en", "de"})
    monkeypatch.setattr(r.medias, "get_language_name", lambda lang: "German")
    monkeypatch.setattr(r.its, "get_channel", lambda: "openrouter")
    monkeypatch.setattr(r.its, "get_prompts_for_lang", lambda lang: {"detail": "translate {target_language_name}"})
    monkeypatch.setattr(r.its, "get_default_model", lambda channel: "openai/gpt-5.4-image-2:high")
    monkeypatch.setattr(
        r.task_state,
        "create_image_translate",
        lambda task_id, task_dir, **kw: created.update(kw) or {"id": task_id},
    )
    monkeypatch.setattr(r, "_start_image_translate_runner", lambda task_id, user_id: True)

    resp = authed_client_no_db.post(
        "/medias/api/products/123/detail-images/translate-from-en",
        json={"lang": "de"},
    )

    assert resp.status_code == 201
    assert created["model_id"] == "openai/gpt-5.4-image-2:high"
```

- [ ] **Step 2: 运行图片翻译路由测试确认新增用例失败**

Run:

```bash
pytest tests/test_image_translate_routes.py -q
```

Expected:

- 仓库里已有与本需求无关的旧失败继续存在
- 新增 4 个用例也会失败，失败点包括模型列表未追加档位，以及 `upload/complete` 还不认识 `openai/gpt-5.4-image-2:mid`

- [ ] **Step 3: 让设置页关闭开关时自动回退默认模型**

回到 `web/routes/settings.py` 的 `_handle_providers_post()`，把 Task 1 里暂时简化的保存逻辑替换成下面版本：

```python
    enabled = request.form.get("openrouter_openai_image2_enabled") == "1"
    quality = (request.form.get("openrouter_openai_image2_default_quality") or "mid").strip().lower()
    set_openrouter_openai_image2_enabled(enabled)
    set_openrouter_openai_image2_default_quality(quality)

    if image_translate_channel in IMAGE_TRANSLATE_CHANNELS:
        set_image_translate_channel(image_translate_channel)
        image_translate_model = request.form.get("image_translate_default_model", "").strip()
        normalized_model = coerce_image_model(image_translate_model, channel=image_translate_channel)
        set_image_translate_default_model(image_translate_channel, normalized_model)
```

这里的关键点是：`coerce_image_model(..., channel="openrouter")` 在开关关闭时必须自动把不合法的 OpenAI Image 2 虚拟模型回退成普通 Gemini 默认模型。这个回退行为由 `appcore.gemini_image` 统一承担，不要在路由里手写字符串判断。

- [ ] **Step 4: 让 `web/routes/image_translate.py` 和 `web/routes/medias.py` 直接信任 `get_default_model()`**

这两个文件里的核心代码保持不大改，只确认不要缓存过时的合法模型集合。

`web/routes/image_translate.py` 里保留：

```python
    channel = _safe_image_translate_channel()
    if not is_valid_image_model(model_id, channel=channel):
        return jsonify({"error": "unsupported model"}), 400
```

`web/routes/medias.py` 里保留：

```python
def _default_image_translate_model_id() -> str:
    channel = "aistudio"
    try:
        channel = its.get_channel()
    except Exception:
        pass
    try:
        return its.get_default_model(channel)
    except Exception:
        return coerce_image_model("", channel=channel)
```

若实现中发现这两段已经有其他分支变动，只允许做最小调整，避免扩大路由层改动面。

- [ ] **Step 5: 运行受影响的路由测试并记录旧失败**

Run:

```bash
pytest tests/test_image_translate_routes.py -q
```

Expected:

- 新增 4 个与 OpenAI Image 2 相关的测试通过
- 旧的 7 个无关失败若仍存在，记录下来但不在本任务里顺手修

- [ ] **Step 6: Commit**

```bash
git add web/routes/settings.py web/routes/image_translate.py web/routes/medias.py tests/test_image_translate_routes.py
git commit -m "feat(image-routes): expose openai image2 variants for image translate"
```

---

### Task 4: 做收尾回归和测试环境验证清单

**Files:**
- Modify: `docs/superpowers/specs/2026-04-24-openrouter-openai-image2-image-translate-design.md`（仅在实现与 spec 偏离时回写）
- Test: `tests/test_image_translate_settings.py`
- Test: `tests/test_gemini_image.py`
- Test: `tests/test_settings_routes_new.py`
- Test: `tests/test_image_translate_routes.py`

- [ ] **Step 1: 运行聚焦测试套件**

Run:

```bash
pytest tests/test_image_translate_settings.py tests/test_gemini_image.py tests/test_settings_routes_new.py -q
```

Expected:

- 三个测试文件全部通过

- [ ] **Step 2: 运行图片翻译相关路由测试并区分旧失败**

Run:

```bash
pytest tests/test_image_translate_routes.py -q
```

Expected:

- 本次新增和受影响的 OpenAI Image 2 相关用例全部通过
- 若仍有旧失败，输出里应仅包含已知无关失败，不新增新的回归

- [ ] **Step 3: 检查最终 diff**

Run:

```bash
git diff --stat HEAD~3..HEAD
git diff -- appcore/image_translate_settings.py appcore/gemini_image.py web/routes/settings.py web/templates/settings.html web/routes/image_translate.py web/routes/medias.py tests/test_image_translate_settings.py tests/test_gemini_image.py tests/test_settings_routes_new.py tests/test_image_translate_routes.py
```

Expected:

- 只出现 spec 里列出的 10 个文件
- 没有无关格式化噪音或顺手改动

- [ ] **Step 4: 测试环境手动验证**

在测试环境 `http://172.30.254.14:8080/` 按下面顺序验证：

```text
1. 打开 /settings?tab=providers，把图片翻译通道切到 OpenRouter
2. 勾选“启用 OpenAI Image 2”，把默认质量设为 High，保存
3. 打开 /image-translate，确认模型备选出现 OpenAI Image 2（Low/Mid/High）
4. 创建一张英文商品图的 low 任务，确认详情页 model_id 为 openai/gpt-5.4-image-2:low
5. 创建一张英文商品图的 mid 任务，确认详情页 model_id 为 openai/gpt-5.4-image-2:mid
6. 创建一张英文商品图的 high 任务，确认详情页 model_id 为 openai/gpt-5.4-image-2:high
7. 回到 /settings 关闭开关并保存，再打开 /image-translate，确认 3 个 OpenAI Image 2 档位从新建页消失
8. 打开前面已创建的历史任务详情页，确认页面仍能正常显示和重试
```

Expected:

- 新建页受开关控制
- 历史任务不受开关关闭影响

- [ ] **Step 5: Commit**

如果实现与 spec 没有偏离，不需要再改 spec；只提交最终代码：

```bash
git add appcore/image_translate_settings.py appcore/gemini_image.py web/routes/settings.py web/templates/settings.html web/routes/image_translate.py web/routes/medias.py tests/test_image_translate_settings.py tests/test_gemini_image.py tests/test_settings_routes_new.py tests/test_image_translate_routes.py
git commit -m "feat(image-translate): add optional openrouter openai image2 quality tiers"
```

如果为了贴合现状微调了实现路径，同时补充提交：

```bash
git add docs/superpowers/specs/2026-04-24-openrouter-openai-image2-image-translate-design.md
git commit -m "docs(spec): sync openrouter openai image2 implementation notes"
```

---

## Self-Review

### Spec coverage

- “可选配置项”由 Task 1 的新配置函数、设置页控件和 POST 保存覆盖
- “放到图片翻译模型备选中”由 Task 2 的 `list_image_models("openrouter")` 扩展和 Task 3 的 `/api/image-translate/models` 覆盖
- “low / mid / high 三档接入方式”由 Task 2 的虚拟模型 ID 和质量映射覆盖
- “商品详情图一键翻译联动”由 Task 3 的 `_default_image_translate_model_id()` 回归验证覆盖
- “历史任务不受开关影响”由 Task 4 的测试环境手动验证覆盖

### Placeholder scan

- 无 `TODO` / `TBD` / “后续实现” 类占位词
- 所有代码步骤都包含具体代码或具体命令

### Type consistency

- 开关函数统一使用 `is_openrouter_openai_image2_enabled` / `set_openrouter_openai_image2_enabled`
- 默认质量函数统一使用 `get_openrouter_openai_image2_default_quality` / `set_openrouter_openai_image2_default_quality`
- 虚拟模型 ID 统一使用 `openai/gpt-5.4-image-2:low|mid|high`
- 运行时质量统一映射为 `low / medium / high`
