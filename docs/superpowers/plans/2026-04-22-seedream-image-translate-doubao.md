# Seedream 5.0 图片翻译接入 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有图片翻译链路中接入豆包 ARK 的 Seedream 5.0，并把它作为按通道可选的图片翻译模型，完成本地真实 Key 联调。

**Architecture:** 保持现有 `appcore.image_translate_runtime -> appcore.gemini_image.generate_image()` 调用协议不变，只把 `doubao` 扩展为新的图片翻译通道。模型列表从“全局平铺”改为“按通道注册”，图片翻译 API、`medias` 入口、`bulk_translate_runtime` 默认值都统一走通道兼容逻辑；Seedream 的请求体、鉴权、尺寸推断、错误分类则落在 `appcore/gemini_image.py` 的豆包分支里，避免引入更大的 `llm_client` 迁移。

**Tech Stack:** Flask, pytest, requests, Pillow, google-genai, OpenAI-compatible auth headers, local `.env`

---

## File Map

- Modify: `appcore/image_translate_settings.py`
  责任：扩展 `image_translate.channel` 支持 `doubao`，保留共享渠道标签的统一入口。
- Modify: `appcore/gemini_image.py`
  责任：把平铺 `IMAGE_MODELS` 重构为按通道模型注册表；新增 `list_image_models()` / `default_image_model()` / `coerce_image_model()`；实现 Seedream 请求、响应解析、错误分类，并在 `generate_image()` 中按通道分发。
- Modify: `web/routes/image_translate.py`
  责任：`/api/image-translate/models` 返回当前通道可用模型和兼容默认值；`upload/complete` 只接受当前通道合法模型；页面 badge 对 `doubao` 显示 Seedream 文案。
- Modify: `web/routes/medias.py`
  责任：商品详情图一键翻译默认模型改为按当前通道选择兼容模型。
- Modify: `appcore/bulk_translate_runtime.py`
  责任：批量翻译里封面图本地化默认模型也改为按当前通道选择，避免隐蔽回归。
- Modify: `web/routes/settings.py`
  责任：设置页下拉框给 `doubao` 通道显示更明确的 `豆包 ARK（Seedream）` 文案，但不影响其他模块对共享 `CHANNEL_LABELS` 的使用。
- Modify: `web/templates/settings.html`
  责任：更新“图片翻译通道”帮助文案，明确 `doubao` 走 ARK Seedream，复用 `doubao_llm` / `DOUBAO_LLM_API_KEY`。
- Modify: `tests/test_image_translate_settings.py`
  责任：覆盖 `doubao` 通道读写。
- Modify: `tests/test_gemini_image.py`
  责任：覆盖通道模型注册、Seedream 请求分支、鉴权链路、错误分类、尺寸策略。
- Modify: `tests/test_image_translate_routes.py`
  责任：覆盖按通道模型列表、默认模型回退、跨通道模型拒绝、`medias` 默认模型。
- Modify: `tests/test_bulk_translate_runtime.py`
  责任：覆盖批量封面翻译默认模型随通道变化。
- Modify: `tests/test_settings_routes_new.py`
  责任：覆盖设置页出现 `豆包 ARK（Seedream）` 文案和更新后的帮助说明。
- Local only, never commit: `.env`
  责任：保存真实 `DOUBAO_LLM_API_KEY` 以完成联调；不要修改 `.env.example`。

## Constraints And Invariants

- `appcore.image_translate_runtime.ImageTranslateRuntime` 的调用签名不改，继续只依赖 `(bytes, mime)`。
- `web/templates/_image_translate_scripts.html` 不改；继续消费 `/api/image-translate/models -> {items, default_model_id}` 这组现有字段。
- `image_translate.channel` 仍存 `system_settings.key = image_translate.channel`，不做 DB migration。
- 用户旧偏好 `api_keys.service = image_translate.extra.default_model_id` 允许继续存在，但如果与当前通道不兼容，必须自动回退为当前通道默认模型。
- `.env.example` 不写真实 key，也不新增默认明文值。

### Task 1: 通道与模型注册表基础

**Files:**
- Modify: `appcore/image_translate_settings.py:17-25,469-479`
- Modify: `appcore/gemini_image.py:32-40,89-119`
- Test: `tests/test_image_translate_settings.py`
- Test: `tests/test_gemini_image.py`

- [ ] **Step 1: 先写失败测试，锁定 `doubao` 通道和按通道模型注册行为**

