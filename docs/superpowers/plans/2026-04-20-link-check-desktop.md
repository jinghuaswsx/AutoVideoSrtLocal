# Link Check Desktop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Windows desktop link-check tool that resolves a target URL to `media_products.id`, downloads language-specific reference images from the server, locks the target page in a visible Edge browser, downloads site images, and runs the existing comparison plus LLM judgment flow into a local `img/<product_id>-<timestamp>/` workspace.

**Architecture:** Add one focused OpenAPI bootstrap endpoint on the Flask server, then add a repo-local `link_check_desktop/` client that orchestrates bootstrap, local storage, browser capture, comparison, and result rendering. Keep the image judgment algorithm aligned with the current `link_check` module by reusing `appcore.link_check_compare` and refactoring link-check LLM entry points onto `appcore.llm_client`.

**Tech Stack:** Flask, Flask test client, `requests`, `Playwright`, `Tkinter`, `PyInstaller`, `pytest`, `appcore.llm_client`, Pillow, ImageHash, scikit-image

---

## File Map

### Server-side files

- Modify: `web/app.py`
  - Register the new `/openapi/link-check` blueprint and exempt it from CSRF like the other OpenAPI blueprints.
- Modify: `web/routes/openapi_materials.py`
  - Export a third blueprint for `/openapi/link-check`.
  - Add request validation, bootstrap response assembly, and download URL serialization.
- Modify: `appcore/medias.py`
  - Add URL normalization, handle extraction, product matching, and reference-image listing helpers.
- Modify: `appcore/llm_use_cases.py`
  - Register `link_check.same_image` for the same-image multimodal judgment path.
- Modify: `appcore/link_check_gemini.py`
  - Route image analysis through `llm_client.invoke_generate()`.
- Modify: `appcore/link_check_same_image.py`
  - Route same-image judgment through `llm_client.invoke_generate()`.

### Desktop client files

- Create: `link_check_desktop/__init__.py`
  - Package marker.
- Create: `link_check_desktop/storage.py`
  - Resolve executable-root workspace, create `img/<product_id>-<timestamp>/`, and write JSON files.
- Create: `link_check_desktop/bootstrap_api.py`
  - Call the bootstrap endpoint with the embedded API key.
- Create: `link_check_desktop/result_schema.py`
  - Normalize the task, page, and final-result payloads written to disk.
- Create: `link_check_desktop/analysis.py`
  - Reuse `find_best_reference`, `run_binary_quick_check`, `analyze_image`, and `judge_same_image`.
- Create: `link_check_desktop/browser_worker.py`
  - Launch visible Edge with Playwright, lock locale, extract images, and download them.
- Create: `link_check_desktop/controller.py`
  - Orchestrate bootstrap -> download references -> browser capture -> analysis -> result persistence.
- Create: `link_check_desktop/gui.py`
  - Minimal Tkinter UI with URL input, status text, and result summary.
- Create: `link_check_desktop/main.py`
  - Desktop entrypoint.
- Create: `link_check_desktop/requirements.txt`
  - Reuse root requirements and add `playwright` and `pyinstaller`.
- Create: `link_check_desktop/README.md`
  - Run and build instructions.
- Create: `link_check_desktop/packaging/link_check_desktop.spec`
  - PyInstaller build recipe.

### Tests

- Create: `tests/test_appcore_medias_link_check_bootstrap.py`
  - Unit tests for URL matching and reference-image queries.
- Create: `tests/test_link_check_bootstrap_routes.py`
  - Route tests for `/openapi/link-check/bootstrap`.
- Modify: `tests/test_link_check_gemini.py`
  - Assert `llm_client.invoke_generate()` is used for image analysis.
- Modify: `tests/test_link_check_same_image.py`
  - Assert `llm_client.invoke_generate()` is used for same-image judgment.
- Create: `tests/test_link_check_desktop_storage.py`
  - Workspace naming and JSON persistence tests.
- Create: `tests/test_link_check_desktop_bootstrap_api.py`
  - API wrapper success/error tests.
- Create: `tests/test_link_check_desktop_controller.py`
  - Controller orchestration tests with mocked browser and analysis.

### Supporting docs

- Modify: `AGENTS.md`
  - Add exact run, test, and build commands for `link_check_desktop/`.

## Task 1: Bootstrap Route Registration And Validation

**Files:**
- Modify: `web/app.py`
- Modify: `web/routes/openapi_materials.py`
- Create: `tests/test_link_check_bootstrap_routes.py`

- [ ] **Step 1: Write the failing route-auth tests**

```python
from __future__ import annotations

import importlib

import pytest

from web.app import create_app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("OPENAPI_MEDIA_API_KEY", "demo-key")
    import config as _config
    importlib.reload(_config)
    app = create_app()
    return app.test_client()


def test_link_check_bootstrap_rejects_missing_api_key(client):
    response = client.post(
        "/openapi/link-check/bootstrap",
        json={"target_url": "https://newjoyloo.com/de/products/demo-rjc"},
    )
    assert response.status_code == 401
    assert response.get_json() == {"error": "invalid api key"}


def test_link_check_bootstrap_requires_target_url(client):
    response = client.post(
        "/openapi/link-check/bootstrap",
        headers={"X-API-Key": "demo-key"},
        json={},
    )
    assert response.status_code == 400
    assert response.get_json() == {"error": "invalid target_url"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_link_check_bootstrap_routes.py::test_link_check_bootstrap_rejects_missing_api_key tests/test_link_check_bootstrap_routes.py::test_link_check_bootstrap_requires_target_url -v`

