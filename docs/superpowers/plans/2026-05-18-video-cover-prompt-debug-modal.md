# Video Cover Prompt Debug Modal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand the 文案封面生成 prompt modal into a structured request/result debugger with full request payloads and cover-generation replay.

**Architecture:** Keep production step execution unchanged. Add read-only debug payload builders in `web/routes/video_cover.py`, add a separate replay endpoint that reconstructs the saved cover-generation request without writing project state, and replace the prompt modal body in `web/templates/video_cover_detail.html` with nested tabs that consume the new debug payload. Tests stay in `tests/test_video_cover_generation.py` because this module already owns route, template, and service coverage for 文案封面生成.

**Tech Stack:** Flask, Jinja2, vanilla JavaScript, pytest, existing `appcore.video_cover_generation`, existing `appcore.llm_provider_configs`, existing `local_media_storage`.

---

## Docs Anchor

- `docs/superpowers/specs/2026-05-18-video-cover-prompt-debug-modal-design.md#目标`
- `docs/superpowers/specs/2026-05-18-video-cover-prompt-debug-modal-design.md#完整报文`
- `docs/superpowers/specs/2026-05-18-video-cover-prompt-debug-modal-design.md#调试窗口`
- `docs/superpowers/specs/2026-05-18-video-cover-prompt-debug-modal-design.md#返回数据`
- `web/templates/CLAUDE.md#CSRF / 路由守卫`
- `web/static/CLAUDE.md#Ocean Blue 设计系统`

## File Structure

- Modify `tests/test_video_cover_generation.py`: add regression tests for modal tabs, debug payload route, debug replay route, and non-cover replay rejection.
- Modify `web/routes/video_cover.py`: add debug payload helpers, provider credential lookup, reference image lookup, full request construction, and two admin routes.
- Modify `web/templates/video_cover_detail.html`: widen the prompt modal, add top-level and nested tabs, render structured request/result panes, load debug payloads, and post debug replay requests with CSRF.

## Task 1: Template Regression Test

**Files:**
- Modify: `tests/test_video_cover_generation.py`

- [ ] **Step 1: Add a failing modal layout test**

Add this test near `test_video_cover_detail_renders_progress_restart_and_four_process_cards`:

```python
def test_video_cover_prompt_modal_has_request_result_tabs_and_debug_form(authed_client_no_db, monkeypatch):
    from web.routes import video_cover

    state = {
        "id": "task-1",
        "type": "video_cover",
        "product_url": "https://shop.example/products/lamp",
        "display_name": "Lamp",
        "image_count": 1,
        "steps": {
            "video_analysis": "done",
            "product_analysis": "done",
            "ad_copy": "done",
            "cover_generation": "done",
        },
        "step_requests": {
            "cover_generation": {
                "provider": "local",
                "model": "gpt-image-2",
                "request_data": {"image_count": 1, "execution_mode": "serial"},
                "image_prompts": [{"index": 1, "prompt": "actual prompt", "source_ad_copy_id": 1}],
            }
        },
        "step_results": {
            "cover_generation": {
                "raw_response": {"data": [{"b64_json": "iVBORw0KGgo="}]},
                "structured_result": {"covers": [{"index": 1, "hook": "Love the breeze"}]},
            }
        },
        "result": {
            "reference": {"object_key": "artifacts/video_cover/1/task-1/reference.png"},
            "covers": [{"platform": "social_reels", "index": 1, "object_key": "artifacts/video_cover/1/task-1/social_reels.png"}],
        },
    }
    row = {"id": "task-1", "state_json": json.dumps(state, ensure_ascii=False), "display_name": "Lamp"}
    monkeypatch.setattr(
        video_cover.video_cover_project_store,
        "get_project",
        lambda task_id, *, user_id, is_admin: row,
    )

    resp = authed_client_no_db.get("/video-cover/task-1")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "vcd-prompt-debug-modal" in html
    assert "width:min(80vw, 1600px)" in html
    assert 'data-prompt-root-tab="request"' in html
    assert 'data-prompt-root-tab="result"' in html
    assert 'data-prompt-subtab="request-data"' in html
    assert 'data-prompt-subtab="full-request"' in html
    assert 'data-prompt-subtab="response-data"' in html
    assert 'data-prompt-subtab="raw-response"' in html
    assert "请求数据" in html
    assert "完整报文" in html
    assert "返回数据" in html
    assert "返回结果报文" in html
    assert 'id="vcdDebugRequestUrl"' in html
    assert 'id="vcdDebugApiKey"' in html
    assert 'id="vcdDebugReplayBtn"' in html
    assert "debug-payload" in html
    assert "debug-replay" in html
    assert "'X-CSRFToken': csrfToken()" in html
```

- [ ] **Step 2: Run the failing test**

Run:

```bash
pytest tests/test_video_cover_generation.py::test_video_cover_prompt_modal_has_request_result_tabs_and_debug_form -q
```

Expected: FAIL because the current modal still renders a single `pre` and has no nested tabs or debug form.

## Task 2: Debug Payload Route Tests

**Files:**
- Modify: `tests/test_video_cover_generation.py`

- [ ] **Step 1: Add a fake provider config helper**

Add this helper near `_FakeProduct`:

