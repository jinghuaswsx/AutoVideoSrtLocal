# Link Check Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a first-version “链接检查” module that accepts a localized Shopify product URL, verifies the page stays on the requested locale, downloads localized product/detail images, analyzes image language with Gemini Flash on Vertex AI, and optionally matches downloaded site images against uploaded reference images using deterministic image fingerprints.

**Architecture:** The feature is a non-persistent, in-memory background task that reuses `appcore.task_state` / `web.store` for ownership and polling, but does not write to `projects`. The backend is split into four focused units: locale-locked page fetching, deterministic reference-image matching, Gemini-based language analysis, and a runtime/runner that orchestrates them. The UI is a single page under a new `link_check` blueprint and polls task state from the backend; it reuses the existing `/api/languages` endpoint instead of creating a duplicate language API.

**Tech Stack:** Flask, Jinja, existing `web.store` / `appcore.task_state`, `requests`, `beautifulsoup4`, `Pillow`, `ImageHash`, `scikit-image`, `google-genai`, pytest.

---

## File Structure

**Create**
- `appcore/link_check_compare.py`
  - Normalize two images and compute `pHash` / `dHash` / `SSIM`-based match scores.
- `appcore/link_check_fetcher.py`
  - Lock the requested locale, parse Shopify product/detail images, and download them to a task directory.
- `appcore/link_check_gemini.py`
  - Build the structured Gemini prompt and parse the JSON result for one image.
- `appcore/link_check_runtime.py`
  - Orchestrate locale locking, image download, optional reference matching, Gemini analysis, and summary aggregation.
- `web/services/link_check_runner.py`
  - Background thread launcher for one in-memory link-check task.
- `web/routes/link_check.py`
  - Page route, task create/status routes, and authenticated preview-image routes.
- `web/templates/link_check.html`
  - Page shell for the create form, progress section, and results cards.
- `web/static/link_check.css`
  - Ocean Blue–aligned page styles for the new page.
- `web/static/link_check.js`
  - Form submission, polling, and result rendering logic.
- `tests/test_gemini_client.py`
  - Vertex client initialization tests for `appcore.gemini`.
- `tests/test_link_check_compare.py`
  - Unit tests for fingerprint-based image matching.
- `tests/test_link_check_fetcher.py`
  - Unit tests for locale locking, HTML parsing, and image extraction.
- `tests/test_link_check_gemini.py`
  - Unit tests for structured Gemini image analysis.
- `tests/test_link_check_runtime.py`
  - Runtime orchestration tests.
- `tests/test_link_check_routes.py`
  - Flask route tests for page render, task creation, status polling, and preview authorization.

**Modify**
- `requirements.txt`
  - Add explicit runtime dependencies for HTML parsing and image comparison.
- `config.py:83-128`
  - Add Vertex AI project/location settings and stop treating cloud mode like API-key auth.
- `appcore/gemini.py:47-80`
  - Initialize Vertex clients with `vertexai=True`, `project`, and `location`.
- `appcore/task_state.py:494-539`
  - Add a `create_link_check()` task constructor that stores in-memory state without DB upsert.
- `web/store.py:1-31`
  - Export `create_link_check`.
- `web/app.py:32-55`
  - Import the new blueprint.
- `web/app.py:172-198`
  - Register the new blueprint.
- `web/templates/layout.html:293-336`
  - Add the new “链接检查” sidebar entry.
- `tests/test_config.py`
  - Assert the new Vertex config variables are parsed correctly.

## Task 1: Vertex AI Configuration and Dependencies

**Files:**
- Modify: `requirements.txt`
- Modify: `config.py:83-128`
- Modify: `appcore/gemini.py:47-80`
- Modify: `tests/test_config.py`
- Create: `tests/test_gemini_client.py`

- [ ] **Step 1: Write the failing config and Vertex-client tests**

```python
# tests/test_config.py
def test_gemini_cloud_project_location_defaults(monkeypatch):
    monkeypatch.setenv("AUTOVIDEOSRT_DISABLE_DOTENV", "1")
    monkeypatch.setenv("GEMINI_BACKEND", "cloud")
    monkeypatch.setenv("GEMINI_CLOUD_PROJECT", "demo-project")
    monkeypatch.setenv("GEMINI_CLOUD_LOCATION", "global")

    import importlib
    config = importlib.import_module("config")
    config = importlib.reload(config)

    assert config.GEMINI_CLOUD_PROJECT == "demo-project"
    assert config.GEMINI_CLOUD_LOCATION == "global"
```

```python
# tests/test_gemini_client.py
import importlib


def test_get_client_uses_vertex_project_location(monkeypatch):
    gemini = importlib.import_module("appcore.gemini")
    gemini = importlib.reload(gemini)
    gemini._clients.clear()

    created = {}

    class DummyClient:
        pass

    def fake_client(**kwargs):
        created.update(kwargs)
        return DummyClient()

    monkeypatch.setattr(gemini, "GEMINI_BACKEND", "cloud")
    monkeypatch.setattr(gemini, "GEMINI_CLOUD_PROJECT", "demo-project")
    monkeypatch.setattr(gemini, "GEMINI_CLOUD_LOCATION", "global")
    monkeypatch.setattr(gemini.genai, "Client", fake_client)

    client = gemini._get_client("")

    assert isinstance(client, DummyClient)
    assert created["vertexai"] is True
    assert created["project"] == "demo-project"
    assert created["location"] == "global"
```

- [ ] **Step 2: Run the tests to verify they fail for the right reason**

Run: `pytest tests/test_config.py::test_gemini_cloud_project_location_defaults tests/test_gemini_client.py::test_get_client_uses_vertex_project_location -v`

Expected: `FAIL` because `config.py` does not expose `GEMINI_CLOUD_PROJECT` / `GEMINI_CLOUD_LOCATION`, and `appcore.gemini._get_client()` still constructs `genai.Client(vertexai=True, api_key=...)`.

- [ ] **Step 3: Add the new config values and fix cloud client initialization**