Expected: FAIL with `404 != 401` or `404 != 400`, proving the route is not registered yet.

- [ ] **Step 3: Write minimal route registration and validation**

```python
# web/routes/openapi_materials.py
link_check_bp = Blueprint("openapi_link_check", __name__, url_prefix="/openapi/link-check")


@link_check_bp.route("/bootstrap", methods=["POST"])
def bootstrap_link_check():
    if not _api_key_valid():
        return jsonify({"error": "invalid api key"}), 401

    body = request.get_json(silent=True) or {}
    target_url = (body.get("target_url") or "").strip()
    if not target_url:
        return jsonify({"error": "invalid target_url"}), 400

    return jsonify({"error": "not implemented"}), 501
```

```python
# web/app.py
from web.routes.openapi_materials import link_check_bp as openapi_link_check_bp

app.register_blueprint(openapi_link_check_bp)
csrf.exempt(openapi_link_check_bp)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_link_check_bootstrap_routes.py::test_link_check_bootstrap_rejects_missing_api_key tests/test_link_check_bootstrap_routes.py::test_link_check_bootstrap_requires_target_url -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/app.py web/routes/openapi_materials.py tests/test_link_check_bootstrap_routes.py
git commit -m "feat(openapi): add link check bootstrap route skeleton"
```

## Task 2: URL Matching And Reference Image Query Helpers

**Files:**
- Modify: `appcore/medias.py`
- Create: `tests/test_appcore_medias_link_check_bootstrap.py`

- [ ] **Step 1: Write the failing helper tests**

```python
from appcore import medias


def test_find_product_for_link_check_url_prefers_exact_localized_link(monkeypatch):
    rows = [{
        "id": 88,
        "product_code": "sonic-lens-refresher",
        "name": "Sonic Lens Refresher",
        "localized_links_json": '{"de":"https://newjoyloo.com/de/products/sonic-lens-refresher-rjc?variant=123"}',
    }]
    monkeypatch.setattr(medias, "query", lambda sql, args=(): rows)

    product = medias.find_product_for_link_check_url(
        "https://newjoyloo.com/de/products/sonic-lens-refresher-rjc?variant=123",
        "de",
    )

    assert product["id"] == 88
    assert product["_matched_by"] == "localized_links_exact"


def test_find_product_for_link_check_url_falls_back_to_product_code_without_rjc(monkeypatch):
    monkeypatch.setattr(
        medias,
        "query_one",
        lambda sql, args=(): {"id": 99, "product_code": "sonic-lens-refresher", "name": "Demo"},
    )

    product = medias.find_product_for_link_check_url(
        "https://newjoyloo.com/de/products/sonic-lens-refresher-rjc",
        "de",
    )

    assert product["id"] == 99
    assert product["_matched_by"] == "product_code"


def test_list_reference_images_for_lang_returns_cover_then_details(monkeypatch):
    calls = []

    def fake_query(sql, args=()):
        calls.append(" ".join(sql.split()))
        if "FROM media_product_covers" in sql:
            return [{"lang": "de", "object_key": "covers/de.jpg"}]
        if "FROM media_product_detail_images" in sql:
            return [
                {"id": 10, "object_key": "details/de-01.jpg"},
                {"id": 11, "object_key": "details/de-02.jpg"},
            ]
        return []

    monkeypatch.setattr(medias, "query", fake_query)

    items = medias.list_reference_images_for_lang(7, "de")

    assert [item["kind"] for item in items] == ["cover", "detail", "detail"]
    assert items[0]["object_key"] == "covers/de.jpg"
    assert items[1]["id"] == "detail-10"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_appcore_medias_link_check_bootstrap.py -v`

Expected: FAIL with `AttributeError` because `find_product_for_link_check_url` and `list_reference_images_for_lang` do not exist yet.

- [ ] **Step 3: Write minimal helper implementation**

```python
# appcore/medias.py
import json
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


def _normalize_link_check_url(url: str, *, keep_query: bool) -> str:
    parsed = urlparse((url or "").strip())
    query = urlencode(parse_qsl(parsed.query, keep_blank_values=True), doseq=True) if keep_query else ""
    path = parsed.path.rstrip("/") or "/"
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), path, "", query, ""))


def _extract_product_handle(url: str) -> str:
    parts = [part for part in urlparse(url).path.split("/") if part]
    if "products" not in parts:
        return ""
    idx = parts.index("products")
    if idx + 1 >= len(parts):
        return ""
    handle = parts[idx + 1].strip().lower()
    return handle[:-4] if handle.endswith("-rjc") else handle
```