```python
class _FakeProviderConfig:
    def __init__(self, api_key="sk-local-test", base_url="http://image.local/v1", model_id=None, extra_config=None):
        self.api_key = api_key
        self.base_url = base_url
        self.model_id = model_id
        self.extra_config = extra_config or {}

    def require_api_key(self):
        if not self.api_key:
            raise AssertionError("missing api key in test provider config")
        return self.api_key

    def require_base_url(self, default=None):
        return (self.base_url or default or "").rstrip("/")
```

- [ ] **Step 2: Add a failing GET debug-payload test**

Add this test after the state endpoint tests:

```python
def test_video_cover_debug_payload_returns_cover_full_request(authed_client_no_db, monkeypatch):
    from web.routes import video_cover

    copy_item = {
        "id": 1,
        "english": {
            "title": "Love the breeze",
            "message": "Stop choosing between fresh air and mosquito bites.",
            "description": "Keep bugs out",
        },
    }
    state = {
        "id": "task-1",
        "type": "video_cover",
        "product": {
            "title": "Bug-Proof Car Sunshade Screen",
            "main_image_url": "https://cdn.example/product.jpg",
            "product_image_path": "/data/product.jpg",
        },
        "product_url": "https://shop.example/products/screen",
        "video_filename": "screen.mp4",
        "image_count": 1,
        "step_requests": {
            "cover_generation": {
                "provider": "local",
                "model": "gpt-image-2",
                "alias": "gpt_image_2",
                "request_data": {"image_count": 1, "execution_mode": "serial", "ad_copy_sets": {"ad_copy_sets": [copy_item]}},
                "image_prompts": [{"index": 1, "prompt": "actual prompt with native hook text", "source_ad_copy_id": 1}],
            }
        },
        "step_results": {
            "cover_generation": {
                "raw_response": {"data": [{"b64_json": "iVBORw0KGgo="}]},
                "structured_result": {"covers": [{"index": 1, "hook": "Love the breeze"}]},
            }
        },
        "result": {
            "reference": {"object_key": "artifacts/video_cover/8/task-1/reference.png"},
            "models": {"cover_generation": {"provider": "local", "model_id": "gpt-image-2", "execution_mode": "serial"}},
            "covers": [{"platform": "social_reels", "index": 1, "object_key": "artifacts/video_cover/8/task-1/social_reels.png", "copy": copy_item}],
        },
    }
    row = {"id": "task-1", "state_json": json.dumps(state, ensure_ascii=False), "display_name": "Screen"}
    monkeypatch.setattr(video_cover.video_cover_project_store, "get_project", lambda task_id, *, user_id, is_admin: row)
    monkeypatch.setattr(video_cover, "get_provider_config", lambda code: _FakeProviderConfig())

    resp = authed_client_no_db.get("/video-cover/api/task-1/debug-payload/cover_generation")

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["ok"] is True
    data = payload["data"]
    assert data["label"] == "封面生成"
    assert data["request_data"]["image_prompts"][0]["prompt"] == "actual prompt with native hook text"
    assert data["response_data"]["covers"][0]["hook"] == "Love the breeze"
    assert data["full_request"]["method"] == "POST"
    assert data["full_request"]["url"] == "http://image.local/v1/images/edits"
    assert data["full_request"]["headers"]["Authorization"] == "Bearer sk-local-test"
    assert data["full_request"]["api_key"] == "sk-local-test"
    assert data["full_request"]["body"]["model"] == "gpt-image-2"
    assert data["full_request"]["body"]["prompt"] == "actual prompt with native hook text"
    assert data["full_request"]["files"][0]["field"] == "image"
    assert data["full_request"]["files"][0]["source"] == "artifacts/video_cover/8/task-1/reference.png"
    assert data["replay"]["supported"] is True
    assert data["raw_response"]["data"][0]["b64_json"] == "iVBORw0KGgo="
```

- [ ] **Step 3: Add a failing text-step debug-payload test**

Add:

```python
def test_video_cover_debug_payload_returns_text_step_without_replay(authed_client_no_db, monkeypatch):
    from web.routes import video_cover

    state = {
        "id": "task-1",
        "type": "video_cover",
        "step_requests": {
            "ad_copy": {
                "provider": "openrouter",
                "model": "google/gemini-3-flash-preview",
                "messages": [{"role": "user", "content": "write ad copy"}],
                "request_data": {"product_title": "Screen"},
            }
        },
        "step_results": {
            "ad_copy": {
                "raw_response": {"ad_copy_sets": []},
                "structured_result": {"ad_copy_sets": []},
            }
        },
    }
    row = {"id": "task-1", "state_json": json.dumps(state, ensure_ascii=False), "display_name": "Screen"}
    monkeypatch.setattr(video_cover.video_cover_project_store, "get_project", lambda task_id, *, user_id, is_admin: row)

    resp = authed_client_no_db.get("/video-cover/api/task-1/debug-payload/ad_copy")

    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["full_request"]["body"]["messages"][0]["content"] == "write ad copy"
    assert data["replay"]["supported"] is False
```

- [ ] **Step 4: Run the failing route tests**

Run:

```bash
pytest tests/test_video_cover_generation.py::test_video_cover_debug_payload_returns_cover_full_request tests/test_video_cover_generation.py::test_video_cover_debug_payload_returns_text_step_without_replay -q
```

Expected: FAIL because the debug-payload route does not exist.

## Task 3: Implement Debug Payload Helpers And GET Route

**Files:**
- Modify: `web/routes/video_cover.py`