```python
def test_get_channel_accepts_doubao(monkeypatch):
    from appcore import image_translate_settings as its

    store = {}
    _patch_store(monkeypatch, store)
    its.set_channel("DOUBAO")

    assert store["image_translate.channel"] == "doubao"
    assert its.get_channel() == "doubao"


def test_image_model_registry_is_channel_scoped():
    from appcore import gemini_image

    assert gemini_image.default_image_model("doubao") == "doubao-seedream-5-0-260128"
    assert gemini_image.is_valid_image_model(
        "doubao-seedream-5-0-260128",
        channel="doubao",
    )
    assert not gemini_image.is_valid_image_model(
        "gemini-3-pro-image-preview",
        channel="doubao",
    )
    assert gemini_image.coerce_image_model(
        "gemini-3-pro-image-preview",
        channel="doubao",
    ) == "doubao-seedream-5-0-260128"
```

- [ ] **Step 2: 运行测试确认它们先失败**

Run: `pytest tests/test_image_translate_settings.py tests/test_gemini_image.py -q -k "doubao or channel_scoped"`

Expected: 失败，至少出现以下一种报错：
- `ValueError: unsupported channel: DOUBAO`
- `AttributeError: module 'appcore.gemini_image' has no attribute 'default_image_model'`

- [ ] **Step 3: 实现最小模型注册表与通道兼容辅助函数**

```python
# appcore/image_translate_settings.py
CHANNELS: tuple[str, ...] = ("aistudio", "cloud", "openrouter", "doubao")
CHANNEL_LABELS: dict[str, str] = {
    "aistudio": "Google AI Studio",
    "cloud": "Google Cloud (Vertex AI)",
    "openrouter": "OpenRouter",
    "doubao": "豆包",
}


# appcore/gemini_image.py
IMAGE_MODELS_BY_CHANNEL: dict[str, list[tuple[str, str]]] = {
    "aistudio": [
        ("gemini-3.1-flash-image-preview", "Nano Banana 2（快速）"),
        ("gemini-3-pro-image-preview", "Nano Banana Pro（高保真）"),
    ],
    "cloud": [
        ("gemini-3.1-flash-image-preview", "Nano Banana 2（快速）"),
        ("gemini-3-pro-image-preview", "Nano Banana Pro（高保真）"),
    ],
    "openrouter": [
        ("gemini-3.1-flash-image-preview", "Nano Banana 2（快速）"),
        ("gemini-3-pro-image-preview", "Nano Banana Pro（高保真）"),
    ],
    "doubao": [
        ("doubao-seedream-5-0-260128", "Seedream 5.0（豆包）"),
    ],
}
IMAGE_MODELS: list[tuple[str, str]] = list(IMAGE_MODELS_BY_CHANNEL["aistudio"])


def normalize_image_channel(channel: str | None) -> str:
    value = (channel or "").strip().lower()
    return value if value in IMAGE_MODELS_BY_CHANNEL else "aistudio"


def list_image_models(channel: str | None = None) -> list[tuple[str, str]]:
    return list(IMAGE_MODELS_BY_CHANNEL[normalize_image_channel(channel)])


def default_image_model(channel: str | None = None) -> str:
    models = list_image_models(channel)
    return models[0][0] if models else "gemini-3.1-flash-image-preview"


def is_valid_image_model(model_id: str, channel: str | None = None) -> bool:
    return any(mid == model_id for mid, _ in list_image_models(channel))


def coerce_image_model(model_id: str | None, channel: str | None = None) -> str:
    if model_id and is_valid_image_model(model_id, channel=channel):
        return model_id
    return default_image_model(channel)
```

- [ ] **Step 4: 回跑基础测试，确认注册表已稳定**

Run: `pytest tests/test_image_translate_settings.py tests/test_gemini_image.py -q`

Expected: `passed`，并且 `tests/test_image_translate_settings.py` / `tests/test_gemini_image.py` 不再出现 `unsupported channel` 或缺失 helper 的错误。

- [ ] **Step 5: 提交这一层基础改动**

```bash
git add appcore/image_translate_settings.py appcore/gemini_image.py tests/test_image_translate_settings.py tests/test_gemini_image.py
git commit -m "feat: add channel-scoped image model registry"
```

### Task 2: 让图片翻译入口按通道返回模型并修正默认值