```python
# appcore/medias.py (actual minimal body)
def find_product_for_link_check_url(target_url: str, target_language: str) -> dict | None:
    normalized = _normalize_link_check_url(target_url, keep_query=True)
    normalized_no_query = _normalize_link_check_url(target_url, keep_query=False)
    rows = query(
        "SELECT id, product_code, name, localized_links_json "
        "FROM media_products WHERE deleted_at IS NULL"
    )
    for row in rows or []:
        raw_links = row.get("localized_links_json")
        links = json.loads(raw_links) if isinstance(raw_links, str) and raw_links else (raw_links or {})
        lang_url = str((links or {}).get(target_language) or "").strip()
        if not lang_url:
            continue
        if _normalize_link_check_url(lang_url, keep_query=True) == normalized:
            return {**row, "_matched_by": "localized_links_exact"}
        if _normalize_link_check_url(lang_url, keep_query=False) == normalized_no_query:
            return {**row, "_matched_by": "localized_links_path"}

    handle = _extract_product_handle(target_url)
    if not handle:
        return None
    row = query_one(
        "SELECT id, product_code, name FROM media_products "
        "WHERE product_code=%s AND deleted_at IS NULL",
        (handle,),
    )
    return {**row, "_matched_by": "product_code"} if row else None


def list_reference_images_for_lang(product_id: int, lang: str) -> list[dict]:
    items: list[dict] = []
    cover_rows = query(
        "SELECT lang, object_key FROM media_product_covers "
        "WHERE product_id=%s AND lang=%s",
        (product_id, lang),
    )
    for idx, row in enumerate(cover_rows or []):
        if row.get("object_key"):
            items.append({
                "id": f"cover-{lang}-{idx}",
                "kind": "cover",
                "filename": row["object_key"].split("/")[-1],
                "object_key": row["object_key"],
            })

    detail_rows = query(
        "SELECT id, object_key FROM media_product_detail_images "
        "WHERE product_id=%s AND lang=%s AND deleted_at IS NULL "
        "ORDER BY sort_order ASC, id ASC",
        (product_id, lang),
    )
    for row in detail_rows or []:
        if row.get("object_key"):
            items.append({
                "id": f"detail-{row['id']}",
                "kind": "detail",
                "filename": row["object_key"].split("/")[-1],
                "object_key": row["object_key"],
            })
    return items
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_appcore_medias_link_check_bootstrap.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add appcore/medias.py tests/test_appcore_medias_link_check_bootstrap.py
git commit -m "feat(medias): add link check bootstrap lookup helpers"
```

## Task 3: Bootstrap Success Payload And Conflict Cases

**Files:**
- Modify: `web/routes/openapi_materials.py`
- Modify: `tests/test_link_check_bootstrap_routes.py`

- [ ] **Step 1: Write the failing success and conflict tests**

```python
def test_link_check_bootstrap_returns_product_and_reference_images(client, monkeypatch):
    monkeypatch.setattr(
        "web.routes.openapi_materials.detect_target_language_from_url",
        lambda url, enabled: "de",
    )
    monkeypatch.setattr(
        "web.routes.openapi_materials.medias.list_languages",
        lambda: [{"code": "de", "name_zh": "德语", "enabled": 1}],
    )
    monkeypatch.setattr(
        "web.routes.openapi_materials.medias.find_product_for_link_check_url",
        lambda url, lang: {"id": 123, "product_code": "sonic-lens-refresher", "name": "Sonic Lens Refresher", "_matched_by": "product_code"},
    )
    monkeypatch.setattr(
        "web.routes.openapi_materials.medias.list_reference_images_for_lang",
        lambda product_id, lang: [
            {"id": "cover-de-0", "kind": "cover", "filename": "cover_de.jpg", "object_key": "1/medias/123/cover_de.jpg"}
        ],
    )
    monkeypatch.setattr(
        "web.routes.openapi_materials.tos_clients.generate_signed_media_download_url",
        lambda key: f"https://signed.example.com/{key}",
    )

    response = client.post(
        "/openapi/link-check/bootstrap",
        headers={"X-API-Key": "demo-key"},
        json={"target_url": "https://newjoyloo.com/de/products/sonic-lens-refresher-rjc?variant=123"},
    )

    body = response.get_json()
    assert response.status_code == 200
    assert body["product"]["id"] == 123
    assert body["target_language"] == "de"
    assert body["target_language_name"] == "德语"
    assert body["matched_by"] == "product_code"
    assert body["reference_images"][0]["download_url"] == "https://signed.example.com/1/medias/123/cover_de.jpg"


def test_link_check_bootstrap_returns_409_when_references_missing(client, monkeypatch):
    monkeypatch.setattr(
        "web.routes.openapi_materials.detect_target_language_from_url",
        lambda url, enabled: "de",
    )
    monkeypatch.setattr(
        "web.routes.openapi_materials.medias.list_languages",
        lambda: [{"code": "de", "name_zh": "德语", "enabled": 1}],
    )
    monkeypatch.setattr(
        "web.routes.openapi_materials.medias.find_product_for_link_check_url",
        lambda url, lang: {"id": 123, "product_code": "demo", "name": "Demo", "_matched_by": "product_code"},
    )
    monkeypatch.setattr(
        "web.routes.openapi_materials.medias.list_reference_images_for_lang",
        lambda product_id, lang: [],
    )

    response = client.post(
        "/openapi/link-check/bootstrap",
        headers={"X-API-Key": "demo-key"},
        json={"target_url": "https://newjoyloo.com/de/products/demo-rjc"},
    )

    assert response.status_code == 409
    assert response.get_json() == {"error": "references not ready"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_link_check_bootstrap_routes.py -v`