```python
# requirements.txt
beautifulsoup4>=4.12,<5.0
Pillow>=10.4,<11.0
ImageHash>=4.3,<5.0
scikit-image>=0.24,<1.0
```

```python
# config.py
GEMINI_CLOUD_PROJECT = _env("GEMINI_CLOUD_PROJECT")
GEMINI_CLOUD_LOCATION = _env("GEMINI_CLOUD_LOCATION", "global")
```

```python
# appcore/gemini.py
from config import (
    GEMINI_API_KEY,
    GEMINI_BACKEND,
    GEMINI_MODEL,
    GEMINI_CLOUD_PROJECT,
    GEMINI_CLOUD_LOCATION,
)


def _client_cache_key(api_key: str) -> str:
    if GEMINI_BACKEND == "cloud":
        return f"cloud:{GEMINI_CLOUD_PROJECT}:{GEMINI_CLOUD_LOCATION}"
    return f"aistudio:{api_key}"


def _get_client(api_key: str) -> genai.Client:
    cache_key = _client_cache_key(api_key)
    if cache_key not in _clients:
        if GEMINI_BACKEND == "cloud":
            if not GEMINI_CLOUD_PROJECT:
                raise GeminiError("GEMINI_CLOUD_PROJECT 未配置，无法使用 Vertex AI")
            _clients[cache_key] = genai.Client(
                vertexai=True,
                project=GEMINI_CLOUD_PROJECT,
                location=GEMINI_CLOUD_LOCATION,
            )
        else:
            _clients[cache_key] = genai.Client(api_key=api_key)
    return _clients[cache_key]
```

- [ ] **Step 4: Re-run the focused tests and confirm they pass**

Run: `pytest tests/test_config.py::test_gemini_cloud_project_location_defaults tests/test_gemini_client.py::test_get_client_uses_vertex_project_location -v`

Expected: both tests `PASS`.

- [ ] **Step 5: Commit the foundation changes**

```bash
git add requirements.txt config.py appcore/gemini.py tests/test_config.py tests/test_gemini_client.py
git commit -m "feat: configure vertex ai client for link check"
```

### Task 2: Deterministic Reference-Image Matching

**Files:**
- Create: `appcore/link_check_compare.py`
- Create: `tests/test_link_check_compare.py`

- [ ] **Step 1: Write the failing comparison tests**

```python
# tests/test_link_check_compare.py
from pathlib import Path

from PIL import Image, ImageDraw


def _make_sample(path: Path, *, size: tuple[int, int], quality: int = 95) -> Path:
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((24, 24, size[0] - 24, size[1] - 24), outline="navy", width=6)
    draw.text((40, 40), "DE SAMPLE", fill="black")
    image.save(path, quality=quality)
    return path


def test_same_image_with_different_sizes_matches(tmp_path):
    from appcore.link_check_compare import compare_images

    left = _make_sample(tmp_path / "left.jpg", size=(1200, 800))
    right = _make_sample(tmp_path / "right.jpg", size=(600, 400))

    result = compare_images(left, right)

    assert result["status"] == "matched"
    assert result["score"] >= 0.85


def test_same_image_with_different_compression_matches(tmp_path):
    from appcore.link_check_compare import compare_images

    left = _make_sample(tmp_path / "left.jpg", size=(1200, 800), quality=95)
    right = _make_sample(tmp_path / "right.jpg", size=(1200, 800), quality=35)

    result = compare_images(left, right)

    assert result["status"] == "matched"
    assert result["score"] >= 0.80


def test_different_images_do_not_match(tmp_path):
    from appcore.link_check_compare import compare_images

    left = _make_sample(tmp_path / "left.jpg", size=(1200, 800))
    other = Image.new("RGB", (1200, 800), "red")
    other.save(tmp_path / "other.jpg")

    result = compare_images(left, tmp_path / "other.jpg")

    assert result["status"] == "not_matched"
    assert result["score"] < 0.60
```

- [ ] **Step 2: Run the comparison tests to confirm the module is missing**

Run: `pytest tests/test_link_check_compare.py -v`

Expected: `FAIL` with `ModuleNotFoundError: No module named 'appcore.link_check_compare'`.

- [ ] **Step 3: Implement image normalization, fingerprinting, and scoring**

```python
# appcore/link_check_compare.py
from __future__ import annotations

from pathlib import Path

import imagehash
import numpy as np
from PIL import Image, ImageOps
from skimage.metrics import structural_similarity


def _normalize(path: str | Path, *, size: int = 256) -> Image.Image:
    image = Image.open(path).convert("RGB")
    image = ImageOps.exif_transpose(image)
    image.thumbnail((size, size))
    canvas = Image.new("RGB", (size, size), "white")
    offset = ((size - image.width) // 2, (size - image.height) // 2)
    canvas.paste(image, offset)
    return canvas


def compare_images(candidate_path: str | Path, reference_path: str | Path) -> dict:
    left = _normalize(candidate_path)
    right = _normalize(reference_path)

    phash_distance = imagehash.phash(left) - imagehash.phash(right)
    dhash_distance = imagehash.dhash(left) - imagehash.dhash(right)
    ssim_score = structural_similarity(
        np.asarray(left.convert("L")),
        np.asarray(right.convert("L")),
    )
    ratio_delta = abs((left.width / left.height) - (right.width / right.height))

    phash_score = max(0.0, 1.0 - (phash_distance / 64.0))
    dhash_score = max(0.0, 1.0 - (dhash_distance / 64.0))
    ratio_score = max(0.0, 1.0 - min(ratio_delta, 1.0))
    score = round(phash_score * 0.40 + dhash_score * 0.25 + ssim_score * 0.30 + ratio_score * 0.05, 4)

    status = "matched" if score >= 0.80 else "weak_match" if score >= 0.65 else "not_matched"
    return {
        "status": status,
        "score": score,
        "phash_distance": phash_distance,
        "dhash_distance": dhash_distance,
        "ssim": round(float(ssim_score), 4),
        "ratio_delta": round(ratio_delta, 4),
    }


def find_best_reference(candidate_path: str | Path, reference_paths: list[str | Path]) -> dict:
    best = None
    for ref_path in reference_paths:
        current = compare_images(candidate_path, ref_path)
        current["reference_path"] = str(ref_path)
        if best is None or current["score"] > best["score"]:
            best = current
    return best or {"status": "not_provided", "score": 0.0, "reference_path": ""}
```