- [ ] **Step 1: Add imports**

Modify the imports at the top of `web/routes/video_cover.py`:

```python
import base64
from datetime import date
import json
import mimetypes
import os
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import requests
```

Extend existing imports:

```python
from appcore.llm_provider_configs import get_provider_config
from appcore.video_cover_generation import (
    SOCIAL_REELS_SPEC,
    VideoCoverGenerationError,
    build_ad_copy_prompt,
    build_platform_prompt,
    build_product_analysis_prompt,
    build_video_analysis_prompt,
    generate_ad_copy_sets,
    generate_product_analysis,
    generate_video_analysis,
    generate_video_covers,
    normalize_cover_execution_mode,
    normalize_image_count,
    normalize_product_image_jpg,
    resolve_cover_model_selection,
    resolve_text_model_selection,
    video_cover_model_options,
    _decode_image_response_payload,
)
```

- [ ] **Step 2: Add helper functions before `api_run_project_step`**

Add:

```python
def _json_safe(value):
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except TypeError:
        return str(value)


def _cover_provider_config_code(provider: str) -> str:
    normalized = (provider or "").strip()
    if normalized == "local":
        return "video_cover_local_image"
    if normalized == "openrouter":
        return "openrouter_image"
    if normalized == "apimart":
        return "apimart_image"
    if normalized == "gemini_aistudio":
        return "gemini_aistudio_image"
    if normalized == "gemini_vertex_adc":
        return "gemini_vertex_adc_image"
    return "video_cover_local_image"


def _step_debug_request(state: dict, step: str) -> dict:
    request_payload = ((state.get("step_requests") or {}).get(step)) or {}
    return request_payload if isinstance(request_payload, dict) else {}


def _step_debug_result(state: dict, step: str) -> dict:
    result_payload = ((state.get("step_results") or {}).get(step)) or {}
    return result_payload if isinstance(result_payload, dict) else {}


def _prompt_index_from_request(default: int = 1) -> int:
    raw = request.args.get("prompt_index")
    try:
        value = int(raw or default)
    except (TypeError, ValueError):
        value = default
    return max(1, value)


def _cover_prompt_row(request_payload: dict, prompt_index: int) -> dict:
    prompts = request_payload.get("image_prompts")
    if not isinstance(prompts, list):
        prompts = []
    for item in prompts:
        if isinstance(item, dict) and int(item.get("index") or 0) == prompt_index:
            return item
    first = prompts[0] if prompts and isinstance(prompts[0], dict) else {}
    return first if first else {"index": prompt_index, "prompt": str(request_payload.get("prompt") or "")}


def _cover_reference_object_key(state: dict) -> str:
    result = state.get("result") if isinstance(state.get("result"), dict) else {}
    reference = result.get("reference") if isinstance(result.get("reference"), dict) else {}
    return str(reference.get("object_key") or "").strip()


def _build_cover_full_request(state: dict, request_payload: dict, prompt_index: int) -> tuple[dict, dict]:
    provider = str(request_payload.get("provider") or "local")
    model = str(request_payload.get("model") or request_payload.get("model_id") or "")
    cfg = get_provider_config(_cover_provider_config_code(provider))
    api_key = str(getattr(cfg, "api_key", "") or "")
    base_url = str(getattr(cfg, "base_url", "") or "").rstrip("/")
    if provider == "local":
        base_url = base_url or "http://172.30.254.14:82/v1"
        url = f"{base_url}/images/edits"
        content_type = "multipart/form-data"
    elif provider == "openrouter":
        base_url = base_url or "https://openrouter.ai/api/v1"
        url = f"{base_url}/chat/completions"
        content_type = "application/json"
    elif provider == "apimart":
        base_url = base_url or "https://api.apimart.ai"
        url = f"{base_url}/v1/images/generations"
        content_type = "application/json"
    else:
        url = base_url or provider
        content_type = "application/json"

    prompt_row = _cover_prompt_row(request_payload, prompt_index)
    prompt = str(prompt_row.get("prompt") or request_payload.get("prompt") or "")
    object_key = _cover_reference_object_key(state)
    body = {
        "model": model or "gpt-image-2",
        "prompt": prompt,
        "n": "1",
        "size": "1024x1536",
    }
    files = [{
        "field": "image",
        "filename": "reference.png",
        "content_type": "image/png",
        "source": object_key,
    }]
    full_request = {
        "method": "POST",
        "url": url,
        "headers": {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": content_type,
        },
        "api_key": api_key,
        "body": body,
        "files": files,
        "image_prompts": request_payload.get("image_prompts") if isinstance(request_payload.get("image_prompts"), list) else [],
    }
    replay = {
        "supported": True,
        "default_url": url,
        "default_api_key": api_key,
        "prompt_index": int(prompt_row.get("index") or prompt_index),
        "prompt_indexes": [
            int(item.get("index") or idx + 1)
            for idx, item in enumerate(full_request["image_prompts"])
            if isinstance(item, dict)
        ] or [prompt_index],
    }
    return full_request, replay


def _build_text_full_request(request_payload: dict) -> dict:
    body = {}
    if request_payload.get("messages") is not None:
        body["messages"] = request_payload.get("messages")
    if request_payload.get("prompt") is not None:
        body["prompt"] = request_payload.get("prompt")
    if request_payload.get("request_data") is not None:
        body["request_data"] = request_payload.get("request_data")
    return {
        "method": "SDK",
        "url": "",
        "headers": {},
        "api_key": "",
        "body": body,
        "files": [],
    }


def _build_step_debug_payload(task_id: str, state: dict, step: str, prompt_index: int = 1) -> dict:
    if step not in STEP_ORDER:
        raise VideoCoverGenerationError(f"未知步骤：{step}")
    view_state = _state_with_urls(task_id, state)
    request_payload = _step_debug_request(view_state, step)
    result_payload = _step_debug_result(view_state, step)
    if step == "cover_generation":
        full_request, replay = _build_cover_full_request(view_state, request_payload, prompt_index)
        response_data = {
            "reference": (view_state.get("result") or {}).get("reference") or {},
            "covers": (view_state.get("result") or {}).get("covers") or [],
            "model": ((view_state.get("models") or {}).get("cover_generation")) or {},
            "timing": ((view_state.get("step_timing") or {}).get(step)) or {},
        }
    else:
        full_request = _build_text_full_request(request_payload)
        replay = {"supported": False, "reason": "该步骤暂不支持调试生成"}
        response_data = result_payload.get("structured_result") or {}
    return {
        "step": step,
        "label": STEP_LABELS.get(step, step),
        "status": ((view_state.get("steps") or {}).get(step)) or "pending",
        "request_data": {
            "request": request_payload,
            "product": view_state.get("product") or {},
            "image_count": view_state.get("image_count") or DEFAULT_IMAGE_COUNT,
            "image_prompts": request_payload.get("image_prompts") if isinstance(request_payload.get("image_prompts"), list) else [],
        },
        "full_request": full_request,
        "response_data": response_data,
        "raw_response": result_payload.get("raw_response", result_payload),
        "replay": replay,
    }
```