Expected: FAIL with `501 != 200` and `501 != 409`, proving the skeleton route still needs real logic.

- [ ] **Step 3: Write minimal bootstrap implementation**

```python
# web/routes/openapi_materials.py
from appcore.link_check_locale import detect_target_language_from_url


def _enabled_language_map() -> dict[str, dict]:
    mapping = {}
    for row in medias.list_languages() or []:
        code = (row.get("code") or "").strip().lower()
        if code:
            mapping[code] = row
    return mapping


def _serialize_reference_image(item: dict) -> dict:
    return {
        "id": item["id"],
        "kind": item["kind"],
        "filename": item["filename"],
        "download_url": tos_clients.generate_signed_media_download_url(item["object_key"]),
        "expires_in": config.TOS_SIGNED_URL_EXPIRES,
    }


@link_check_bp.route("/bootstrap", methods=["POST"])
def bootstrap_link_check():
    if not _api_key_valid():
        return jsonify({"error": "invalid api key"}), 401

    body = request.get_json(silent=True) or {}
    target_url = (body.get("target_url") or "").strip()
    if not target_url.startswith(("http://", "https://")):
        return jsonify({"error": "invalid target_url"}), 400

    language_map = _enabled_language_map()
    target_language = detect_target_language_from_url(target_url, set(language_map))
    if not target_language:
        return jsonify({"error": "language not detected"}), 409

    product = medias.find_product_for_link_check_url(target_url, target_language)
    if not product:
        return jsonify({"error": "product not found"}), 404

    reference_images = medias.list_reference_images_for_lang(product["id"], target_language)
    if not reference_images:
        return jsonify({"error": "references not ready"}), 409

    language = language_map.get(target_language) or {"name_zh": target_language}
    return jsonify({
        "product": {
            "id": product["id"],
            "product_code": product.get("product_code") or "",
            "name": product.get("name") or "",
        },
        "target_language": target_language,
        "target_language_name": language.get("name_zh") or target_language,
        "matched_by": product.get("_matched_by") or "",
        "normalized_url": target_url,
        "reference_images": [_serialize_reference_image(item) for item in reference_images],
    })
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_link_check_bootstrap_routes.py tests/test_appcore_medias_link_check_bootstrap.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/routes/openapi_materials.py tests/test_link_check_bootstrap_routes.py
git commit -m "feat(openapi): implement link check bootstrap payload"
```

## Task 4: Refactor Link-Check LLM Calls Onto `llm_client`

**Files:**
- Modify: `appcore/llm_use_cases.py`
- Modify: `appcore/link_check_gemini.py`
- Modify: `appcore/link_check_same_image.py`
- Modify: `tests/test_link_check_gemini.py`
- Modify: `tests/test_link_check_same_image.py`

- [ ] **Step 1: Write the failing LLM-client tests**

```python
def test_analyze_image_uses_llm_client_invoke_generate(monkeypatch, tmp_path):
    from appcore import link_check_gemini as module

    image_path = tmp_path / "sample.jpg"
    image_path.write_bytes(b"fake")
    captured = {}

    def fake_invoke_generate(use_case_code, **kwargs):
        captured["use_case_code"] = use_case_code
        captured["kwargs"] = kwargs
        return {
            "text": "",
            "raw": {
                "decision": "pass",
                "has_text": True,
                "detected_language": "de",
                "language_match": True,
                "text_summary": "Hallo",
                "quality_score": 95,
                "quality_reason": "ok",
                "needs_replacement": False,
            },
        }

    monkeypatch.setattr(module.llm_client, "invoke_generate", fake_invoke_generate)

    result = module.analyze_image(image_path, target_language="de", target_language_name="德语")

    assert result["decision"] == "pass"
    assert captured["use_case_code"] == "link_check.analyze"
    assert captured["kwargs"]["media"] == [image_path]


def test_same_image_judgment_uses_llm_client(monkeypatch, tmp_path):
    from appcore import link_check_same_image as module

    site = tmp_path / "site.jpg"
    ref = tmp_path / "ref.jpg"
    site.write_bytes(b"site")
    ref.write_bytes(b"ref")
    captured = {}

    def fake_invoke_generate(use_case_code, **kwargs):
        captured["use_case_code"] = use_case_code
        captured["kwargs"] = kwargs
        return {"text": "", "raw": {"answer": "是"}}

    monkeypatch.setattr(module.llm_client, "invoke_generate", fake_invoke_generate)

    result = module.judge_same_image(site, ref)

    assert result["status"] == "done"
    assert result["answer"] == "是"
    assert captured["use_case_code"] == "link_check.same_image"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_link_check_gemini.py tests/test_link_check_same_image.py -v`

Expected: FAIL because the current implementations still call `gemini.generate()` and provider-specific helpers directly.

- [ ] **Step 3: Write minimal `llm_client` implementation**

```python
# appcore/llm_use_cases.py
"link_check.same_image": _uc(
    "link_check.same_image", "image", "链接商品图同图判断",
    "判断站点图与参考图是否属于同一基础图片",
    "gemini_aistudio", "gemini-3.1-flash-lite-preview", "gemini",
),
```