**Files:**
- Modify: `web/routes/image_translate.py:20-33,192-197,261-280`
- Modify: `web/routes/medias.py:24,203-215,1750`
- Modify: `appcore/bulk_translate_runtime.py:603-680`
- Test: `tests/test_image_translate_routes.py`
- Test: `tests/test_bulk_translate_runtime.py`

- [ ] **Step 1: 先写失败测试，覆盖模型列表、默认值回退和跨通道拒绝**

```python
def test_models_endpoint_returns_seedream_when_channel_is_doubao(
    authed_client_no_db, monkeypatch
):
    from web.routes import image_translate as r

    monkeypatch.setattr(r.its, "get_channel", lambda: "doubao")
    monkeypatch.setattr(
        "appcore.api_keys.resolve_extra",
        lambda uid, svc: {"default_model_id": "gemini-3-pro-image-preview"},
    )

    resp = authed_client_no_db.get("/api/image-translate/models")

    assert resp.status_code == 200
    assert resp.get_json() == {
        "items": [
            {"id": "doubao-seedream-5-0-260128", "name": "Seedream 5.0（豆包）"},
        ],
        "default_model_id": "doubao-seedream-5-0-260128",
    }


def test_complete_rejects_gemini_model_when_channel_is_doubao(
    authed_client_no_db, monkeypatch
):
    from web.routes import image_translate as r

    _patch_tos_and_runner(monkeypatch)
    _patch_lang(monkeypatch)
    _patch_task_state(monkeypatch)
    monkeypatch.setattr(r.its, "get_channel", lambda: "doubao")

    bootstrap = authed_client_no_db.post(
        "/api/image-translate/upload/bootstrap",
        json={"files": [{"filename": "a.jpg", "size": 1, "content_type": "image/jpeg"}]},
    ).get_json()

    resp = authed_client_no_db.post(
        "/api/image-translate/upload/complete",
        json={
            "task_id": bootstrap["task_id"],
            "preset": "cover",
            "target_language": "de",
            "model_id": "gemini-3-pro-image-preview",
            "prompt": "x",
            "product_name": "p",
            "uploaded": [{"idx": 0, "object_key": bootstrap["uploads"][0]["object_key"], "filename": "a.jpg"}],
        },
    )

    assert resp.status_code == 400
    assert resp.get_json()["error"] == "unsupported model"


def test_bulk_translate_default_image_model_respects_doubao_channel(monkeypatch):
    from appcore import bulk_translate_runtime as mod

    monkeypatch.setattr(
        "appcore.api_keys.resolve_extra",
        lambda uid, svc: {"default_model_id": "gemini-3-pro-image-preview"},
    )
    monkeypatch.setattr(mod.its, "get_channel", lambda: "doubao")

    assert mod._default_image_translate_model_id(1) == "doubao-seedream-5-0-260128"


def test_medias_default_image_model_uses_seedream_when_channel_is_doubao(
    authed_client_no_db, monkeypatch
):
    from web.routes import medias as r

    created = {}
    monkeypatch.setattr(r.medias, "get_product", lambda pid: {"id": pid, "user_id": 1, "name": "灯"})
    monkeypatch.setattr(r, "_can_access_product", lambda product: True)
    monkeypatch.setattr(
        r.medias,
        "list_detail_images",
        lambda pid, lang: [{"id": 11, "object_key": "1/medias/1/a.jpg"}] if lang == "en" else [],
    )
    monkeypatch.setattr(r.medias, "is_valid_language", lambda code: code in {"en", "de"})
    monkeypatch.setattr(r.medias, "get_language_name", lambda lang: "德语")
    monkeypatch.setattr(r.its, "get_prompts_for_lang", lambda lang: {"detail": "翻 {target_language_name}"})
    monkeypatch.setattr(r.its, "get_channel", lambda: "doubao")
    monkeypatch.setattr("appcore.api_keys.resolve_extra", lambda uid, svc: {})
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
    assert created["model_id"] == "doubao-seedream-5-0-260128"
```

- [ ] **Step 2: 运行这些测试，确认旧逻辑确实失败**

Run: `pytest tests/test_image_translate_routes.py tests/test_bulk_translate_runtime.py -q -k "doubao or default_image_model"`

Expected: 失败，旧代码会把 Gemini 模型当成全局合法模型，并且默认值仍落在 `gemini-3.1-flash-image-preview`。

- [ ] **Step 3: 在三个入口统一接入 `coerce_image_model()`**