- [ ] **Step 4: Re-run the comparison tests and confirm they pass**

Run: `pytest tests/test_link_check_compare.py -v`

Expected: all three tests `PASS`.

- [ ] **Step 5: Commit the comparison engine**

```bash
git add appcore/link_check_compare.py tests/test_link_check_compare.py
git commit -m "feat: add deterministic link check image matching"
```

### Task 3: Locale-Locked Shopify Fetcher

**Files:**
- Create: `appcore/link_check_fetcher.py`
- Create: `tests/test_link_check_fetcher.py`

- [ ] **Step 1: Write the failing locale-locking and extraction tests**

```python
# tests/test_link_check_fetcher.py
from types import SimpleNamespace

import pytest


def test_fetch_page_sets_accept_language(monkeypatch):
    from appcore.link_check_fetcher import LinkCheckFetcher

    captured = {}

    def fake_get(url, *, headers, allow_redirects, timeout):
        captured["headers"] = headers
        return SimpleNamespace(
            url=url,
            status_code=200,
            text="<html lang='de'><body><img src='https://img.example.com/a.jpg'></body></html>",
        )

    fetcher = LinkCheckFetcher()
    monkeypatch.setattr(fetcher.session, "get", fake_get)
    fetcher.fetch_page("https://shop.example.com/de/products/demo", "de")

    assert captured["headers"]["Accept-Language"].startswith("de-DE")


def test_fetch_page_rejects_wrong_html_lang(monkeypatch):
    from appcore.link_check_fetcher import LinkCheckFetcher, LocaleLockError

    def fake_get(url, *, headers, allow_redirects, timeout):
        return SimpleNamespace(
            url="https://shop.example.com/products/demo",
            status_code=200,
            text="<html lang='en'><body></body></html>",
        )

    fetcher = LinkCheckFetcher()
    monkeypatch.setattr(fetcher.session, "get", fake_get)

    with pytest.raises(LocaleLockError):
        fetcher.fetch_page("https://shop.example.com/de/products/demo", "de")


def test_extract_images_dedupes_and_labels_sections():
    from appcore.link_check_fetcher import extract_images_from_html

    html = '''
    <html lang="de">
      <body>
        <div class="product__media"><img src="https://img.example.com/hero.jpg?width=640"></div>
        <div class="product__media"><img src="https://img.example.com/hero.jpg?width=1280"></div>
        <div class="rte"><img src="https://img.example.com/detail.jpg"></div>
      </body>
    </html>
    '''

    items = extract_images_from_html(html, base_url="https://shop.example.com/de/products/demo")

    assert [item["kind"] for item in items] == ["carousel", "detail"]
```

- [ ] **Step 2: Run the fetcher tests to verify they fail because the module does not exist**

Run: `pytest tests/test_link_check_fetcher.py -v`

Expected: `FAIL` with `ModuleNotFoundError: No module named 'appcore.link_check_fetcher'`.

- [ ] **Step 3: Implement locale locking, Shopify parsing, dedupe, and image download**

```python
# appcore/link_check_fetcher.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup


class LocaleLockError(RuntimeError):
    pass


def _accept_language(code: str) -> str:
    mapping = {
        "de": "de-DE,de;q=0.9,en;q=0.8",
        "fr": "fr-FR,fr;q=0.9,en;q=0.8",
        "pt": "pt-PT,pt;q=0.9,en;q=0.8",
    }
    return mapping.get(code, f"{code};q=0.9,en;q=0.8")


def _normalize_image_url(raw_url: str, base_url: str) -> str:
    absolute = urljoin(base_url, raw_url)
    parsed = urlparse(absolute)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


def _page_lang(soup: BeautifulSoup) -> str:
    html = soup.find("html")
    return (html.get("lang") or "").strip().lower() if html else ""


def extract_images_from_html(html: str, *, base_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    items = []
    seen = set()

    for node in soup.select(".product__media img, [data-media-id] img, .featured img"):
        src = node.get("src") or node.get("data-src")
        if not src:
            continue
        normalized = _normalize_image_url(src, base_url)
        if normalized in seen:
            continue
        seen.add(normalized)
        items.append({"kind": "carousel", "source_url": normalized})

    for node in soup.select(".rte img, .product__description img, [class*='description'] img"):
        src = node.get("src") or node.get("data-src")
        if not src:
            continue
        normalized = _normalize_image_url(src, base_url)
        if normalized in seen:
            continue
        seen.add(normalized)
        items.append({"kind": "detail", "source_url": normalized})

    return items


@dataclass
class FetchedPage:
    requested_url: str
    resolved_url: str
    page_language: str
    html: str
    images: list[dict]


class LinkCheckFetcher:
    def __init__(self) -> None:
        self.session = requests.Session()

    def fetch_page(self, url: str, target_language: str) -> FetchedPage:
        response = self.session.get(
            url,
            headers={"User-Agent": "Mozilla/5.0", "Accept-Language": _accept_language(target_language)},
            allow_redirects=True,
            timeout=20,
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        lang = _page_lang(soup)
        if target_language != "en" and not (
            f"/{target_language}/" in response.url or lang.startswith(target_language)
        ):
            raise LocaleLockError(
                f"目标语种页面锁定失败：target={target_language} resolved_url={response.url} page_lang={lang or 'unknown'}"
            )
        return FetchedPage(
            requested_url=url,
            resolved_url=response.url,
            page_language=lang,
            html=response.text,
            images=extract_images_from_html(response.text, base_url=response.url),
        )

    def download_images(self, images: list[dict], task_dir: str | Path) -> list[dict]:
        output_dir = Path(task_dir) / "site_images"
        output_dir.mkdir(parents=True, exist_ok=True)
        downloaded = []
        for index, item in enumerate(images):
            response = self.session.get(item["source_url"], headers={"User-Agent": "Mozilla/5.0"}, allow_redirects=True, timeout=20)
            response.raise_for_status()
            suffix = Path(urlparse(item["source_url"]).path).suffix or ".jpg"
            local_path = output_dir / f"site_{index:03d}{suffix}"
            local_path.write_bytes(response.content)
            downloaded.append({**item, "id": f"site-{index}", "local_path": str(local_path)})
        return downloaded
```