```python
# appcore/link_check_gemini.py
from appcore import llm_client


def analyze_image(image_path: str | Path, *, target_language: str, target_language_name: str) -> dict:
    result = llm_client.invoke_generate(
        "link_check.analyze",
        prompt=_build_prompt(target_language=target_language, target_language_name=target_language_name),
        media=[Path(image_path)],
        response_schema=_RESPONSE_SCHEMA,
        temperature=0,
    )
    payload = result.get("raw") if isinstance(result, dict) else {}
    if not isinstance(payload, dict):
        payload = {}
```

```python
# appcore/link_check_same_image.py
from appcore import llm_client

_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string", "enum": ["是", "不是"]}},
    "required": ["answer"],
}


def judge_same_image(site_path: str | Path, reference_path: str | Path) -> dict:
    try:
        payload = llm_client.invoke_generate(
            "link_check.same_image",
            prompt=_build_prompt(),
            media=[Path(site_path), Path(reference_path)],
            response_schema=_RESPONSE_SCHEMA,
            temperature=0,
        )
        raw = payload.get("raw") if isinstance(payload, dict) else {}
        answer = _normalize_answer(str((raw or {}).get("answer") or ""))
        return {
            "status": "done",
            "answer": answer,
            "channel": "binding",
            "channel_label": "Binding",
            "model": "",
            "reason": "",
        }
    except Exception as exc:
        return {
            "status": "error",
            "answer": "",
            "channel": "binding",
            "channel_label": "Binding",
            "model": "",
            "reason": str(exc),
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_link_check_gemini.py tests/test_link_check_same_image.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add appcore/llm_use_cases.py appcore/link_check_gemini.py appcore/link_check_same_image.py tests/test_link_check_gemini.py tests/test_link_check_same_image.py
git commit -m "feat(link-check): route LLM judgments through llm_client"
```

## Task 5: Desktop Storage, Result Schema, And Bootstrap Client

**Files:**
- Create: `link_check_desktop/__init__.py`
- Create: `link_check_desktop/storage.py`
- Create: `link_check_desktop/result_schema.py`
- Create: `link_check_desktop/bootstrap_api.py`
- Create: `link_check_desktop/requirements.txt`
- Create: `tests/test_link_check_desktop_storage.py`
- Create: `tests/test_link_check_desktop_bootstrap_api.py`

- [ ] **Step 1: Write the failing storage and API wrapper tests**

```python
from datetime import datetime
from pathlib import Path

from link_check_desktop import bootstrap_api, storage


def test_create_workspace_uses_product_id_timestamp_name(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "executable_root", lambda: tmp_path)
    workspace = storage.create_workspace(12345, now=datetime(2026, 4, 20, 23, 5, 18))

    assert workspace.root == tmp_path / "img" / "12345-20260420230518"
    assert workspace.reference_dir.is_dir()
    assert workspace.site_dir.is_dir()
    assert workspace.compare_dir.is_dir()


def test_fetch_bootstrap_raises_for_conflict(monkeypatch):
    class DummyResponse:
        status_code = 409
        def json(self):
            return {"error": "references not ready"}

    monkeypatch.setattr(bootstrap_api.requests, "post", lambda *args, **kwargs: DummyResponse())

    try:
        bootstrap_api.fetch_bootstrap("http://127.0.0.1:5000", "demo-key", "https://newjoyloo.com/de/products/demo-rjc")
    except bootstrap_api.BootstrapError as exc:
        assert exc.status_code == 409
        assert exc.payload == {"error": "references not ready"}
    else:
        raise AssertionError("expected BootstrapError")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_link_check_desktop_storage.py tests/test_link_check_desktop_bootstrap_api.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'link_check_desktop'`.

- [ ] **Step 3: Write minimal storage and bootstrap modules**

```python
# link_check_desktop/storage.py
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class Workspace:
    root: Path
    reference_dir: Path
    site_dir: Path
    compare_dir: Path


def executable_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def create_workspace(product_id: int, *, now: datetime | None = None) -> Workspace:
    current = now or datetime.now()
    root = executable_root() / "img" / f"{product_id}-{current:%Y%m%d%H%M%S}"
    reference_dir = root / "reference"
    site_dir = root / "site"
    compare_dir = root / "compare"
    reference_dir.mkdir(parents=True, exist_ok=False)
    site_dir.mkdir(parents=True, exist_ok=True)
    compare_dir.mkdir(parents=True, exist_ok=True)
    return Workspace(root=root, reference_dir=reference_dir, site_dir=site_dir, compare_dir=compare_dir)


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
```

```python
# link_check_desktop/bootstrap_api.py
from __future__ import annotations

import requests


class BootstrapError(RuntimeError):
    def __init__(self, status_code: int, payload: dict):
        super().__init__(payload.get("error") or f"bootstrap failed: {status_code}")
        self.status_code = status_code
        self.payload = payload


def fetch_bootstrap(base_url: str, api_key: str, target_url: str, *, timeout: int = 20) -> dict:
    response = requests.post(
        f"{base_url.rstrip('/')}/openapi/link-check/bootstrap",
        headers={"X-API-Key": api_key},
        json={"target_url": target_url},
        timeout=timeout,
    )
    payload = response.json()
    if response.status_code >= 400:
        raise BootstrapError(response.status_code, payload)
    return payload
```

