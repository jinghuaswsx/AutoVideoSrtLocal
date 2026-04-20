# Link Check Locale Lock Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `link-check` prove it only downloads images after the page is confirmed to be the target locale, using Shopify warm-up retries with 2-second gaps, and expose that evidence through the API and detail page.

**Architecture:** Extend `appcore.link_check_fetcher` so locale locking becomes a strict gate with recorded per-attempt evidence. Persist that evidence through `appcore.link_check_runtime` and `appcore.task_state`, serialize it in `web.routes.link_check`, and render it in the existing detail page without creating a new UI surface.

**Tech Stack:** Python, `requests`, `BeautifulSoup`, Flask, vanilla JavaScript, pytest

---

## File Structure

- Modify: `appcore/link_check_fetcher.py`
  Responsibility: Shopify page warm-up retry logic, locale lock evidence, strict pre-download gate, image download evidence.
- Modify: `appcore/link_check_runtime.py`
  Responsibility: Persist `locale_evidence`, reject downloads when locale lock is not confirmed, carry `download_evidence` into task items.
- Modify: `appcore/task_state.py`
  Responsibility: Initialize and persist new `link_check` evidence fields.
- Modify: `web/routes/link_check.py`
  Responsibility: Return `locale_evidence` and `download_evidence` in task APIs.
- Modify: `web/static/link_check.js`
  Responsibility: Render page-level locale lock evidence and image-level download evidence.
- Modify: `web/static/link_check.css`
  Responsibility: Style evidence sections using the existing Ocean Blue admin language.
- Test: `tests/test_link_check_fetcher.py`
  Responsibility: Verify warm-up attempts, 2-second waits, alternate-locale fallback, and image download evidence.
- Test: `tests/test_link_check_runtime.py`
  Responsibility: Verify strict gating before download and evidence persistence.
- Test: `tests/test_link_check_routes.py`
  Responsibility: Verify task API serialization of the new evidence.
- Test: `tests/test_link_check_ui_assets.py`
  Responsibility: Verify detail-page evidence rendering contract.

---

### Task 1: Fetcher Locale Lock Warm-Up And Evidence

**Files:**
- Modify: `appcore/link_check_fetcher.py`
- Test: `tests/test_link_check_fetcher.py`

- [ ] **Step 1: Write the failing fetcher tests**

Add these tests to `tests/test_link_check_fetcher.py`:

```python
def test_fetch_page_warmup_second_attempt_locks_locale_and_records_attempts(monkeypatch):
    from appcore.link_check_fetcher import LinkCheckFetcher

    responses = [
        SimpleNamespace(
            url="https://shop.example.com/products/demo?variant=123",
            status_code=200,
            text="""
            <html lang="en">
              <head>
                <link rel="alternate" hreflang="de" href="https://shop.example.com/de/products/demo">
              </head>
              <body></body>
            </html>
            """,
        ),
        SimpleNamespace(
            url="https://shop.example.com/de/products/demo?variant=123",
            status_code=200,
            text="""
            <html lang="de">
              <body>
                <div data-media-id="1"><img data-src="https://img.example.com/de-hero.jpg?width=800"></div>
              </body>
            </html>
            """,
        ),
    ]
    requested_urls = []
    sleeps = []

    def fake_get(url, *, headers, allow_redirects, timeout):
        requested_urls.append(url)
        return responses[len(requested_urls) - 1]

    fetcher = LinkCheckFetcher(sleep_func=sleeps.append)
    monkeypatch.setattr(fetcher.session, "get", fake_get)

    page = fetcher.fetch_page("https://shop.example.com/de/products/demo?variant=123", "de")

    assert requested_urls == [
        "https://shop.example.com/de/products/demo?variant=123",
        "https://shop.example.com/de/products/demo?variant=123",
    ]
    assert sleeps == [2]
    assert page.locale_evidence["locked"] is True
    assert page.locale_evidence["lock_source"] == "warmup_attempt_2"
    assert [attempt["locked"] for attempt in page.locale_evidence["attempts"]] == [False, True]
```