- [ ] **Step 4: Run the fetcher tests and confirm they pass**

Run: `pytest tests/test_link_check_fetcher.py -v`

Expected: all tests `PASS`.

- [ ] **Step 5: Commit the locale-aware fetcher**

```bash
git add appcore/link_check_fetcher.py tests/test_link_check_fetcher.py
git commit -m "feat: add locale locked link check fetcher"
```

### Task 4: Gemini Image Analysis Wrapper

**Files:**
- Create: `appcore/link_check_gemini.py`
- Create: `tests/test_link_check_gemini.py`

- [ ] **Step 1: Write the failing Gemini analysis tests**

```python
# tests/test_link_check_gemini.py
from pathlib import Path


def test_analyze_image_passes_media_and_schema(monkeypatch, tmp_path):
    from appcore import link_check_gemini as lcg

    image_path = tmp_path / "sample.jpg"
    image_path.write_bytes(b"fake")
    captured = {}

    def fake_generate(prompt, **kwargs):
        captured["prompt"] = prompt
        captured["kwargs"] = kwargs
        return {
            "has_text": True,
            "detected_language": "de",
            "language_match": True,
            "text_summary": "Hallo Welt",
            "quality_score": 95,
            "quality_reason": "ok",
            "needs_replacement": False,
            "decision": "pass",
        }

    monkeypatch.setattr(lcg.gemini, "generate", fake_generate)
    result = lcg.analyze_image(image_path, target_language="de", target_language_name="德语")

    assert result["decision"] == "pass"
    assert captured["kwargs"]["media"] == [image_path]
    assert captured["kwargs"]["response_schema"]["type"] == "object"


def test_analyze_image_normalizes_missing_keys(monkeypatch, tmp_path):
    from appcore import link_check_gemini as lcg

    image_path = tmp_path / "sample.jpg"
    image_path.write_bytes(b"fake")
    monkeypatch.setattr(lcg.gemini, "generate", lambda *args, **kwargs: {"decision": "replace"})

    result = lcg.analyze_image(image_path, target_language="de", target_language_name="德语")

    assert result["needs_replacement"] is True
    assert result["detected_language"] == ""
```

- [ ] **Step 2: Run the Gemini tests and verify they fail because the module is missing**

Run: `pytest tests/test_link_check_gemini.py -v`

Expected: `FAIL` with `ModuleNotFoundError: No module named 'appcore.link_check_gemini'`.

- [ ] **Step 3: Implement the structured analysis helper**

```python
# appcore/link_check_gemini.py
from __future__ import annotations

from pathlib import Path

from appcore import gemini

_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "has_text": {"type": "boolean"},
        "detected_language": {"type": "string"},
        "language_match": {"type": "boolean"},
        "text_summary": {"type": "string"},
        "quality_score": {"type": "integer"},
        "quality_reason": {"type": "string"},
        "needs_replacement": {"type": "boolean"},
        "decision": {"type": "string"},
    },
    "required": ["decision"],
}


def analyze_image(image_path: str | Path, *, target_language: str, target_language_name: str) -> dict:
    prompt = (
        "请只返回 JSON。分析这张商品图片中的可见文字，并判断其是否已经适配为目标语种。"
        f"目标语言代码：{target_language}；目标语言名称：{target_language_name}。"
        "如果图片没有文字，decision 返回 no_text。"
        "如果主要文字不是目标语种，decision 返回 replace。"
        "如果是目标语种但质量明显不自然，decision 返回 review。"
        "如果可以通过，decision 返回 pass。"
    )
    raw = gemini.generate(
        prompt,
        media=[Path(image_path)],
        response_schema=_RESPONSE_SCHEMA,
        temperature=0,
        service="gemini",
        default_model="gemini-2.5-flash",
    )
    return {
        "has_text": bool(raw.get("has_text", False)),
        "detected_language": str(raw.get("detected_language") or ""),
        "language_match": bool(raw.get("language_match", False)),
        "text_summary": str(raw.get("text_summary") or ""),
        "quality_score": int(raw.get("quality_score") or 0),
        "quality_reason": str(raw.get("quality_reason") or ""),
        "needs_replacement": bool(raw.get("needs_replacement", raw.get("decision") in {"replace", "review"})),
        "decision": str(raw.get("decision") or "review"),
    }
```

- [ ] **Step 4: Re-run the Gemini tests and confirm they pass**

Run: `pytest tests/test_link_check_gemini.py -v`

Expected: both tests `PASS`.

- [ ] **Step 5: Commit the Gemini wrapper**

```bash
git add appcore/link_check_gemini.py tests/test_link_check_gemini.py
git commit -m "feat: add link check gemini analyzer"
```

### Task 5: In-Memory Task State, Runtime, and Background Runner

**Files:**
- Modify: `appcore/task_state.py:494-539`
- Modify: `web/store.py:1-31`
- Create: `appcore/link_check_runtime.py`
- Create: `web/services/link_check_runner.py`
- Create: `tests/test_link_check_runtime.py`

- [ ] **Step 1: Write the failing runtime tests**