```python
# web/routes/image_translate.py
from appcore.gemini_image import (
    coerce_image_model,
    is_valid_image_model,
    list_image_models,
)

_BACKEND_LABELS = {
    "aistudio": "Google AI Studio",
    "cloud": "Google Cloud (Vertex AI)",
    "openrouter": "OpenRouter",
    "doubao": "豆包 ARK（Seedream）",
}


def _current_image_channel() -> str:
    try:
        return its.get_channel()
    except Exception:
        return "aistudio"


def _preferred_model_for_channel(user_id: int, channel: str) -> str:
    from appcore.api_keys import resolve_extra

    extra = resolve_extra(user_id, "image_translate") or {}
    preferred = (extra.get("default_model_id") or "").strip()
    return coerce_image_model(preferred, channel=channel)


def api_models():
    channel = _current_image_channel()
    return jsonify({
        "items": [{"id": mid, "name": label} for mid, label in list_image_models(channel)],
        "default_model_id": _preferred_model_for_channel(current_user.id, channel),
    })


def api_upload_complete():
    channel = _current_image_channel()
    if not is_valid_image_model(model_id, channel=channel):
        return jsonify({"error": "unsupported model"}), 400


# web/routes/medias.py / appcore/bulk_translate_runtime.py
channel = its.get_channel()
preferred = (extra.get("default_model_id") or "").strip()
return coerce_image_model(preferred, channel=channel)
```

- [ ] **Step 4: 回跑入口测试，确认页面 API 和后台默认值一致**

Run: `pytest tests/test_image_translate_routes.py tests/test_bulk_translate_runtime.py -q`

Expected: `passed`，并且以下断言成立：
- `/api/image-translate/models` 在 `doubao` 通道下只返回 Seedream。
- `upload/complete` 在跨通道模型时返回 `400 unsupported model`。
- `medias` 与 `bulk_translate_runtime` 默认模型跟通道一致。

- [ ] **Step 5: 提交入口层改动**

```bash
git add web/routes/image_translate.py web/routes/medias.py appcore/bulk_translate_runtime.py tests/test_image_translate_routes.py tests/test_bulk_translate_runtime.py
git commit -m "feat: make image translate defaults channel-aware"
```

### Task 3: 在 `gemini_image` 里实现 Seedream 5.0 调用分支

**Files:**
- Modify: `appcore/gemini_image.py:99-119,317-383`
- Test: `tests/test_gemini_image.py`
- Test: `tests/test_image_translate_runtime.py`

- [ ] **Step 1: 先写失败测试，覆盖真实分支约束**

```python
def _seedream_response(payload: bytes, *, status_code: int = 200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = "seedream"
    resp.json.return_value = {
        "data": [{"b64_json": base64.b64encode(payload).decode("ascii")}],
    }
    return resp


def test_generate_image_doubao_channel_skips_gemini_resolve_config():
    from appcore import gemini_image

    with patch.object(gemini_image, "_resolve_channel", return_value="doubao"), \
         patch.object(gemini_image, "resolve_config", side_effect=AssertionError("should not resolve Gemini config")), \
         patch("appcore.gemini_image.requests.post", return_value=_seedream_response(b"PNG")), \
         patch.object(gemini_image.ai_billing, "log_request"):
        out, mime = gemini_image.generate_image(
            prompt="翻译",
            source_image=b"RAW",
            source_mime="image/png",
            model="doubao-seedream-5-0-260128",
            user_id=1,
            project_id="seedream-smoke",
        )

    assert out == b"PNG"
    assert mime == "image/png"


def test_generate_image_doubao_channel_maps_429_to_retryable():
    from appcore import gemini_image

    resp = _seedream_response(b"", status_code=429)
    resp.json.return_value = {"error": {"message": "Too Many Requests"}}

    with patch.object(gemini_image, "_resolve_channel", return_value="doubao"), \
         patch("appcore.gemini_image.requests.post", return_value=resp):
        with pytest.raises(gemini_image.GeminiImageRetryable):
            gemini_image.generate_image(
                prompt="x",
                source_image=b"RAW",
                source_mime="image/png",
                model="doubao-seedream-5-0-260128",
            )


def test_generate_image_doubao_channel_maps_401_to_non_retryable():
    from appcore import gemini_image

    resp = _seedream_response(b"", status_code=401)
    resp.json.return_value = {"error": {"message": "Unauthorized"}}

    with patch.object(gemini_image, "_resolve_channel", return_value="doubao"), \
         patch("appcore.gemini_image.requests.post", return_value=resp):
        with pytest.raises(gemini_image.GeminiImageError):
            gemini_image.generate_image(
                prompt="x",
                source_image=b"RAW",
                source_mime="image/png",
                model="doubao-seedream-5-0-260128",
            )


def test_resolve_seedream_size_prefers_source_dimensions():
    import io
    from PIL import Image
    from appcore import gemini_image

    buf = io.BytesIO()
    Image.new("RGB", (320, 640), "white").save(buf, format="PNG")

    assert gemini_image._resolve_seedream_size(buf.getvalue()) == "320x640"
    assert gemini_image._resolve_seedream_size(b"not-an-image") == "2K"
```