```python
def test_fetch_page_waits_two_seconds_before_each_warmup_attempt(monkeypatch):
    from appcore.link_check_fetcher import LinkCheckFetcher, LocaleLockError

    responses = [
        SimpleNamespace(url="https://shop.example.com/products/demo", status_code=200, text="<html lang='en'></html>"),
        SimpleNamespace(url="https://shop.example.com/products/demo", status_code=200, text="<html lang='en'></html>"),
        SimpleNamespace(url="https://shop.example.com/products/demo", status_code=200, text="<html lang='en'></html>"),
        SimpleNamespace(url="https://shop.example.com/products/demo", status_code=200, text="<html lang='en'></html>"),
    ]
    sleeps = []

    def fake_get(url, *, headers, allow_redirects, timeout):
        return responses.pop(0)

    fetcher = LinkCheckFetcher(sleep_func=sleeps.append)
    monkeypatch.setattr(fetcher.session, "get", fake_get)

    with pytest.raises(LocaleLockError):
        fetcher.fetch_page("https://shop.example.com/de/products/demo", "de")

    assert sleeps == [2, 2]
```

```python
def test_fetch_page_uses_alternate_locale_after_failed_warmups(monkeypatch):
    from appcore.link_check_fetcher import LinkCheckFetcher

    responses = [
        SimpleNamespace(
            url="https://shop.example.com/products/demo?variant=123",
            status_code=200,
            text="""
            <html lang="en">
              <head>
                <link rel="alternate" hreflang="de" href="https://shop.example.com/de/products/demo">
              </head>
              <body></body>
            </html>
            """,
        ),
        SimpleNamespace(url="https://shop.example.com/products/demo?variant=123", status_code=200, text="<html lang='en'></html>"),
        SimpleNamespace(url="https://shop.example.com/products/demo?variant=123", status_code=200, text="<html lang='en'></html>"),
        SimpleNamespace(url="https://shop.example.com/de/products/demo?variant=123", status_code=200, text="<html lang='de'></html>"),
    ]

    def fake_get(url, *, headers, allow_redirects, timeout):
        return responses.pop(0)

    fetcher = LinkCheckFetcher(sleep_func=lambda seconds: None)
    monkeypatch.setattr(fetcher.session, "get", fake_get)

    page = fetcher.fetch_page("https://shop.example.com/de/products/demo?variant=123", "de")

    assert page.locale_evidence["lock_source"] == "alternate_locale"
    assert page.locale_evidence["attempts"][-1]["phase"] == "alternate_locale"
    assert page.locale_evidence["attempts"][-1]["locked"] is True
```

```python
def test_download_images_records_download_evidence_for_success(monkeypatch, tmp_path):
    from appcore.link_check_fetcher import LinkCheckFetcher

    def fake_get(url, *, headers, allow_redirects, timeout):
        return SimpleNamespace(
            url=url,
            status_code=200,
            content=b"hero-bytes",
            text="",
        )

    fetcher = LinkCheckFetcher()
    monkeypatch.setattr(fetcher.session, "get", fake_get)

    result = fetcher.download_images(
        [{"kind": "carousel", "source_url": "https://img.example.com/hero.jpg?width=640", "variant_selected": True}],
        tmp_path,
    )

    assert result[0]["download_evidence"] == {
        "requested_source_url": "https://img.example.com/hero.jpg?width=640",
        "resolved_source_url": "https://img.example.com/hero.jpg?width=640",
        "redirect_preserved_asset": True,
        "variant_selected": True,
        "evidence_status": "ok",
        "evidence_reason": "",
    }
```

- [ ] **Step 2: Run the fetcher tests to verify they fail**

Run: `pytest tests/test_link_check_fetcher.py -q`

Expected: FAIL because `LinkCheckFetcher` does not yet support injected sleep, multi-attempt locale evidence, or `download_evidence`.

- [ ] **Step 3: Implement locale-attempt evidence and strict lock gate**