```python
# tests/test_link_check_runtime.py
from appcore import task_state


def test_runtime_marks_locale_failure(monkeypatch, tmp_path):
    from appcore.link_check_runtime import LinkCheckRuntime

    task = task_state.create_link_check(
        "lc-1",
        task_dir=str(tmp_path),
        user_id=1,
        link_url="https://shop.example.com/de/products/demo",
        target_language="de",
        target_language_name="德语",
        reference_images=[],
    )

    class DummyFetcher:
        def fetch_page(self, url, target_language):
            raise RuntimeError("locale lock failed")

    runtime = LinkCheckRuntime(fetcher=DummyFetcher())
    runtime.start("lc-1")

    saved = task_state.get("lc-1")
    assert saved["status"] == "failed"
    assert "locale lock failed" in saved["error"]


def test_runtime_records_best_reference_match(monkeypatch, tmp_path):
    from appcore.link_check_runtime import LinkCheckRuntime

    ref_path = tmp_path / "ref.jpg"
    ref_path.write_bytes(b"ref")
    site_path = tmp_path / "site.jpg"
    site_path.write_bytes(b"site")

    task = task_state.create_link_check(
        "lc-2",
        task_dir=str(tmp_path),
        user_id=1,
        link_url="https://shop.example.com/de/products/demo",
        target_language="de",
        target_language_name="德语",
        reference_images=[{"id": "ref-1", "filename": "ref.jpg", "local_path": str(ref_path)}],
    )

    class DummyFetcher:
        def fetch_page(self, url, target_language):
            return type("Page", (), {
                "resolved_url": url,
                "page_language": "de",
                "images": [{"id": "site-1", "kind": "carousel", "source_url": "https://img/site.jpg", "local_path": str(site_path)}],
            })()

        def download_images(self, images, task_dir):
            return images

    monkeypatch.setattr("appcore.link_check_runtime.find_best_reference", lambda *args, **kwargs: {
        "status": "matched",
        "score": 0.91,
        "reference_path": str(ref_path),
    })
    monkeypatch.setattr("appcore.link_check_runtime.analyze_image", lambda *args, **kwargs: {
        "decision": "pass",
        "has_text": True,
        "detected_language": "de",
        "language_match": True,
        "text_summary": "Hallo",
        "quality_score": 95,
        "quality_reason": "ok",
        "needs_replacement": False,
    })

    runtime = LinkCheckRuntime(fetcher=DummyFetcher())
    runtime.start("lc-2")

    saved = task_state.get("lc-2")
    assert saved["items"][0]["reference_match"]["status"] == "matched"
    assert saved["items"][0]["reference_match"]["reference_id"] == "ref-1"
    assert saved["summary"]["overall_decision"] == "done"
```

- [ ] **Step 2: Run the runtime tests to confirm the new state/runtime pieces do not exist yet**

Run: `pytest tests/test_link_check_runtime.py -v`

Expected: `FAIL` because `create_link_check()` and `LinkCheckRuntime` do not exist.

- [ ] **Step 3: Add `create_link_check()` to task state and export it via `web.store`**

```python
# appcore/task_state.py
def create_link_check(task_id: str, task_dir: str, *, user_id: int,
                      link_url: str, target_language: str,
                      target_language_name: str,
                      reference_images: list[dict]) -> dict:
    task = {
        "id": task_id,
        "type": "link_check",
        "status": "queued",
        "task_dir": task_dir,
        "link_url": link_url,
        "resolved_url": "",
        "page_language": "",
        "target_language": target_language,
        "target_language_name": target_language_name,
        "reference_images": reference_images,
        "progress": {"total": 0, "downloaded": 0, "analyzed": 0, "compared": 0, "failed": 0},
        "summary": {
            "pass_count": 0,
            "no_text_count": 0,
            "replace_count": 0,
            "review_count": 0,
            "reference_unmatched_count": 0,
            "overall_decision": "running",
        },
        "items": [],
        "error": "",
        "_user_id": user_id,
    }
    with _lock:
        _tasks[task_id] = task
    return task
```

```python
# web/store.py
from appcore.task_state import create_link_check

__all__.append("create_link_check")
```

- [ ] **Step 4: Implement the runtime and thread runner**

```python
# appcore/link_check_runtime.py
from __future__ import annotations

from pathlib import Path

from appcore.link_check_compare import find_best_reference
from appcore.link_check_fetcher import LinkCheckFetcher
from appcore.link_check_gemini import analyze_image
from web import store


class LinkCheckRuntime:
    def __init__(self, *, fetcher: LinkCheckFetcher | None = None) -> None:
        self.fetcher = fetcher or LinkCheckFetcher()

    def start(self, task_id: str) -> None:
        task = store.get(task_id)
        if not task or task.get("type") != "link_check":
            return
        try:
            store.update(task_id, status="locking_locale")
            page = self.fetcher.fetch_page(task["link_url"], task["target_language"])
            downloaded = self.fetcher.download_images(page.images, task["task_dir"])

            task["resolved_url"] = page.resolved_url
            task["page_language"] = page.page_language
            task["items"] = []
            task["progress"]["total"] = len(downloaded)
            task["progress"]["downloaded"] = len(downloaded)

            references = task.get("reference_images") or []
            reference_paths = [ref["local_path"] for ref in references]
            reference_index = {ref["local_path"]: ref for ref in references}

            for item in downloaded:
                result = {
                    "id": item["id"],
                    "kind": item["kind"],
                    "source_url": item["source_url"],
                    "_local_path": item["local_path"],
                    "analysis": {},
                    "reference_match": {"status": "not_provided", "score": 0.0},
                    "status": "running",
                    "error": "",
                }
                if reference_paths:
                    best_reference = find_best_reference(item["local_path"], reference_paths)
                    reference_meta = reference_index.get(best_reference.get("reference_path", ""), {})
                    result["reference_match"] = {
                        **best_reference,
                        "reference_id": reference_meta.get("id", ""),
                        "reference_filename": reference_meta.get("filename", ""),
                    }
                    task["progress"]["compared"] += 1

                result["analysis"] = analyze_image(
                    item["local_path"],
                    target_language=task["target_language"],
                    target_language_name=task["target_language_name"],
                )
                task["progress"]["analyzed"] += 1
                result["status"] = "done"
                task["items"].append(result)

            self._finalize(task)
            store.update(task_id, **task)
        except Exception as exc:
            store.update(task_id, status="failed", error=str(exc))

    def _finalize(self, task: dict) -> None:
        summary = {
            "pass_count": 0,
            "no_text_count": 0,
            "replace_count": 0,
            "review_count": 0,
            "reference_unmatched_count": 0,
            "overall_decision": "done",
        }
        for item in task["items"]:
            decision = item["analysis"].get("decision")
            if decision == "pass":
                summary["pass_count"] += 1
            elif decision == "no_text":
                summary["no_text_count"] += 1
            elif decision == "replace":
                summary["replace_count"] += 1
                summary["overall_decision"] = "unfinished"
            else:
                summary["review_count"] += 1
                summary["overall_decision"] = "unfinished"
            if item["reference_match"]["status"] == "not_matched":
                summary["reference_unmatched_count"] += 1
                summary["overall_decision"] = "unfinished"
        task["summary"] = summary
        task["status"] = "done" if summary["overall_decision"] == "done" else "review_ready"
```