- [ ] **Step 2: 运行测试，确认旧 dispatcher 没有豆包分支**

Run: `pytest tests/test_gemini_image.py tests/test_image_translate_runtime.py -q -k "doubao or seedream"`

Expected: 失败，常见错误包括：
- `AssertionError: should not resolve Gemini config`
- `requests.post` 从未被调用
- `GeminiImageError` / `GeminiImageRetryable` 映射不符合预期

- [ ] **Step 3: 在 `gemini_image.py` 增加 Seedream 请求体、鉴权链路和分发**

```python
import io
import requests
from PIL import Image
from appcore.api_keys import resolve_extra, resolve_key
from config import DOUBAO_LLM_API_KEY, DOUBAO_LLM_BASE_URL


def _resolve_doubao_credentials(user_id: int | None) -> tuple[str, str]:
    key = (
        resolve_key(user_id, "doubao_llm", "DOUBAO_LLM_API_KEY")
        if user_id is not None
        else DOUBAO_LLM_API_KEY
    )
    extra = resolve_extra(user_id, "doubao_llm") if user_id else {}
    return key or "", extra.get("base_url") or DOUBAO_LLM_BASE_URL


def _resolve_seedream_size(source_image: bytes) -> str:
    try:
        with Image.open(io.BytesIO(source_image)) as image:
            width, height = image.size
        if width > 0 and height > 0:
            return f"{width}x{height}"
    except Exception:
        logger.debug("resolve seedream size failed", exc_info=True)
    return "2K"


def _generate_via_seedream(
    prompt: str,
    source_image: bytes,
    source_mime: str,
    model_id: str,
    *,
    user_id: int | None,
) -> tuple[bytes, str, dict]:
    api_key, base_url = _resolve_doubao_credentials(user_id)
    if not api_key:
        raise GeminiImageError(
            "豆包 ARK API key 未配置（请在系统设置中配置 doubao_llm，或设置 DOUBAO_LLM_API_KEY / VOLC_API_KEY）"
        )

    payload = {
        "model": model_id,
        "prompt": prompt,
        "image": f"data:{source_mime or 'image/png'};base64,{base64.b64encode(source_image).decode('ascii')}",
        "size": _resolve_seedream_size(source_image),
        "sequential_image_generation": "disabled",
        "response_format": "b64_json",
        "output_format": "png",
        "stream": False,
        "watermark": False,
    }
    try:
        resp = requests.post(
            f"{base_url.rstrip('/')}/images/generations",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=120,
        )
    except requests.Timeout as exc:
        raise GeminiImageRetryable("Seedream 请求超时") from exc
    except requests.RequestException as exc:
        raise GeminiImageRetryable(f"Seedream 网络错误：{exc}") from exc

    if resp.status_code in {429, 500, 502, 503, 504}:
        raise GeminiImageRetryable(resp.text or f"Seedream HTTP {resp.status_code}")
    if resp.status_code >= 400:
        raise GeminiImageError(resp.text or f"Seedream HTTP {resp.status_code}")

    data = (resp.json().get("data") or [{}])[0]
    image_b64 = (data.get("b64_json") or "").strip()
    if not image_b64:
        raise GeminiImageError("Seedream 未返回 b64_json 图像数据")
    return base64.b64decode(image_b64), "image/png", resp.json()


def _channel_provider(channel: str) -> str:
    if channel == "doubao":
        return "doubao"
    if channel == "openrouter":
        return "openrouter"
    if channel == "cloud":
        return "gemini_vertex"
    return "gemini_aistudio"


def generate_image(
    prompt: str,
    *,
    source_image: bytes,
    source_mime: str,
    model: str,
    user_id: int | None = None,
    project_id: str | None = None,
    service: str = "image_translate.generate",
) -> tuple[bytes, str]:
    channel = _resolve_channel()
    model_id = coerce_image_model(model, channel=channel)
    provider = _channel_provider(channel)

    try:
        if channel == "doubao":
            image_bytes, mime, resp = _generate_via_seedream(
                prompt,
                source_image,
                source_mime,
                model_id,
                user_id=user_id,
            )
            input_tokens = output_tokens = None
            response_cost_cny = None
        elif channel == "openrouter":
            image_bytes, mime, resp = _generate_via_openrouter(
                prompt,
                source_image,
                source_mime,
                model_id,
                api_key=OPENROUTER_API_KEY,
            )
            usage = getattr(resp, "usage", None)
            input_tokens = getattr(usage, "prompt_tokens", None) if usage else None
            output_tokens = getattr(usage, "completion_tokens", None) if usage else None
            response_cost_cny = _extract_openrouter_cost_cny(resp)
        else:
            api_key_from_gemini, _ = resolve_config(
                user_id,
                service=service,
                default_model=model_id,
            )
            api_key = GEMINI_CLOUD_API_KEY if channel == "cloud" else (api_key_from_gemini or GEMINI_AISTUDIO_API_KEY)
            image_bytes, mime, resp = _generate_via_genai(
                prompt,
                source_image,
                source_mime,
                model_id,
                backend=channel,
                api_key=api_key,
            )
            meta = getattr(resp, "usage_metadata", None)
            input_tokens = int(getattr(meta, "prompt_token_count", 0) or 0) if meta else None
            output_tokens = int(getattr(meta, "candidates_token_count", 0) or 0) if meta else None
            response_cost_cny = None
    except Exception as e:
        _log_usage(
            user_id=user_id,
            project_id=project_id,
            use_case_code=service,
            provider=provider,
            model_id=model_id,
            image_bytes_len=None,
            input_tokens=None,
            output_tokens=None,
            channel=channel,
            success=False,
            error=e,
        )
        raise

    _log_usage(
        user_id=user_id,
        project_id=project_id,
        use_case_code=service,
        provider=provider,
        model_id=model_id,
        image_bytes_len=len(image_bytes),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        channel=channel,
        response_cost_cny=response_cost_cny,
        success=True,
    )
    return image_bytes, mime
```