Update `appcore/link_check_fetcher.py` with these focused changes:

```python
from dataclasses import dataclass
import time


def _empty_locale_evidence(requested_url: str, target_language: str) -> dict:
    return {
        "target_language": target_language,
        "requested_url": requested_url,
        "lock_source": "",
        "locked": False,
        "failure_reason": "",
        "attempts": [],
    }


def _append_attempt(evidence: dict, *, phase: str, attempt_index: int, wait_seconds: int, requested_url: str, resolved_url: str, page_language: str, locked: bool) -> None:
    evidence["attempts"].append(
        {
            "phase": phase,
            "attempt_index": attempt_index,
            "wait_seconds_before_request": wait_seconds,
            "requested_url": requested_url,
            "resolved_url": resolved_url,
            "page_language": page_language,
            "locked": locked,
        }
    )
```

```python
@dataclass
class FetchedPage:
    requested_url: str
    resolved_url: str
    page_language: str
    html: str
    images: list[dict]
    locale_evidence: dict
```

```python
class LinkCheckFetcher:
    def __init__(self, *, sleep_func=None) -> None:
        self.session = requests.Session()
        self._sleep = sleep_func or time.sleep
```

```python
def _variant_featured_images(soup: BeautifulSoup, *, base_url: str) -> list[dict]:
    selected_variant = _selected_variant_id(base_url)
    if not selected_variant:
        return []

    items: list[dict] = []
    for payload in _script_payloads(soup):
        for variant in _variant_candidates(payload):
            if str(variant.get("id") or "").strip() != selected_variant:
                continue
            source_url = _variant_featured_image_url(variant)
            if source_url:
                items.append(
                    {
                        "kind": "carousel",
                        "source_url": _absolute_image_url(source_url, base_url),
                        "variant_selected": True,
                    }
                )
    return items
```

```python
def _append_image(items: list[dict], seen: set[str], *, source_url: str, kind: str, variant_selected: bool = False) -> None:
    dedupe_key = _image_dedupe_key(source_url)
    if dedupe_key in seen:
        return
    seen.add(dedupe_key)
    items.append({"kind": kind, "source_url": source_url, "variant_selected": variant_selected})
```

Implement a `_lock_target_locale()` loop that:

```python
evidence = _empty_locale_evidence(requested_url, target_language)
attempt_index = 0

for phase, wait_seconds in [("initial", 0), ("warmup", 2), ("warmup", 2)]:
    if phase == "warmup":
        self._sleep(wait_seconds)
    attempt_index += 1
    response = self._request_page(requested_url, target_language)
    _raise_for_status(response)
    soup = BeautifulSoup(response.text, "html.parser")
    lang = _page_lang(soup)
    locked = _is_locale_locked(
        resolved_url=response.url,
        page_language=lang,
        target_language=target_language,
    )
    _append_attempt(
        evidence,
        phase=phase,
        attempt_index=attempt_index,
        wait_seconds=wait_seconds if phase == "warmup" else 0,
        requested_url=requested_url,
        resolved_url=response.url,
        page_language=lang,
        locked=locked,
    )
    if locked:
        evidence["locked"] = True
        evidence["lock_source"] = "initial" if phase == "initial" else f"warmup_attempt_{attempt_index}"
        return response, soup, lang, evidence
```

Then only after those three attempts fail:

```python
retry_url = _alternate_locale_url(
    soup,
    current_url=response.url,
    requested_url=requested_url,
    target_language=target_language,
)
```

If alternate-locale succeeds, append a final attempt with `phase="alternate_locale"` and `lock_source="alternate_locale"`. If all attempts fail, set:

```python
evidence["failure_reason"] = (
    f"locale lock failed: target={target_language} "
    f"resolved_url={response.url} page_lang={lang or 'unknown'}"
)
raise LocaleLockError(evidence["failure_reason"])
```

Finally, make `fetch_page()` return `FetchedPage(..., locale_evidence=evidence)` and only call `extract_images_from_html()` after evidence confirms `locked=True`.