```python
# web/services/link_check_runner.py
from __future__ import annotations

import threading

from appcore.link_check_runtime import LinkCheckRuntime

_running: set[str] = set()
_lock = threading.Lock()


def start(task_id: str) -> bool:
    with _lock:
        if task_id in _running:
            return False
        _running.add(task_id)

    runtime = LinkCheckRuntime()

    def run() -> None:
        try:
            runtime.start(task_id)
        finally:
            with _lock:
                _running.discard(task_id)

    threading.Thread(target=run, daemon=True).start()
    return True
```

- [ ] **Step 5: Re-run the runtime tests and confirm they pass**

Run: `pytest tests/test_link_check_runtime.py -v`

Expected: both tests `PASS`.

- [ ] **Step 6: Commit the task-state/runtime work**

```bash
git add appcore/task_state.py web/store.py appcore/link_check_runtime.py web/services/link_check_runner.py tests/test_link_check_runtime.py
git commit -m "feat: add in-memory runtime for link check tasks"
```

### Task 6: Routes, Preview Endpoints, and Blueprint Registration

**Files:**
- Create: `web/routes/link_check.py`
- Modify: `web/app.py:32-55`
- Modify: `web/app.py:172-198`
- Create: `tests/test_link_check_routes.py`

- [ ] **Step 1: Write the failing route tests**

```python
# tests/test_link_check_routes.py
import io


def test_link_check_page_renders_form(authed_client_no_db, monkeypatch):
    monkeypatch.setattr("web.app._run_startup_recovery", lambda: None)
    monkeypatch.setattr("web.app.recover_all_interrupted_tasks", lambda: None)

    response = authed_client_no_db.get("/link-check")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'id="linkCheckForm"' in html
    assert 'name="reference_images"' in html


def test_create_link_check_task_accepts_optional_reference_images(authed_client_no_db, monkeypatch, tmp_path):
    from web import store

    created = {}

    def fake_create(task_id, task_dir, **kwargs):
        created.update({"task_id": task_id, "task_dir": task_dir, **kwargs})
        return {"id": task_id, "type": "link_check", "_user_id": 1}

    monkeypatch.setattr(store, "create_link_check", fake_create)
    monkeypatch.setattr("web.routes.link_check.medias.get_language", lambda code: {"code": "de", "name_zh": "德语"})
    monkeypatch.setattr("web.routes.link_check.link_check_runner.start", lambda tid: True)

    response = authed_client_no_db.post(
        "/api/link-check/tasks",
        data={
            "link_url": "https://shop.example.com/de/products/demo",
            "target_language": "de",
            "reference_images": [(io.BytesIO(b"fake-image"), "ref-1.jpg")],
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 202
    assert created["target_language"] == "de"
    assert len(created["reference_images"]) == 1


def test_get_task_serializes_preview_urls(authed_client_no_db, monkeypatch):
    from web import store

    monkeypatch.setattr(store, "get", lambda task_id: {
        "id": task_id,
        "type": "link_check",
        "_user_id": 1,
        "status": "done",
        "progress": {"total": 1, "downloaded": 1, "analyzed": 1, "compared": 1, "failed": 0},
        "summary": {"overall_decision": "done"},
        "reference_images": [{"id": "ref-1", "filename": "ref.jpg", "local_path": "C:/tmp/ref.jpg"}],
        "items": [{
            "id": "site-1",
            "kind": "carousel",
            "source_url": "https://img/site.jpg",
            "_local_path": "C:/tmp/site.jpg",
            "analysis": {"decision": "pass"},
            "reference_match": {"status": "matched", "score": 0.9, "reference_id": "ref-1"},
            "status": "done",
            "error": "",
        }],
    })

    response = authed_client_no_db.get("/api/link-check/tasks/lc-1")
    payload = response.get_json()

    assert payload["items"][0]["site_preview_url"].endswith("/api/link-check/tasks/lc-1/images/site/site-1")
    assert payload["reference_images"][0]["preview_url"].endswith("/api/link-check/tasks/lc-1/images/reference/ref-1")
```

- [ ] **Step 2: Run the route tests to confirm the blueprint is missing**

Run: `pytest tests/test_link_check_routes.py -v`

Expected: `FAIL` with `404` or import errors because `/link-check` and `/api/link-check/tasks` do not exist.

- [ ] **Step 3: Implement the blueprint, task creation, polling, and preview-image routes**