- [ ] **Step 3: Add the GET route before `api_run_project_step`**

Add:

```python
@bp.route("/video-cover/api/<task_id>/debug-payload/<step>", methods=["GET"])
@login_required
@admin_required
def api_debug_payload(task_id: str, step: str):
    row, state = _load_user_project(task_id)
    if not row:
        return _json_response({"ok": False, "error": "not found"}, 404)
    try:
        payload = _build_step_debug_payload(task_id, state, step, _prompt_index_from_request())
    except VideoCoverGenerationError as exc:
        return _json_response({"ok": False, "error": str(exc)}, 400)
    return _json_response({"ok": True, "data": _json_safe(payload)})
```

- [ ] **Step 4: Run route tests**

Run:

```bash
pytest tests/test_video_cover_generation.py::test_video_cover_debug_payload_returns_cover_full_request tests/test_video_cover_generation.py::test_video_cover_debug_payload_returns_text_step_without_replay -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add web/routes/video_cover.py tests/test_video_cover_generation.py
git commit -m "feat: expose video cover debug payload" -m "Docs-anchor: docs/superpowers/specs/2026-05-18-video-cover-prompt-debug-modal-design.md#完整报文"
```

## Task 4: Debug Replay Route Tests

**Files:**
- Modify: `tests/test_video_cover_generation.py`

- [ ] **Step 1: Add a failing replay test**

Add:

```python
def test_video_cover_debug_replay_posts_same_cover_payload_without_saving_state(authed_client_no_db, monkeypatch, tmp_path):
    from web.routes import video_cover

    reference_path = tmp_path / "reference.png"
    reference_path.write_bytes(_png_bytes())
    state = {
        "id": "task-1",
        "type": "video_cover",
        "step_requests": {
            "cover_generation": {
                "provider": "local",
                "model": "gpt-image-2",
                "image_prompts": [{"index": 1, "prompt": "actual prompt with native hook text"}],
            }
        },
        "result": {"reference": {"object_key": "artifacts/video_cover/8/task-1/reference.png"}},
    }
    row = {"id": "task-1", "state_json": json.dumps(state, ensure_ascii=False), "display_name": "Screen"}
    posted = {}
    saved = []

    class FakeResponse:
        status_code = 200
        text = json.dumps({"data": [{"b64_json": base64.b64encode(_png_bytes()).decode("ascii")}]})

        def json(self):
            return json.loads(self.text)

    def fake_post(url, *, headers, data, files, timeout):
        posted["url"] = url
        posted["headers"] = headers
        posted["data"] = data
        posted["files"] = files
        posted["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(video_cover.video_cover_project_store, "get_project", lambda task_id, *, user_id, is_admin: row)
    monkeypatch.setattr(video_cover.local_media_storage, "safe_local_path_for", lambda object_key: reference_path)
    monkeypatch.setattr(video_cover.local_media_storage, "download_to", lambda object_key, path: None)
    monkeypatch.setattr(video_cover.requests, "post", fake_post)
    monkeypatch.setattr(video_cover, "save_project_state", lambda *args, **kwargs: saved.append(args))

    resp = authed_client_no_db.post(
        "/video-cover/api/task-1/debug-replay/cover_generation",
        json={"request_url": "https://debug.example/v1/images/edits", "api_key": "sk-debug", "prompt_index": 1},
    )

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["ok"] is True
    assert payload["image"]["data_url"].startswith("data:image/png;base64,")
    assert payload["raw_response"]["data"][0]["b64_json"]
    assert posted["url"] == "https://debug.example/v1/images/edits"
    assert posted["headers"]["Authorization"] == "Bearer sk-debug"
    assert posted["data"]["model"] == "gpt-image-2"
    assert posted["data"]["prompt"] == "actual prompt with native hook text"
    assert posted["files"]["image"][0] == "reference.png"
    assert saved == []
```