- [ ] **Step 4: 跑完 Seedream + runtime 回归**

Run: `pytest tests/test_gemini_image.py tests/test_image_translate_runtime.py -q`

Expected: `passed`，并且：
- `doubao` 分支不再依赖 `resolve_config()`
- `429/5xx` 走 `GeminiImageRetryable`
- `401/403/422/缺少 b64_json` 走 `GeminiImageError`
- runtime 仍然只处理 `(bytes, mime)`，不需要改调用协议

- [ ] **Step 5: 提交 Seedream 调用实现**

```bash
git add appcore/gemini_image.py tests/test_gemini_image.py tests/test_image_translate_runtime.py
git commit -m "feat: add seedream image generation channel"
```

### Task 4: 设置页文案与 Seedream 通道展示

**Files:**
- Modify: `web/routes/settings.py:97-100,111-133`
- Modify: `web/templates/settings.html:145-156`
- Test: `tests/test_settings_routes_new.py`

- [ ] **Step 1: 先写失败测试，锁定设置页里对 Seedream 的用户可见文案**

```python
def test_settings_get_renders_seedream_channel_label(admin_no_db_client):
    with patch("web.routes.settings.get_all", return_value={}), \
         patch("web.routes.settings.llm_bindings.list_all", return_value=[]), \
         patch("web.routes.settings.get_image_translate_channel", return_value="doubao"):
        resp = admin_no_db_client.get("/settings")

    body = resp.get_data(as_text=True)
    assert "豆包 ARK（Seedream）" in body
    assert "DOUBAO_LLM_API_KEY" in body
```

- [ ] **Step 2: 运行设置页测试确认旧模板没有这些提示**

Run: `pytest tests/test_settings_routes_new.py -q -k "seedream"`

Expected: 失败，因为当前模板仍写着“调用 Gemini 图像模型”，也不会出现 `豆包 ARK（Seedream）`。

- [ ] **Step 3: 在设置页把通道文案改成 Seedream 感知版本**