```text
# link_check_desktop/requirements.txt
-r ../requirements.txt
playwright>=1.54,<2.0
pyinstaller>=6.14,<7.0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_link_check_desktop_storage.py tests/test_link_check_desktop_bootstrap_api.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add link_check_desktop/__init__.py link_check_desktop/storage.py link_check_desktop/result_schema.py link_check_desktop/bootstrap_api.py link_check_desktop/requirements.txt tests/test_link_check_desktop_storage.py tests/test_link_check_desktop_bootstrap_api.py
git commit -m "feat(desktop): add bootstrap client and local workspace helpers"
```

## Task 6: Browser Capture And Controller Orchestration

**Files:**
- Create: `link_check_desktop/analysis.py`
- Create: `link_check_desktop/browser_worker.py`
- Create: `link_check_desktop/controller.py`
- Create: `tests/test_link_check_desktop_controller.py`

- [ ] **Step 1: Write the failing controller test**

```python
from pathlib import Path

from link_check_desktop import controller


def test_run_link_check_builds_workspace_and_result(monkeypatch, tmp_path):
    monkeypatch.setattr(controller.storage, "create_workspace", lambda product_id, now=None: type("WS", (), {
        "root": tmp_path / "img" / "123-20260420230518",
        "reference_dir": tmp_path / "img" / "123-20260420230518" / "reference",
        "site_dir": tmp_path / "img" / "123-20260420230518" / "site",
        "compare_dir": tmp_path / "img" / "123-20260420230518" / "compare",
    })())
    monkeypatch.setattr(controller.bootstrap_api, "fetch_bootstrap", lambda *args, **kwargs: {
        "product": {"id": 123, "product_code": "demo", "name": "Demo"},
        "target_language": "de",
        "target_language_name": "德语",
        "reference_images": [],
    })
    monkeypatch.setattr(controller, "_download_references", lambda *args, **kwargs: [])
    monkeypatch.setattr(controller.browser_worker, "capture_page", lambda **kwargs: {
        "requested_url": "https://newjoyloo.com/de/products/demo-rjc",
        "final_url": "https://newjoyloo.com/de/products/demo-rjc",
        "html_lang": "de",
        "locked": True,
        "downloaded_images": [],
        "image_urls": [],
    })
    monkeypatch.setattr(controller.analysis, "analyze_downloaded_images", lambda **kwargs: {"summary": {"pass_count": 0}, "items": []})

    result = controller.run_link_check(
        base_url="http://127.0.0.1:5000",
        api_key="demo-key",
        target_url="https://newjoyloo.com/de/products/demo-rjc",
        status_cb=lambda message: None,
    )

    assert result["product"]["id"] == 123
    assert result["page"]["locked"] is True
    assert result["analysis"]["summary"]["pass_count"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_link_check_desktop_controller.py -v`

Expected: FAIL with `ImportError` because `controller`, `browser_worker`, and `analysis` do not exist yet.

- [ ] **Step 3: Write minimal browser and controller implementation**

```python
# link_check_desktop/analysis.py
from __future__ import annotations

from appcore.link_check_compare import find_best_reference, run_binary_quick_check
from appcore.link_check_gemini import analyze_image
from appcore.link_check_same_image import judge_same_image


def analyze_downloaded_images(*, downloaded_images: list[dict], reference_images: list[dict], target_language: str, target_language_name: str) -> dict:
    reference_paths = [item["local_path"] for item in reference_images]
    output_items = []
    summary = {"pass_count": 0, "replace_count": 0, "review_count": 0}
    for item in downloaded_images:
        match = find_best_reference(item["local_path"], reference_paths) if reference_paths else {"status": "not_provided", "reference_path": ""}
        binary = {"status": "skipped"} if match.get("status") != "matched" else run_binary_quick_check(item["local_path"], match["reference_path"])
        if binary.get("status") == "pass":
            analysis = {"decision": "pass", "decision_source": "binary_quick_check"}
            same_image = {"status": "skipped"}
        elif binary.get("status") == "fail":
            analysis = {"decision": "replace", "decision_source": "binary_quick_check"}
            same_image = judge_same_image(item["local_path"], match["reference_path"])
        else:
            same_image = judge_same_image(item["local_path"], match["reference_path"]) if match.get("status") == "matched" else {"status": "skipped"}
            analysis = analyze_image(item["local_path"], target_language=target_language, target_language_name=target_language_name)
        output_items.append({**item, "reference_match": match, "binary_quick_check": binary, "same_image_llm": same_image, "analysis": analysis})
        summary[f"{analysis['decision']}_count"] = summary.get(f"{analysis['decision']}_count", 0) + 1
    summary["overall_decision"] = "done" if summary.get("replace_count", 0) == 0 and summary.get("review_count", 0) == 0 else "unfinished"
    return {"summary": summary, "items": output_items}
```