- [ ] **Step 2: Add a failing non-cover replay test**

Add:

```python
def test_video_cover_debug_replay_rejects_non_cover_step(authed_client_no_db, monkeypatch):
    from web.routes import video_cover

    state = {"id": "task-1", "type": "video_cover", "step_requests": {"ad_copy": {"messages": []}}}
    row = {"id": "task-1", "state_json": json.dumps(state, ensure_ascii=False), "display_name": "Screen"}
    monkeypatch.setattr(video_cover.video_cover_project_store, "get_project", lambda task_id, *, user_id, is_admin: row)

    resp = authed_client_no_db.post(
        "/video-cover/api/task-1/debug-replay/ad_copy",
        json={"request_url": "https://debug.example/v1/images/edits", "api_key": "sk-debug"},
    )

    assert resp.status_code == 400
    assert "暂不支持调试生成" in resp.get_json()["error"]
```

- [ ] **Step 3: Run the failing replay tests**

Run:

```bash
pytest tests/test_video_cover_generation.py::test_video_cover_debug_replay_posts_same_cover_payload_without_saving_state tests/test_video_cover_generation.py::test_video_cover_debug_replay_rejects_non_cover_step -q
```

Expected: FAIL because the debug-replay route does not exist.

## Task 5: Implement Debug Replay

**Files:**
- Modify: `web/routes/video_cover.py`

- [ ] **Step 1: Add replay helpers before `api_debug_payload`**

Add:

```python
def _request_json_payload() -> dict:
    payload = request.get_json(silent=True) if request.is_json else None
    return payload if isinstance(payload, dict) else {}


def _cover_reference_image_bytes(state: dict) -> tuple[bytes, str, str]:
    object_key = _cover_reference_object_key(state)
    if not object_key:
        raise VideoCoverGenerationError("缺少封面生成参考图，无法调试生成")
    path = local_media_storage.safe_local_path_for(object_key)
    if not path.is_file():
        local_media_storage.download_to(object_key, path)
    if not path.is_file():
        raise VideoCoverGenerationError("封面生成参考图文件不存在，无法调试生成")
    return path.read_bytes(), "image/png", object_key


def _post_debug_cover_request(
    *,
    request_url: str,
    api_key: str,
    model: str,
    prompt: str,
    image_bytes: bytes,
    image_mime: str,
) -> tuple[bytes, str, dict]:
    if not request_url:
        raise VideoCoverGenerationError("请填写请求 URL")
    if not api_key:
        raise VideoCoverGenerationError("请填写 API key")
    response = requests.post(
        request_url,
        headers={"Authorization": f"Bearer {api_key}"},
        data={"model": model or "gpt-image-2", "prompt": prompt, "n": "1", "size": "1024x1536"},
        files={"image": ("reference.png", image_bytes, image_mime or "image/png")},
        timeout=360,
    )
    try:
        raw_response = response.json()
    except Exception:
        raw_response = {"text": str(getattr(response, "text", "") or "")}
    if getattr(response, "status_code", 0) >= 400:
        message = str(raw_response.get("error") or raw_response.get("message") or getattr(response, "text", "") or "")
        raise VideoCoverGenerationError(f"调试生成失败（HTTP {response.status_code}）：{message}")
    image, mime = _decode_image_response_payload(raw_response)
    return image, mime, raw_response
```

- [ ] **Step 2: Add the POST route after `api_debug_payload`**

Add:

```python
@bp.route("/video-cover/api/<task_id>/debug-replay/<step>", methods=["POST"])
@login_required
@admin_required
def api_debug_replay(task_id: str, step: str):
    row, state = _load_user_project(task_id)
    if not row:
        return _json_response({"ok": False, "error": "not found"}, 404)
    if step != "cover_generation":
        return _json_response({"ok": False, "error": "该步骤暂不支持调试生成"}, 400)
    payload = _request_json_payload()
    prompt_index = normalize_image_count(payload.get("prompt_index"), default=1)
    request_payload = _step_debug_request(state, step)
    prompt_row = _cover_prompt_row(request_payload, prompt_index)
    prompt = str(prompt_row.get("prompt") or request_payload.get("prompt") or "")
    model = str(request_payload.get("model") or request_payload.get("model_id") or "gpt-image-2")
    try:
        image_bytes, image_mime, _object_key = _cover_reference_image_bytes(state)
        generated, mime, raw_response = _post_debug_cover_request(
            request_url=str(payload.get("request_url") or "").strip(),
            api_key=str(payload.get("api_key") or "").strip(),
            model=model,
            prompt=prompt,
            image_bytes=image_bytes,
            image_mime=image_mime,
        )
    except VideoCoverGenerationError as exc:
        return _json_response({"ok": False, "error": str(exc)}, 400)
    data_url = f"data:{mime or 'image/png'};base64,{base64.b64encode(generated).decode('ascii')}"
    return _json_response({
        "ok": True,
        "image": {"data_url": data_url, "mime": mime or "image/png"},
        "raw_response": raw_response,
        "request_url": str(payload.get("request_url") or "").strip(),
        "prompt_index": prompt_index,
    })
```