- [ ] **Step 4: Implement image download evidence**

Extend `download_images()` in `appcore/link_check_fetcher.py` like this:

```python
def _build_download_evidence(item: dict, resolved_url: str, *, preserved_asset: bool) -> dict:
    return {
        "requested_source_url": item["source_url"],
        "resolved_source_url": resolved_url,
        "redirect_preserved_asset": preserved_asset,
        "variant_selected": bool(item.get("variant_selected")),
        "evidence_status": "ok" if preserved_asset else "mismatch",
        "evidence_reason": "" if preserved_asset else "final image URL did not preserve the original asset path",
    }
```

```python
preserved_asset = _same_image_target(item["source_url"], response.url)
download_evidence = _build_download_evidence(item, response.url, preserved_asset=preserved_asset)
if not preserved_asset:
    raise ImageRedirectMismatchError(
        f"image redirect mismatch: requested={item['source_url']} resolved={response.url}"
    )

downloaded.append(
    {
        **item,
        "id": f"site-{index}",
        "local_path": str(local_path),
        "resolved_source_url": response.url,
        "download_evidence": download_evidence,
    }
)
```

- [ ] **Step 5: Re-run the fetcher tests**

Run: `pytest tests/test_link_check_fetcher.py -q`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add appcore/link_check_fetcher.py tests/test_link_check_fetcher.py
git commit -m "feat: add locale lock warm-up evidence"
```

---

### Task 2: Runtime Gate And Task-State Persistence

**Files:**
- Modify: `appcore/link_check_runtime.py`
- Modify: `appcore/task_state.py`
- Test: `tests/test_link_check_runtime.py`

- [ ] **Step 1: Write the failing runtime tests**

Add these tests to `tests/test_link_check_runtime.py`:

```python
def test_runtime_persists_locale_evidence_and_download_evidence(monkeypatch):
    from appcore.link_check_runtime import LinkCheckRuntime

    task_dir = _workspace_tmp()
    site_path = task_dir / "site.jpg"
    site_path.write_bytes(b"site")

    task_state.create_link_check(
        "lc-evidence",
        task_dir=str(task_dir),
        user_id=1,
        link_url="https://shop.example.com/de/products/demo?variant=123",
        target_language="de",
        target_language_name="德语",
        reference_images=[],
    )

    class DummyFetcher:
        def fetch_page(self, url, target_language):
            return type(
                "Page",
                (),
                {
                    "resolved_url": url,
                    "page_language": "de",
                    "locale_evidence": {
                        "target_language": "de",
                        "requested_url": url,
                        "lock_source": "warmup_attempt_2",
                        "locked": True,
                        "failure_reason": "",
                        "attempts": [{"phase": "initial", "attempt_index": 1, "wait_seconds_before_request": 0, "requested_url": url, "resolved_url": "https://shop.example.com/products/demo?variant=123", "page_language": "en", "locked": False}],
                    },
                    "images": [{"id": "site-1", "kind": "carousel", "source_url": "https://img/site.jpg", "local_path": str(site_path), "download_evidence": {"requested_source_url": "https://img/site.jpg", "resolved_source_url": "https://img/site.jpg", "redirect_preserved_asset": True, "variant_selected": False, "evidence_status": "ok", "evidence_reason": ""}}],
                },
            )()

        def download_images(self, images, task_dir):
            return images

    monkeypatch.setattr("appcore.link_check_runtime.analyze_image", lambda *args, **kwargs: {"decision": "pass", "has_text": True, "detected_language": "de", "language_match": True, "text_summary": "Hallo", "quality_score": 95, "quality_reason": "ok", "needs_replacement": False})

    LinkCheckRuntime(fetcher=DummyFetcher()).start("lc-evidence")

    saved = task_state.get("lc-evidence")
    assert saved["locale_evidence"]["lock_source"] == "warmup_attempt_2"
    assert saved["items"][0]["download_evidence"]["evidence_status"] == "ok"