```python
# web/routes/settings.py
IMAGE_TRANSLATE_CHANNEL_DISPLAY_LABELS = {
    **IMAGE_TRANSLATE_CHANNEL_LABELS,
    "doubao": "豆包 ARK（Seedream）",
}

image_translate_channels=[
    (code, IMAGE_TRANSLATE_CHANNEL_DISPLAY_LABELS.get(code, code))
    for code in IMAGE_TRANSLATE_CHANNELS
]
```

```html
<p class="hint">
  选择图片翻译走哪个通道。
  AI Studio / Google Cloud 继续调用 Gemini 图像模型；
  OpenRouter 调用 OpenRouter 的 Gemini 图像能力；
  豆包 ARK 调用 Seedream 5.0，并复用下方豆包 ARK 的 Key，或环境变量
  <code>DOUBAO_LLM_API_KEY</code>（未设置时继续回落 <code>VOLC_API_KEY</code>）。
</p>
```

- [ ] **Step 4: 回跑设置页与图片翻译路由 smoke 测试**

Run: `pytest tests/test_settings_routes_new.py tests/test_image_translate_routes.py -q`

Expected: `passed`，并且设置页 HTML 中能看到 `豆包 ARK（Seedream）` 与更新后的帮助文案。

- [ ] **Step 5: 提交 UI 文案调整**

```bash
git add web/routes/settings.py web/templates/settings.html tests/test_settings_routes_new.py
git commit -m "feat: describe seedream image translate channel in settings"
```

### Task 5: 完整回归、本地 Key 存储与真实 API 联调

**Files:**
- Verify: `tests/test_image_translate_settings.py`
- Verify: `tests/test_gemini_image.py`
- Verify: `tests/test_image_translate_routes.py`
- Verify: `tests/test_image_translate_runtime.py`
- Verify: `tests/test_bulk_translate_runtime.py`
- Verify: `tests/test_settings_routes_new.py`
- Local only: `.env`

- [ ] **Step 1: 先跑一遍完整回归**

Run: `pytest tests/test_image_translate_settings.py tests/test_gemini_image.py tests/test_image_translate_routes.py tests/test_image_translate_runtime.py tests/test_bulk_translate_runtime.py tests/test_settings_routes_new.py -q`

Expected: 所有测试通过；若有额外回归，再补修后重跑同一命令直到绿灯。

- [ ] **Step 2: 把真实 Key 只写进本地 `.env`，不要提交**

要求：
- 打开工作区本地 `.env`，把 `DOUBAO_LLM_API_KEY=` 这一行直接填成用户提供的真实 key
- 只改工作区本地 `.env`
- 不改 `.env.example`
- 不把真实 key 写进测试代码、计划文档、commit message、截图或日志

- [ ] **Step 3: 用公开入口 `generate_image()` 做一次真实 Seedream smoke test**

```powershell
@'
import io
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from appcore import gemini_image

buf = io.BytesIO()
Image.new("RGB", (256, 256), "white").save(buf, format="PNG")

with patch("appcore.gemini_image._resolve_channel", return_value="doubao"):
    out, mime = gemini_image.generate_image(
        prompt="在白底海报中央生成一句德语 Hallo，保持简洁排版和清晰文字",
        source_image=buf.getvalue(),
        source_mime="image/png",
        model="doubao-seedream-5-0-260128",
        user_id=None,
        project_id="seedream-smoke",
    )

Path("output").mkdir(exist_ok=True)
out_path = Path("output/seedream-smoke.png")
out_path.write_bytes(out)
print({"mime": mime, "bytes": len(out), "path": str(out_path)})
'@ | python -
```

Expected:
- 终端输出 `mime=image/png` 或 JSON 中的 `"mime": "image/png"`
- `bytes` 明显大于 `100`
- 生成 `output/seedream-smoke.png`

- [ ] **Step 4: 验证生成结果是可打开的真实图片**

Run: `@'from PIL import Image; Image.open("output/seedream-smoke.png").load(); print("ok")'@ | python -`

Expected: 输出 `ok`，说明返回的 `b64_json` 已被系统代码正确解码并写盘。

- [ ] **Step 5: 清理并确认没有把本地密钥带进 Git**

Run: `git status --short`

Expected:
- 不出现 `.env` 被暂存或待提交
- 只剩下预期代码改动，或者工作区已经干净
- `output/seedream-smoke.png` 如不打算保留，手动删除后再执行一次 `git status --short`