- [ ] **Step 3: Run replay tests**

Run:

```bash
pytest tests/test_video_cover_generation.py::test_video_cover_debug_replay_posts_same_cover_payload_without_saving_state tests/test_video_cover_generation.py::test_video_cover_debug_replay_rejects_non_cover_step -q
```

Expected: PASS.

- [ ] **Step 4: Commit**

Run:

```bash
git add web/routes/video_cover.py tests/test_video_cover_generation.py
git commit -m "feat: add video cover debug replay" -m "Docs-anchor: docs/superpowers/specs/2026-05-18-video-cover-prompt-debug-modal-design.md#调试窗口"
```

## Task 6: Implement Prompt Modal UI

**Files:**
- Modify: `web/templates/video_cover_detail.html`
- Modify: `tests/test_video_cover_generation.py`

- [ ] **Step 1: Replace modal CSS**

In `web/templates/video_cover_detail.html`, replace the existing `.vcd-modal` and prompt-body styles with:

```css
.vcd-modal { width:min(920px, 100%); max-height:88vh; overflow:auto; border:1px solid var(--border-main); border-radius:8px; background:var(--bg-card); box-shadow:0 18px 48px rgba(15,23,42,.24); }
.vcd-prompt-debug-modal { width:min(80vw, 1600px); max-height:90vh; display:grid; grid-template-rows:auto minmax(0, 1fr); overflow:hidden; }
.vcd-modal-head { display:flex; align-items:center; justify-content:space-between; gap:12px; padding:14px 16px; border-bottom:1px solid var(--border-main); }
.vcd-modal-title { font-size:15px; font-weight:900; color:var(--text-main); }
.vcd-modal-body { padding:16px; }
.vcd-prompt-debug-body { min-height:0; overflow:auto; display:grid; gap:12px; padding:14px 16px 16px; }
.vcd-tab-row { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
.vcd-tab-btn { height:30px; padding:0 14px; border:1px solid var(--border-main); border-radius:999px; background:var(--bg-card); color:var(--text-main); font-size:12px; font-weight:900; cursor:pointer; }
.vcd-tab-btn.active { border-color:var(--primary-color); background:var(--primary-color); color:#f8fafc; }
.vcd-debug-panel { display:none; min-width:0; }
.vcd-debug-panel.active { display:grid; gap:12px; }
.vcd-debug-grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(220px, 1fr)); gap:10px; }
.vcd-debug-card { min-width:0; border:1px solid var(--border-main); border-radius:8px; background:#f8fafc; padding:10px; }
.vcd-debug-label { margin-bottom:6px; font-size:11px; font-weight:900; color:#64748b; }
.vcd-debug-value { font-size:12px; line-height:1.5; color:var(--text-main); white-space:pre-wrap; overflow-wrap:anywhere; }
.vcd-debug-form { display:grid; grid-template-columns:minmax(240px, 1fr) minmax(180px, 280px) auto; gap:10px; align-items:end; border:1px solid var(--border-main); border-radius:8px; background:#f8fafc; padding:12px; }
.vcd-debug-field { display:grid; gap:5px; min-width:0; }
.vcd-debug-field label { font-size:11px; font-weight:900; color:#64748b; }
.vcd-debug-field input { height:32px; border:1px solid var(--border-main); border-radius:8px; padding:0 10px; font-size:12px; color:var(--text-main); background:var(--bg-card); }
.vcd-debug-result { display:grid; gap:10px; }
.vcd-debug-result img { width:min(260px, 100%); aspect-ratio:9/16; object-fit:cover; border:1px solid var(--border-main); border-radius:8px; background:#e5e7eb; }
.vcd-pre { white-space:pre-wrap; overflow-wrap:anywhere; font-size:12px; line-height:1.55; padding:12px; border:1px solid var(--border-main); border-radius:8px; background:#0f172a; color:#e5e7eb; }
```

Keep the existing media queries and add:

```css
@media (max-width:720px) {
  .vcd-prompt-debug-modal { width:min(96vw, 100%); }
  .vcd-debug-form { grid-template-columns:1fr; }
}
```

- [ ] **Step 2: Replace the prompt modal HTML**

Replace the `#vcdPromptModal` block with:

```html
<div class="vcd-modal-backdrop" id="vcdPromptModal">
  <div class="vcd-modal vcd-prompt-debug-modal">
    <div class="vcd-modal-head">
      <div class="vcd-modal-title" id="vcdPromptTitle">提示词</div>
      <button class="btn btn-ghost btn-sm" type="button" id="vcdPromptClose">关闭</button>
    </div>
    <div class="vcd-prompt-debug-body">
      <div class="vcd-tab-row" role="tablist" aria-label="提示词调试">
        <button class="vcd-tab-btn active" type="button" data-prompt-root-tab="request">请求</button>
        <button class="vcd-tab-btn" type="button" data-prompt-root-tab="result">结果</button>
      </div>

      <section class="vcd-debug-panel active" data-prompt-root-panel="request">
        <div class="vcd-tab-row" role="tablist" aria-label="请求明细">
          <button class="vcd-tab-btn active" type="button" data-prompt-subtab="request-data">请求数据</button>
          <button class="vcd-tab-btn" type="button" data-prompt-subtab="full-request">完整报文</button>
        </div>
        <div class="vcd-debug-panel active" data-prompt-subpanel="request-data" id="vcdPromptRequestData"></div>
        <div class="vcd-debug-panel" data-prompt-subpanel="full-request">
          <pre class="vcd-pre" id="vcdPromptFullRequest"></pre>
          <div class="vcd-debug-form" id="vcdDebugReplayForm">
            <div class="vcd-debug-field">
              <label for="vcdDebugRequestUrl">请求 URL</label>
              <input id="vcdDebugRequestUrl" type="text" autocomplete="off">
            </div>
            <div class="vcd-debug-field">
              <label for="vcdDebugApiKey">API key</label>
              <input id="vcdDebugApiKey" type="text" autocomplete="off">
            </div>
            <button class="btn btn-primary" type="button" id="vcdDebugReplayBtn">调试生成</button>
          </div>
          <div class="vcd-debug-result" id="vcdDebugReplayResult"></div>
        </div>
      </section>

      <section class="vcd-debug-panel" data-prompt-root-panel="result">
        <div class="vcd-tab-row" role="tablist" aria-label="结果明细">
          <button class="vcd-tab-btn active" type="button" data-prompt-subtab="response-data">返回数据</button>
          <button class="vcd-tab-btn" type="button" data-prompt-subtab="raw-response">返回结果报文</button>
        </div>
        <div class="vcd-debug-panel active" data-prompt-subpanel="response-data" id="vcdPromptResponseData"></div>
        <div class="vcd-debug-panel" data-prompt-subpanel="raw-response">
          <pre class="vcd-pre" id="vcdPromptRawResponse"></pre>
        </div>
      </section>
    </div>
  </div>
</div>
```

- [ ] **Step 3: Replace prompt modal JavaScript state and renderers**

Replace the current `modalBody` constant and `openPrompt(step)` implementation with these pieces:

```javascript
  const promptRequestData = document.getElementById('vcdPromptRequestData');
  const promptFullRequest = document.getElementById('vcdPromptFullRequest');
  const promptResponseData = document.getElementById('vcdPromptResponseData');
  const promptRawResponse = document.getElementById('vcdPromptRawResponse');
  const debugRequestUrl = document.getElementById('vcdDebugRequestUrl');
  const debugApiKey = document.getElementById('vcdDebugApiKey');
  const debugReplayBtn = document.getElementById('vcdDebugReplayBtn');
  const debugReplayResult = document.getElementById('vcdDebugReplayResult');
  let currentPromptStep = '';
  let currentDebugPayload = null;

  function debugCard(label, value) {
    const text = typeof value === 'string' ? value : compactJson(value);
    if (!text) return '';
    return `<div class="vcd-debug-card"><div class="vcd-debug-label">${escapeHtml(label)}</div><div class="vcd-debug-value">${escapeHtml(text)}</div></div>`;
  }

  function renderDebugGrid(items) {
    const html = items.map(([label, value]) => debugCard(label, value)).filter(Boolean).join('');
    return html ? `<div class="vcd-debug-grid">${html}</div>` : '<div class="vcd-empty">暂无数据。</div>';
  }

  function renderRequestData(data) {
    const request = (data.request_data && data.request_data.request) || {};
    const product = (data.request_data && data.request_data.product) || {};
    const imagePrompts = (data.request_data && data.request_data.image_prompts) || [];
    return renderDebugGrid([
      ['步骤', data.label || data.step],
      ['状态', data.status || 'pending'],
      ['供应商', request.provider || ''],
      ['模型', request.model || request.model_id || ''],
      ['执行方式', request.execution_mode || (request.request_data || {}).execution_mode || ''],
      ['商品标题', product.title || ''],
      ['商品链接', product.product_url || currentState.product_url || ''],
      ['请求输入', request.request_data || {}],
      ['Prompt', request.prompt || ''],
      ['Messages', request.messages || ''],
      ['图片 Prompt', imagePrompts]
    ]);
  }

  function renderResponseData(data) {
    return renderDebugGrid([
      ['步骤', data.label || data.step],
      ['返回数据', data.response_data || {}],
      ['完整结构化结果', ((data.raw_response || {}).structured_result) || {}],
      ['模型', (((currentState.models || {})[data.step]) || {})],
      ['耗时', (((currentState.step_timing || {})[data.step]) || {})]
    ]);
  }

  function setPromptRootTab(name) {
    document.querySelectorAll('[data-prompt-root-tab]').forEach(btn => btn.classList.toggle('active', btn.dataset.promptRootTab === name));
    document.querySelectorAll('[data-prompt-root-panel]').forEach(panel => panel.classList.toggle('active', panel.dataset.promptRootPanel === name));
  }

  function setPromptSubTab(name) {
    document.querySelectorAll('[data-prompt-subtab]').forEach(btn => btn.classList.toggle('active', btn.dataset.promptSubtab === name));
    document.querySelectorAll('[data-prompt-subpanel]').forEach(panel => panel.classList.toggle('active', panel.dataset.promptSubpanel === name));
  }

  function fillPromptModal(data) {
    currentDebugPayload = data;
    promptRequestData.innerHTML = renderRequestData(data);
    promptFullRequest.textContent = compactJson(data.full_request || {});
    promptResponseData.innerHTML = renderResponseData(data);
    promptRawResponse.textContent = compactJson({raw_response: data.raw_response || {}, response_data: data.response_data || {}});
    debugRequestUrl.value = ((data.replay || {}).default_url) || ((data.full_request || {}).url) || '';
    debugApiKey.value = ((data.replay || {}).default_api_key) || ((data.full_request || {}).api_key) || '';
    debugReplayBtn.disabled = !((data.replay || {}).supported);
    debugReplayResult.innerHTML = (data.replay || {}).supported ? '' : '<div class="vcd-empty">该步骤暂不支持调试生成。</div>';
  }

  async function loadDebugPayload(step) {
    const response = await fetch(`/video-cover/api/${encodeURIComponent(TASK_ID)}/debug-payload/${encodeURIComponent(step)}`, {cache: 'no-store'});
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || !payload.ok) throw new Error(payload.error || '调试报文加载失败');
    return payload.data || {};
  }

  async function openPrompt(step) {
    currentPromptStep = step;
    modalTitle.textContent = `${STEP_LABELS[step] || step}提示词`;
    setPromptRootTab('request');
    setPromptSubTab('request-data');
    promptRequestData.innerHTML = '<div class="vcd-empty">正在加载请求数据</div>';
    promptFullRequest.textContent = '';
    promptResponseData.innerHTML = '';
    promptRawResponse.textContent = '';
    debugReplayResult.innerHTML = '';
    modal.classList.add('show');
    try {
      fillPromptModal(await loadDebugPayload(step));
    } catch (e) {
      promptRequestData.innerHTML = `<div class="vcd-empty">调试报文加载失败：${escapeHtml(e.message || e)}</div>`;
    }
  }

  async function runDebugReplay() {
    if (!currentPromptStep) return;
    debugReplayBtn.disabled = true;
      debugReplayResult.innerHTML = '<div class="vcd-empty">正在调试生成</div>';
    try {
      const response = await fetch(`/video-cover/api/${encodeURIComponent(TASK_ID)}/debug-replay/${encodeURIComponent(currentPromptStep)}`, {
        method: 'POST',
        headers: {'X-CSRFToken': csrfToken(), 'Content-Type': 'application/json'},
        body: JSON.stringify({
          request_url: debugRequestUrl.value,
          api_key: debugApiKey.value,
          prompt_index: ((currentDebugPayload || {}).replay || {}).prompt_index || 1
        })
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || !payload.ok) throw new Error(payload.error || '调试生成失败');
      debugReplayResult.innerHTML = `<img src="${escapeHtml((payload.image || {}).data_url || '')}" alt="调试生成结果"><pre class="vcd-pre">${escapeHtml(compactJson(payload.raw_response || {}))}</pre>`;
    } catch (e) {
      debugReplayResult.innerHTML = `<div class="vcd-empty">调试生成失败：${escapeHtml(e.message || e)}</div>`;
    } finally {
      debugReplayBtn.disabled = false;
    }
  }
```