```

```python
def test_runtime_fails_before_download_when_page_not_locked(monkeypatch):
    from appcore.link_check_runtime import LinkCheckRuntime

    task_dir = _workspace_tmp()
    task_state.create_link_check(
        "lc-unlocked",
        task_dir=str(task_dir),
        user_id=1,
        link_url="https://shop.example.com/de/products/demo",
        target_language="de",
        target_language_name="德语",
        reference_images=[],
    )

    download_calls = []

    class DummyFetcher:
        def fetch_page(self, url, target_language):
            return type(
                "Page",
                (),
                {
                    "resolved_url": "https://shop.example.com/products/demo",
                    "page_language": "en",
                    "locale_evidence": {
                        "target_language": "de",
                        "requested_url": url,
                        "lock_source": "",
                        "locked": False,
                        "failure_reason": "locale lock failed",
                        "attempts": [],
                    },
                    "images": [],
                },
            )()

        def download_images(self, images, task_dir):
            download_calls.append((images, task_dir))
            return []

    LinkCheckRuntime(fetcher=DummyFetcher()).start("lc-unlocked")

    saved = task_state.get("lc-unlocked")
    assert saved["status"] == "failed"
    assert saved["progress"]["downloaded"] == 0
    assert saved["items"] == []
    assert download_calls == []
```

- [ ] **Step 2: Run the runtime tests to verify they fail**

Run: `pytest tests/test_link_check_runtime.py -q`

Expected: FAIL because `locale_evidence` is not initialized or persisted and runtime does not yet enforce the pre-download lock gate.

- [ ] **Step 3: Initialize new task-state fields**

Update `appcore/task_state.py` inside `create_link_check()`:

```python
"locale_evidence": {
    "target_language": target_language,
    "requested_url": link_url,
    "lock_source": "",
    "locked": False,
    "failure_reason": "",
    "attempts": [],
},
```

- [ ] **Step 4: Persist locale evidence in runtime and refuse unlocked pages**

Update `appcore/link_check_runtime.py` at the start of `start()`:

```python
page = self.fetcher.fetch_page(task["link_url"], task["target_language"])
task["resolved_url"] = page.resolved_url
task["page_language"] = page.page_language
task["locale_evidence"] = dict(page.locale_evidence or {})

if not task["locale_evidence"].get("locked"):
    raise RuntimeError(
        task["locale_evidence"].get("failure_reason")
        or "target page was not locked before download"
    )
```

This check must happen before:

```python
downloaded = self.fetcher.download_images(page.images, task["task_dir"])
```

- [ ] **Step 5: Carry download evidence into task items and DB sync**

Extend `_build_item_result()` in `appcore/link_check_runtime.py`:

```python
return {
    "id": item["id"],
    "kind": item["kind"],
    "source_url": item["source_url"],
    "_local_path": item["local_path"],
    "download_evidence": dict(item.get("download_evidence") or {}),
    "analysis": {},
    ...
}
```

Update `_persist()`:

```python
task_state.update(
    task_id,
    status=task["status"],
    resolved_url=task.get("resolved_url", ""),
    page_language=task.get("page_language", ""),
    locale_evidence=dict(task.get("locale_evidence") or {}),
    steps=task["steps"],
    step_messages=task["step_messages"],
    progress=task["progress"],
    items=task["items"],
    summary=task["summary"],
    error=task.get("error", ""),
)
```

- [ ] **Step 6: Re-run the runtime tests**

Run: `pytest tests/test_link_check_runtime.py -q`

Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add appcore/link_check_runtime.py appcore/task_state.py tests/test_link_check_runtime.py
git commit -m "feat: persist link check locale evidence"
```

---

### Task 3: Route Serialization For Evidence

**Files:**
- Modify: `web/routes/link_check.py`
- Test: `tests/test_link_check_routes.py`

- [ ] **Step 1: Write the failing route test**

Extend `tests/test_link_check_routes.py`:

```python
def test_get_task_serializes_locale_and_download_evidence(authed_user_client_no_db, monkeypatch):
    from web import store

    monkeypatch.setattr(
        "web.routes.link_check.query_one",
        lambda sql, args: {
            "id": args[0],
            "type": "link_check",
            "display_name": "Demo Link Check",
            "status": "done",
            "state_json": json.dumps({}, ensure_ascii=False),
        },
    )
    monkeypatch.setattr(
        store,
        "get",
        lambda task_id: {
            "id": task_id,
            "type": "link_check",
            "_user_id": 2,
            "status": "done",
            "link_url": "https://shop.example.com/de/products/demo?variant=123",
            "resolved_url": "https://shop.example.com/de/products/demo?variant=123",
            "page_language": "de",
            "target_language": "de",
            "target_language_name": "德语",
            "locale_evidence": {
                "target_language": "de",
                "requested_url": "https://shop.example.com/de/products/demo?variant=123",
                "lock_source": "warmup_attempt_2",
                "locked": True,
                "failure_reason": "",
                "attempts": [{"phase": "warmup", "attempt_index": 2, "wait_seconds_before_request": 2, "requested_url": "https://shop.example.com/de/products/demo?variant=123", "resolved_url": "https://shop.example.com/de/products/demo?variant=123", "page_language": "de", "locked": True}],
            },
            "progress": {},
            "summary": {"overall_decision": "done"},
            "reference_images": [],
            "items": [
                {
                    "id": "site-1",
                    "kind": "carousel",
                    "source_url": "https://img/site.jpg",
                    "_local_path": "C:/tmp/site.jpg",
                    "download_evidence": {
                        "requested_source_url": "https://img/site.jpg",
                        "resolved_source_url": "https://img/site.jpg",
                        "redirect_preserved_asset": True,
                        "variant_selected": True,
                        "evidence_status": "ok",
                        "evidence_reason": "",
                    },
                    "analysis": {"decision": "pass"},
                    "reference_match": {"status": "not_provided"},
                    "binary_quick_check": {"status": "skipped"},
                    "same_image_llm": {"status": "skipped"},
                    "status": "done",
                    "error": "",
                }
            ],
        },
    )

    payload = authed_user_client_no_db.get("/api/link-check/tasks/lc-1").get_json()

    assert payload["locale_evidence"]["lock_source"] == "warmup_attempt_2"
    assert payload["items"][0]["download_evidence"]["variant_selected"] is True
```

- [ ] **Step 2: Run the route tests to verify they fail**

Run: `pytest tests/test_link_check_routes.py -q`

Expected: FAIL because the route does not yet include the new evidence keys.

- [ ] **Step 3: Serialize locale evidence and download evidence**

Update `_load_task_from_row()` in `web/routes/link_check.py`:

```python
state.setdefault("locale_evidence", {
    "target_language": state.get("target_language", ""),
    "requested_url": state.get("link_url", ""),
    "lock_source": "",
    "locked": False,
    "failure_reason": "",
    "attempts": [],
})
```

Update `_serialize_task()`:

```python
"locale_evidence": dict(task.get("locale_evidence") or {}),
```

and inside each serialized item:

```python
"download_evidence": dict(item.get("download_evidence") or {}),
```

- [ ] **Step 4: Re-run the route tests**

Run: `pytest tests/test_link_check_routes.py -q`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add web/routes/link_check.py tests/test_link_check_routes.py
git commit -m "feat: expose link check evidence in api"
```

---

### Task 4: Detail Page Evidence Rendering

**Files:**
- Modify: `web/static/link_check.js`
- Modify: `web/static/link_check.css`
- Test: `tests/test_link_check_ui_assets.py`

- [ ] **Step 1: Write the failing UI asset tests**

Extend `tests/test_link_check_ui_assets.py`:

```python
def test_link_check_detail_script_renders_locale_evidence_and_download_evidence():
    script = Path("web/static/link_check.js").read_text(encoding="utf-8")
    style = Path("web/static/link_check.css").read_text(encoding="utf-8")

    assert "function renderLocaleEvidence" in script
    assert "function renderDownloadEvidence" in script
    assert "locale_evidence" in script
    assert "download_evidence" in script
    assert ".lc-evidence-grid" in style
    assert ".lc-attempt-table" in style