```python
# link_check_desktop/browser_worker.py
from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright


def capture_page(*, target_url: str, target_language: str, workspace, status_cb) -> dict:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=False)
        context = browser.new_context(locale=target_language)
        page = context.new_page()
        status_cb("正在打开浏览器")
        page.goto(target_url, wait_until="domcontentloaded")
        first_final_url = page.url
        html_lang = page.eval_on_selector("html", "el => el.lang || ''")
        if not (html_lang or "").lower().startswith(target_language.lower()):
            page.goto(target_url, wait_until="domcontentloaded")
        final_url = page.url
        html_lang = page.eval_on_selector("html", "el => el.lang || ''")
        html = page.content()
        (workspace.root / "page.html").write_text(html, encoding="utf-8")
        browser.close()
    return {
        "requested_url": target_url,
        "first_final_url": first_final_url,
        "second_final_url": final_url,
        "final_url": final_url,
        "html_lang": html_lang,
        "locked": (html_lang or "").lower().startswith(target_language.lower()),
        "image_urls": [],
        "downloaded_images": [],
    }
```

```python
# link_check_desktop/controller.py
from __future__ import annotations

from link_check_desktop import analysis, bootstrap_api, browser_worker, result_schema, storage


def _noop(_message: str) -> None:
    return None


def _download_references(reference_images: list[dict], workspace, status_cb) -> list[dict]:
    return []


def run_link_check(*, base_url: str, api_key: str, target_url: str, status_cb=None):
    reporter = status_cb or _noop
    reporter("正在解析产品和语种")
    bootstrap = bootstrap_api.fetch_bootstrap(base_url, api_key, target_url)
    workspace = storage.create_workspace(bootstrap["product"]["id"])
    reference_images = _download_references(bootstrap["reference_images"], workspace, reporter)
    reporter("正在通过浏览器锁定目标页")
    page_result = browser_worker.capture_page(
        target_url=target_url,
        target_language=bootstrap["target_language"],
        workspace=workspace,
        status_cb=reporter,
    )
    reporter("正在分析图片")
    analyzed = analysis.analyze_downloaded_images(
        downloaded_images=page_result["downloaded_images"],
        reference_images=reference_images,
        target_language=bootstrap["target_language"],
        target_language_name=bootstrap["target_language_name"],
    )
    result = {
        "product": bootstrap["product"],
        "target_language": bootstrap["target_language"],
        "target_language_name": bootstrap["target_language_name"],
        "page": page_result,
        "analysis": analyzed,
    }
    storage.write_json(workspace.root / "task.json", result_schema.build_task_manifest(target_url, bootstrap, workspace))
    storage.write_json(workspace.root / "page_info.json", page_result)
    storage.write_json(workspace.compare_dir / "result.json", analyzed)
    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_link_check_desktop_controller.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add link_check_desktop/analysis.py link_check_desktop/browser_worker.py link_check_desktop/controller.py tests/test_link_check_desktop_controller.py
git commit -m "feat(desktop): add browser capture and controller flow"
```

## Task 7: Desktop GUI, Packaging, And Project Commands

**Files:**
- Create: `link_check_desktop/gui.py`
- Create: `link_check_desktop/main.py`
- Create: `link_check_desktop/README.md`
- Create: `link_check_desktop/packaging/link_check_desktop.spec`
- Modify: `AGENTS.md`

- [ ] **Step 1: Write the failing smoke test**

```python
def test_main_window_exposes_start_button(monkeypatch):
    from link_check_desktop.gui import LinkCheckApp

    app = LinkCheckApp()
    try:
        assert app.start_button["text"] == "开始检查"
        assert app.url_var.get() == ""
    finally:
        app.root.destroy()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_link_check_desktop_gui.py -v`

Expected: FAIL with `ModuleNotFoundError` because `gui.py` does not exist yet.

- [ ] **Step 3: Write minimal GUI and packaging files**

```python
# link_check_desktop/gui.py
from __future__ import annotations

import threading
import tkinter as tk
from tkinter import messagebox

from link_check_desktop import controller


class LinkCheckApp:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("Link Check Desktop")
        self.url_var = tk.StringVar()
        self.status_var = tk.StringVar(value="请输入目标页链接")
        self.result_var = tk.StringVar(value="")

        tk.Label(self.root, text="目标页链接").pack(anchor="w", padx=12, pady=(12, 4))
        tk.Entry(self.root, textvariable=self.url_var, width=90).pack(fill="x", padx=12)
        self.start_button = tk.Button(self.root, text="开始检查", command=self.start_run)
        self.start_button.pack(anchor="e", padx=12, pady=12)
        tk.Label(self.root, textvariable=self.status_var, justify="left").pack(anchor="w", padx=12)
        tk.Label(self.root, textvariable=self.result_var, justify="left").pack(anchor="w", padx=12, pady=(8, 12))

    def start_run(self) -> None:
        target_url = self.url_var.get().strip()
        if not target_url:
            messagebox.showerror("错误", "请先输入目标页链接")
            return
        self.start_button.configure(state="disabled")
        threading.Thread(target=self._run, args=(target_url,), daemon=True).start()

    def _run(self, target_url: str) -> None:
        try:
            result = controller.run_link_check(
                base_url="http://172.30.254.14",
                api_key="autovideosrt-materials-openapi",
                target_url=target_url,
                status_cb=lambda message: self.root.after(0, self.status_var.set, message),
            )
            summary = result["analysis"]["summary"]
            text = f"产品 ID: {result['product']['id']}\\n语种: {result['target_language']}\\n通过: {summary.get('pass_count', 0)}\\n替换: {summary.get('replace_count', 0)}\\n复核: {summary.get('review_count', 0)}"
            self.root.after(0, self.result_var.set, text)
        except Exception as exc:
            self.root.after(0, messagebox.showerror, "执行失败", str(exc))
        finally:
            self.root.after(0, lambda: self.start_button.configure(state="normal"))
```