- [ ] **Step 4: Update event binding**

Inside `bindEventListeners()`, add:

```javascript
    document.querySelectorAll('[data-prompt-root-tab]').forEach(btn => btn.addEventListener('click', () => setPromptRootTab(btn.dataset.promptRootTab)));
    document.querySelectorAll('[data-prompt-subtab]').forEach(btn => btn.addEventListener('click', () => setPromptSubTab(btn.dataset.promptSubtab)));
    debugReplayBtn.addEventListener('click', runDebugReplay);
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape') {
        modal.classList.remove('show');
        allPayloadModal.classList.remove('show');
      }
    });
```

Remove the old references to `modalBody`.

- [ ] **Step 5: Run the template test**

Run:

```bash
pytest tests/test_video_cover_generation.py::test_video_cover_prompt_modal_has_request_result_tabs_and_debug_form -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```bash
git add web/templates/video_cover_detail.html tests/test_video_cover_generation.py
git commit -m "feat: add video cover prompt debug modal tabs" -m "Docs-anchor: docs/superpowers/specs/2026-05-18-video-cover-prompt-debug-modal-design.md#弹窗布局"
```

## Task 7: Focused Verification

**Files:**
- Modify only if verification exposes a bug in files from earlier tasks.

- [ ] **Step 1: Run focused pytest**

Run:

```bash
pytest tests/test_video_cover_generation.py -q
```

Expected: PASS.

- [ ] **Step 2: Run template syntax smoke**

Run:

```bash
python -m compileall web/routes/video_cover.py appcore/video_cover_generation.py -q
```

Expected: exit code 0.

- [ ] **Step 3: Run route smoke for auth behavior**

Run:

```bash
python -m web.app
```

Open a second shell and run:

```bash
curl -I http://127.0.0.1:5000/video-cover
```

Expected: HTTP 302 to login when unauthenticated. Stop the dev server after the check.

- [ ] **Step 4: Final status**

Run:

```bash
git status --short
```

Expected: only intended committed changes or a clean worktree. If any uncommitted change remains, inspect it before final response.

## Self-Review

- Spec coverage: Tasks 1 and 6 cover modal size and Tab names; Tasks 2 and 3 cover full request URL/header/API key/body/files and `返回数据`/`返回结果报文` data sources; Tasks 4 and 5 cover debug replay without saving state; Task 7 covers verification.
- Placeholder scan: this plan contains no placeholder markers or unspecified implementation steps.
- Type consistency: route payload names are consistent across tests, helpers, and frontend: `request_data`, `full_request`, `response_data`, `raw_response`, `replay`, `debug-payload`, `debug-replay`.