```

Add to the existing Node harness assertions:

```python
assert "warmup_attempt_2" in rendered["summaryHtml"]
assert "requested_source_url" not in rendered["resultsHtml"]
assert "最终下载 URL" in rendered["resultsHtml"]
assert "是否保持同一资源" in rendered["resultsHtml"]
```

Use this task payload inside the harness:

```python
"locale_evidence": {
    "target_language": "de",
    "requested_url": "https://shop.example.com/de/products/demo?variant=123",
    "lock_source": "warmup_attempt_2",
    "locked": True,
    "failure_reason": "",
    "attempts": [
        {"phase": "initial", "attempt_index": 1, "wait_seconds_before_request": 0, "requested_url": "https://shop.example.com/de/products/demo?variant=123", "resolved_url": "https://shop.example.com/products/demo?variant=123", "page_language": "en", "locked": False},
        {"phase": "warmup", "attempt_index": 2, "wait_seconds_before_request": 2, "requested_url": "https://shop.example.com/de/products/demo?variant=123", "resolved_url": "https://shop.example.com/de/products/demo?variant=123", "page_language": "de", "locked": True},
    ],
},
```

and item payload:

```python
"download_evidence": {
    "requested_source_url": "https://img/site.jpg",
    "resolved_source_url": "https://img/site.jpg",
    "redirect_preserved_asset": True,
    "variant_selected": True,
    "evidence_status": "ok",
    "evidence_reason": "",
},
```

- [ ] **Step 2: Run the UI asset tests to verify they fail**

Run: `pytest tests/test_link_check_ui_assets.py -q`

Expected: FAIL because the detail page script does not yet render either evidence block.

- [ ] **Step 3: Render locale evidence in the detail summary**

Add these helpers in `web/static/link_check.js`:

```javascript
function renderLocaleAttemptRow(attempt) {
  return `
    <tr>
      <td>${escapeHtml(String(attempt.attempt_index || "-"))}</td>
      <td>${escapeHtml(formatValue(attempt.phase))}</td>
      <td>${escapeHtml(formatValue(attempt.wait_seconds_before_request))}</td>
      <td class="lc-mono lc-clamp-2">${escapeHtml(formatValue(attempt.requested_url))}</td>
      <td class="lc-mono lc-clamp-2">${escapeHtml(formatValue(attempt.resolved_url))}</td>
      <td>${escapeHtml(formatValue(attempt.page_language))}</td>
      <td>${attempt.locked ? badge("已锁定", "is-success") : badge("未锁定", "is-warning")}</td>
    </tr>
  `;
}