```python
# link_check_desktop/main.py
from link_check_desktop.gui import LinkCheckApp


def main() -> None:
    app = LinkCheckApp()
    app.root.mainloop()


if __name__ == "__main__":
    main()
```

```python
# link_check_desktop/packaging/link_check_desktop.spec
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

root = Path(__file__).resolve().parents[2]
hiddenimports = collect_submodules("skimage")

a = Analysis(
    [str(root / "link_check_desktop" / "main.py")],
    pathex=[str(root)],
    hiddenimports=hiddenimports,
    datas=[],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="LinkCheckDesktop", console=False)
coll = COLLECT(exe, a.binaries, a.datas, name="LinkCheckDesktop")
```

```text
# link_check_desktop/README.md
开发运行：
python -m link_check_desktop.main

打包：
pyinstaller link_check_desktop/packaging/link_check_desktop.spec
```

```md
# AGENTS.md
- `link_check_desktop` 开发运行：`python -m link_check_desktop.main`
- `link_check_desktop` 定向测试：`pytest tests/test_link_check_bootstrap_routes.py tests/test_appcore_medias_link_check_bootstrap.py tests/test_link_check_gemini.py tests/test_link_check_same_image.py tests/test_link_check_desktop_storage.py tests/test_link_check_desktop_bootstrap_api.py tests/test_link_check_desktop_controller.py -q`
- `link_check_desktop` 打包：`pyinstaller link_check_desktop/packaging/link_check_desktop.spec`
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_link_check_desktop_gui.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add link_check_desktop/gui.py link_check_desktop/main.py link_check_desktop/README.md link_check_desktop/packaging/link_check_desktop.spec AGENTS.md tests/test_link_check_desktop_gui.py
git commit -m "feat(desktop): add gui entrypoint and packaging setup"
```

## Task 8: Focused Verification

**Files:**
- Modify: none
- Test: `tests/test_appcore_medias_link_check_bootstrap.py`
- Test: `tests/test_link_check_bootstrap_routes.py`
- Test: `tests/test_link_check_gemini.py`
- Test: `tests/test_link_check_same_image.py`
- Test: `tests/test_link_check_desktop_storage.py`
- Test: `tests/test_link_check_desktop_bootstrap_api.py`
- Test: `tests/test_link_check_desktop_controller.py`
- Test: `tests/test_link_check_desktop_gui.py`

- [ ] **Step 1: Run the focused server and desktop tests**

Run:

```bash
pytest tests/test_appcore_medias_link_check_bootstrap.py \
       tests/test_link_check_bootstrap_routes.py \
       tests/test_link_check_gemini.py \
       tests/test_link_check_same_image.py \
       tests/test_link_check_desktop_storage.py \
       tests/test_link_check_desktop_bootstrap_api.py \
       tests/test_link_check_desktop_controller.py \
       tests/test_link_check_desktop_gui.py -v
```

Expected: PASS for all targeted tests.

- [ ] **Step 2: Run a packaging smoke build**

Run: `pyinstaller link_check_desktop/packaging/link_check_desktop.spec`

Expected: build completes and creates `dist/LinkCheckDesktop/LinkCheckDesktop.exe`.

- [ ] **Step 3: Run a manual desktop smoke**

Run: `python -m link_check_desktop.main`

Expected:

1. Tkinter window opens.
2. Entering a valid target URL starts the flow.
3. A visible Edge window launches.
4. The client writes `img/<product_id>-<timestamp>/task.json`, `page_info.json`, and `compare/result.json`.

- [ ] **Step 4: Commit**

```bash
git add .
git commit -m "test(desktop): verify link check desktop focused workflow"
```

## Self-Review

### Spec coverage

Covered requirements:

1. Windows desktop subproject: Tasks 5-7
2. Bootstrap route and product-id mapping: Tasks 1-3
3. Target-language detection: Task 3
4. Reference-image download contract: Tasks 2-3 and Task 5
5. Visible Edge with first-visit plus second-visit lock: Task 6
6. Local workspace naming `img/<product_id>-<timestamp>/`: Task 5
7. Existing comparison and LLM logic reuse: Tasks 4 and 6
8. PyInstaller packaging: Task 7 and Task 8
9. AGENTS commands update: Task 7

No spec sections are left without a task.

### Placeholder scan

Checked for:

1. `TBD`
2. `TODO`
3. `implement later`
4. vague “add validation” wording without code

Result: no placeholders remain.

### Type consistency

Consistent names used throughout:

1. `find_product_for_link_check_url`
2. `list_reference_images_for_lang`
3. `fetch_bootstrap`
4. `create_workspace`
5. `capture_page`
6. `analyze_downloaded_images`
7. `run_link_check`

No later task renames these functions.