```python
# web/routes/link_check.py
from __future__ import annotations

import os
import uuid
from pathlib import Path

from flask import Blueprint, abort, jsonify, render_template, request, send_file
from flask_login import current_user, login_required

from appcore import medias
from config import OUTPUT_DIR
from web import store
from web.services import link_check_runner

bp = Blueprint("link_check", __name__)


def _get_owned_task(task_id: str) -> dict:
    task = store.get(task_id)
    if not task or task.get("_user_id") != current_user.id or task.get("type") != "link_check":
        abort(404)
    return task


@bp.route("/link-check")
@login_required
def page():
    return render_template("link_check.html")


@bp.route("/api/link-check/tasks", methods=["POST"])
@login_required
def create_task():
    link_url = (request.form.get("link_url") or "").strip()
    target_language = (request.form.get("target_language") or "").strip().lower()
    if not link_url or not target_language:
        return jsonify({"error": "link_url 和 target_language 必填"}), 400
    language = medias.get_language(target_language)
    if not language or not language.get("enabled"):
        return jsonify({"error": "target_language 非法"}), 400

    task_id = str(uuid.uuid4())
    task_dir = Path(OUTPUT_DIR) / "link_check" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)

    references = []
    for index, storage in enumerate(request.files.getlist("reference_images")):
        if not storage or not storage.filename:
            continue
        suffix = Path(storage.filename).suffix.lower()
        local_path = task_dir / "reference" / f"ref_{index:03d}{suffix}"
        local_path.parent.mkdir(parents=True, exist_ok=True)
        storage.save(local_path)
        references.append({"id": f"ref-{index}", "filename": storage.filename, "local_path": str(local_path)})

    store.create_link_check(
        task_id,
        str(task_dir),
        user_id=current_user.id,
        link_url=link_url,
        target_language=target_language,
        target_language_name=language.get("name_zh") or target_language,
        reference_images=references,
    )
    link_check_runner.start(task_id)
    return jsonify({"task_id": task_id}), 202


@bp.route("/api/link-check/tasks/<task_id>")
@login_required
def get_task(task_id: str):
    task = _get_owned_task(task_id)
    payload = {
        "id": task["id"],
        "type": task["type"],
        "status": task["status"],
        "link_url": task["link_url"],
        "resolved_url": task.get("resolved_url", ""),
        "page_language": task.get("page_language", ""),
        "target_language": task["target_language"],
        "target_language_name": task["target_language_name"],
        "progress": task["progress"],
        "summary": task["summary"],
        "error": task.get("error", ""),
        "reference_images": [
            {
                "id": ref["id"],
                "filename": ref["filename"],
                "preview_url": f"/api/link-check/tasks/{task_id}/images/reference/{ref['id']}",
            }
            for ref in task.get("reference_images", [])
        ],
        "items": [
            {
                "id": item["id"],
                "kind": item["kind"],
                "source_url": item["source_url"],
                "site_preview_url": f"/api/link-check/tasks/{task_id}/images/site/{item['id']}",
                "analysis": item["analysis"],
                "reference_match": item["reference_match"],
                "status": item["status"],
                "error": item["error"],
            }
            for item in task.get("items", [])
        ],
    }
    return jsonify(payload)


@bp.route("/api/link-check/tasks/<task_id>/images/site/<image_id>")
@login_required
def get_site_image(task_id: str, image_id: str):
    task = _get_owned_task(task_id)
    item = next((it for it in task.get("items", []) if it["id"] == image_id), None)
    if not item:
        abort(404)
    return send_file(item["_local_path"])


@bp.route("/api/link-check/tasks/<task_id>/images/reference/<reference_id>")
@login_required
def get_reference_image(task_id: str, reference_id: str):
    task = _get_owned_task(task_id)
    ref = next((it for it in task.get("reference_images", []) if it["id"] == reference_id), None)
    if not ref:
        abort(404)
    return send_file(ref["local_path"])
```

```python
# web/app.py
from web.routes.link_check import bp as link_check_bp

app.register_blueprint(link_check_bp)
```

- [ ] **Step 4: Re-run the route tests and confirm they pass**

Run: `pytest tests/test_link_check_routes.py -v`

Expected: the page route and task-create route tests `PASS`.

- [ ] **Step 5: Commit the route and blueprint work**

```bash
git add web/routes/link_check.py web/app.py tests/test_link_check_routes.py
git commit -m "feat: add link check routes and runner entrypoints"
```

### Task 7: Single-Page UI and Navigation

**Files:**
- Create: `web/templates/link_check.html`
- Create: `web/static/link_check.css`
- Create: `web/static/link_check.js`
- Modify: `web/templates/layout.html:293-336`

- [ ] **Step 1: Write the failing page-render assertion for the new UI markers**

```python
# tests/test_link_check_routes.py
def test_link_check_page_contains_progress_and_results_shell(authed_client_no_db):
    response = authed_client_no_db.get("/link-check")
    html = response.get_data(as_text=True)

    assert 'id="linkCheckSummary"' in html
    assert 'id="linkCheckResults"' in html
```

- [ ] **Step 2: Run the page-render assertion and confirm it fails**

Run: `pytest tests/test_link_check_routes.py::test_link_check_page_contains_progress_and_results_shell -v`

Expected: `FAIL` because the page template does not yet render the progress/result containers.

- [ ] **Step 3: Build the page shell, styles, polling JS, and sidebar link**

```html
<!-- web/templates/link_check.html -->
{% extends "layout.html" %}
{% block title %}链接检查{% endblock %}
{% block page_title %}链接检查{% endblock %}
{% block content %}
<div class="lc-shell">
  <section class="lc-card">
    <form id="linkCheckForm" class="lc-form" enctype="multipart/form-data">
      <label for="linkUrl">检查链接</label>
      <input id="linkUrl" name="link_url" type="url" placeholder="https://example.com/de/products/demo" required>

      <label for="targetLanguage">目标语言</label>
      <select id="targetLanguage" name="target_language" required></select>

      <label for="referenceImages">参考图片（可选）</label>
      <input id="referenceImages" name="reference_images" type="file" accept="image/jpeg,image/png,image/webp" multiple>

      <button id="linkCheckSubmit" class="btn btn-primary" type="submit">开始检查</button>
    </form>
  </section>

  <section id="linkCheckSummary" class="lc-card"></section>
  <section id="linkCheckResults" class="lc-card"></section>
</div>
<link rel="stylesheet" href="{{ url_for('static', filename='link_check.css') }}">
<script src="{{ url_for('static', filename='link_check.js') }}"></script>
{% endblock %}
```