function renderLocaleEvidence(task) {
  const evidence = task.locale_evidence || {};
  const attempts = evidence.attempts || [];
  return `
    <section class="lc-evidence-block">
      <div class="lc-panel-head">
        <span class="lc-kicker">Locale Evidence</span>
        <h3>页面锁定证据</h3>
        <p>只有锁定到目标页面后才允许下载图片。</p>
      </div>
      <div class="lc-evidence-grid">
        ${summaryCard("锁定来源", evidence.lock_source || "-")}
        ${summaryCard("目标语种", evidence.target_language || task.target_language || "-")}
        ${summaryCard("是否锁定", evidence.locked ? "是" : "否")}
        ${summaryCard("失败原因", evidence.failure_reason || "-")}
      </div>
      <div class="lc-attempt-table-wrap">
        <table class="lc-attempt-table">
          <thead>
            <tr>
              <th>尝试</th><th>阶段</th><th>等待秒数</th><th>请求 URL</th><th>最终 URL</th><th>页面语言</th><th>结果</th>
            </tr>
          </thead>
          <tbody>
            ${attempts.map(renderLocaleAttemptRow).join("")}
          </tbody>
        </table>
      </div>
    </section>
  `;
}
```

Inside `renderSummary(task)`, append:

```javascript
${renderLocaleEvidence(task)}
```

- [ ] **Step 4: Render image download evidence per result card**

Add to `web/static/link_check.js`:

```javascript
function renderDownloadEvidence(item) {
  const evidence = item.download_evidence || {};
  return [
    { label: "原始图片 URL", value: evidence.requested_source_url || "-", mono: true },
    { label: "最终下载 URL", value: evidence.resolved_source_url || "-", mono: true },
    {
      label: "是否保持同一资源",
      value: evidence.redirect_preserved_asset ? "是" : "否",
      isAlert: evidence.redirect_preserved_asset === false,
    },
    {
      label: "是否来自当前 Variant",
      value: evidence.variant_selected ? "是" : "否",
    },
    {
      label: "下载证据状态",
      value: evidence.evidence_status || "-",
      isAlert: evidence.evidence_status === "mismatch",
    },
    { label: "下载证据说明", value: evidence.evidence_reason || "-" },
  ];
}
```

In `getItemMetaEntries(item, task)`, append:

```javascript
...renderDownloadEvidence(item),
```

- [ ] **Step 5: Style the evidence blocks**

Append these rules to `web/static/link_check.css`:

```css
.lc-evidence-block {
  margin-top: var(--space-6);
  border-top: 1px solid var(--border);
  padding-top: var(--space-6);
}

.lc-evidence-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--space-4);
}

.lc-attempt-table-wrap {
  margin-top: var(--space-4);
  overflow-x: auto;
}

.lc-attempt-table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--text-sm);
}

.lc-attempt-table th,
.lc-attempt-table td {
  border-bottom: 1px solid var(--border);
  padding: var(--space-3);
  text-align: left;
  vertical-align: top;
}

@media (max-width: 768px) {
  .lc-evidence-grid {
    grid-template-columns: 1fr;
  }
}
```

- [ ] **Step 6: Re-run the UI asset tests**

Run: `pytest tests/test_link_check_ui_assets.py -q`

Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add web/static/link_check.js web/static/link_check.css tests/test_link_check_ui_assets.py
git commit -m "feat: show link check locale evidence in detail view"
```

---

### Task 5: Full Verification

**Files:**
- Modify: none
- Test: `tests/test_link_check_fetcher.py`
- Test: `tests/test_link_check_runtime.py`
- Test: `tests/test_link_check_routes.py`
- Test: `tests/test_link_check_ui_assets.py`

- [ ] **Step 1: Run focused link-check verification**

Run:

```bash
pytest tests/test_link_check_fetcher.py tests/test_link_check_runtime.py tests/test_link_check_routes.py tests/test_link_check_ui_assets.py -q
```

Expected: all selected tests PASS.

- [ ] **Step 2: Run targeted route regression**

Run:

```bash
pytest tests/test_medias_link_check_routes.py tests/test_web_routes.py -k "link_check" -q
```

Expected: PASS, proving that the new evidence keys do not break existing link-check surfaces.

- [ ] **Step 3: Run syntax verification**

Run:

```bash
python -m py_compile appcore/link_check_fetcher.py appcore/link_check_runtime.py appcore/task_state.py web/routes/link_check.py
```

Expected: no output, exit code 0.

- [ ] **Step 4: Commit final verification-only checkpoint**

```bash
git add docs/superpowers/specs/2026-04-20-link-check-locale-lock-evidence-design.md docs/superpowers/plans/2026-04-20-link-check-locale-lock-evidence.md
git commit -m "docs: add link check locale lock implementation plan"
```

---

## Spec Coverage Check

- Shopify warm-up retries with 2-second gaps: Task 1
- Download only after confirmed target page: Tasks 1 and 2
- Task-level `locale_evidence`: Tasks 1, 2, and 3
- Item-level `download_evidence`: Tasks 1, 2, and 3
- Detail page visibility: Task 4
- Regression and syntax verification: Task 5