```javascript
// web/static/link_check.js
async function loadLanguages() {
  const res = await fetch("/api/languages");
  const data = await res.json();
  const select = document.getElementById("targetLanguage");
  select.innerHTML = '<option value="">请选择语言</option>';
  for (const item of data.items || []) {
    const option = document.createElement("option");
    option.value = item.code;
    option.textContent = item.name_zh;
    select.appendChild(option);
  }
}

async function createTask(formData) {
  const res = await fetch("/api/link-check/tasks", {
    method: "POST",
    body: formData,
  });
  return res.json();
}

async function pollTask(taskId) {
  const res = await fetch(`/api/link-check/tasks/${taskId}`);
  return res.json();
}

function renderTask(state) {
  document.getElementById("linkCheckSummary").innerHTML = `
    <div class="lc-summary-grid">
      <div>抓取图片：${state.progress.total}</div>
      <div>已分析：${state.progress.analyzed}</div>
      <div>异常：${state.progress.failed}</div>
      <div>整体结论：${state.summary.overall_decision}</div>
    </div>
  `;

  const cards = (state.items || []).map((item) => {
    const reference = item.reference_match || {};
    const referencePreview = reference.reference_id
      ? `/api/link-check/tasks/${state.id}/images/reference/${reference.reference_id}`
      : "";
    return `
      <article class="lc-result-card">
        <div class="lc-result-grid">
          <img class="lc-preview" src="${item.site_preview_url}" alt="site image">
          ${referencePreview ? `<img class="lc-preview" src="${referencePreview}" alt="reference image">` : `<div class="lc-preview lc-preview--empty">无参考图</div>`}
        </div>
        <div class="lc-result-meta">
          <div>类型：${item.kind}</div>
          <div>来源：${item.source_url}</div>
          <div>识别语言：${item.analysis.detected_language || "-"}</div>
          <div>判断：${item.analysis.decision || item.status}</div>
          <div>质量分：${item.analysis.quality_score ?? "-"}</div>
          <div>参考匹配：${reference.status || "not_provided"} (${reference.score ?? 0})</div>
          <div>说明：${item.analysis.quality_reason || item.error || "-"}</div>
        </div>
      </article>
    `;
  }).join("");
  document.getElementById("linkCheckResults").innerHTML = cards || "<div class='lc-empty'>等待结果…</div>";
}

document.addEventListener("DOMContentLoaded", async () => {
  await loadLanguages();
  const form = document.getElementById("linkCheckForm");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = await createTask(new FormData(form));
    if (!payload.task_id) return;
    const timer = window.setInterval(async () => {
      const state = await pollTask(payload.task_id);
      renderTask(state);
      if (["done", "failed", "review_ready"].includes(state.status)) {
        window.clearInterval(timer);
      }
    }, 1500);
  });
});
```

```css
/* web/static/link_check.css */
.lc-shell { display: grid; gap: 16px; }
.lc-card { background: var(--bg-card); border: 1px solid var(--border-main); border-radius: 12px; padding: 20px; }
.lc-form { display: grid; gap: 12px; }
.lc-summary-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; }
.lc-result-card { border: 1px solid var(--border-main); border-radius: 10px; padding: 12px; background: var(--bg-subtle, #f8fbff); }
.lc-result-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin-bottom: 12px; }
.lc-preview { width: 100%; max-height: 240px; object-fit: contain; border-radius: 8px; background: #eef4fb; }
.lc-preview--empty { display: grid; place-items: center; color: var(--fg-subtle, #64748b); }
.lc-result-meta { display: grid; gap: 6px; font-size: 13px; color: var(--text-main); }
.lc-empty { color: var(--fg-subtle, #64748b); }
```

```html
<!-- web/templates/layout.html -->
<a href="/link-check" {% if request.path.startswith('/link-check') %}class="active"{% endif %}>
  <span class="nav-icon">🔎</span> 链接检查
</a>
```

- [ ] **Step 4: Re-run the UI-facing route assertions**

Run: `pytest tests/test_link_check_routes.py::test_link_check_page_renders_form tests/test_link_check_routes.py::test_link_check_page_contains_progress_and_results_shell -v`

Expected: both tests `PASS`.

- [ ] **Step 5: Commit the UI and navigation changes**

```bash
git add web/templates/link_check.html web/static/link_check.css web/static/link_check.js web/templates/layout.html tests/test_link_check_routes.py
git commit -m "feat: add link check page and navigation"
```

## Self-Review

**Spec coverage**
- Sidebar entry: covered by Task 7.
- Single-page form with link/language/optional reference images: covered by Tasks 6-7.
- Locale locking to avoid silent English fallback: covered by Task 3.
- Downloading carousel/detail images from Shopify product pages: covered by Task 3.
- Optional deterministic same-image matching for Shopify-compressed assets: covered by Task 2 and consumed in Task 5.
- Gemini Flash language/quality analysis: covered by Task 4.
- In-memory, non-persistent background task with current-page polling only: covered by Task 5 and Task 6.
- Visualized results with previews and summary: covered by Task 7.
- Vertex AI initialization correction: covered by Task 1.

**Placeholder scan**
- No `TBD`, `TODO`, or “similar to above” placeholders remain.
- Each task lists exact files, focused tests, commands, and commit messages.

**Type consistency**
- Task state consistently uses `type="link_check"`.
- Image comparison returns `status` / `score`.
- Gemini analysis returns `decision` / `needs_replacement`.
- Runtime summary uses `overall_decision`.

## Notes

- This plan intentionally reuses `GET /api/languages` instead of adding a second languages endpoint for link check.
- This plan intentionally uses polling instead of Socket.IO to keep the first version smaller and easier to debug.
