# Link Check Binary Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the existing `link-check` module so matched reference-image pairs use binary quick-check for final pass/fail, expose the exact quick-check metrics in the UI, and add a same-image Gemini judgment through the image-translation channel chain.

**Architecture:** Keep the current locale-lock + Shopify image fetch + optional reference-match pipeline, but split the post-match path into two branches. `matched` pairs go through deterministic binary quick-check plus a display-only same-image LLM check; unmatched pairs continue to use the existing single-image language/quality Gemini analysis. Runtime remains in-memory and non-persistent, with the page polling the task status API.

**Tech Stack:** Flask, Jinja, vanilla JS, Pillow, NumPy, scikit-image, ImageHash, existing `appcore.gemini`, existing `appcore.image_translate_settings`, pytest.

---

## File Structure

**Create**
- `appcore/link_check_same_image.py`
  - Reuse `image_translate.channel` routing and call Gemini 3.1 Flash-Lite Preview for yes/no same-image judgment.
- `tests/test_link_check_same_image.py`
  - Verify channel routing, prompt/output parsing, and failure fallback for same-image LLM checks.

**Modify**
- `appcore/link_check_compare.py`
  - Keep current reference matching and add binary quick-check helpers plus structured metric output.
- `appcore/link_check_runtime.py`
  - Route `matched` pairs through binary quick-check + same-image LLM, and keep the original Gemini path for unmatched images.
- `appcore/link_check_gemini.py`
  - Keep the current single-image analysis contract, but normalize the returned shape so runtime can merge binary/LLM shortcuts cleanly.
- `appcore/task_state.py`
  - Expand link-check task progress and summary counters for binary checks and same-image LLM results.
- `web/routes/link_check.py`
  - Serialize `binary_quick_check` and `same_image_llm` in API payloads.
- `web/static/link_check.js`
  - Render quick-check metrics, same-image LLM result, and the final decision source on each card.
- `web/templates/link_check.html`
  - Add result shell copy where needed for the new metrics.
- `tests/test_link_check_compare.py`
  - Cover binary quick-check pass/fail/error scenarios and exact metric fields.
- `tests/test_link_check_runtime.py`
  - Cover the new matched/unmatched execution branches and summary counters.
- `tests/test_link_check_routes.py`
  - Assert the API exposes quick-check and same-image LLM payloads.

## Task 1: Add Binary Quick-Check to the Comparison Layer

**Files:**
- Modify: `appcore/link_check_compare.py`
- Modify: `tests/test_link_check_compare.py`

- [ ] **Step 1: Write the failing binary quick-check tests**

```python
def test_binary_quick_check_passes_same_layout_after_resize(tmp_path):
    from appcore.link_check_compare import run_binary_quick_check

    left = _make_multiline_text_sample(
        tmp_path / "left.jpg",
        text="GERMAN TEXT LARGE BLOCK\nSECOND LINE COPY\nTHIRD LINE HERE",
    )
    right = _make_multiline_text_sample(
        tmp_path / "right.jpg",
        text="GERMAN TEXT LARGE BLOCK\nSECOND LINE COPY\nTHIRD LINE HERE",
    ).with_name("right_resized.jpg")
    Image.open(tmp_path / "right.jpg").resize((900, 600)).save(right, quality=60)

    result = run_binary_quick_check(left, right)

    assert result["status"] == "pass"
    assert result["binary_similarity"] >= 0.90
    assert result["foreground_overlap"] >= 0.85
    assert result["threshold"] == 0.90


def test_binary_quick_check_fails_when_text_changes(tmp_path):
    from appcore.link_check_compare import run_binary_quick_check

    left = _make_multiline_text_sample(
        tmp_path / "left.jpg",
        text="GERMAN TEXT LARGE BLOCK\nSECOND LINE COPY\nTHIRD LINE HERE",
    )
    right = _make_multiline_text_sample(
        tmp_path / "right.jpg",
        text="ENGLISH HEADLINE CHANGED\nNEW SECOND LINE TEXT\nTOTALLY DIFFERENT WORDS",
    )

    result = run_binary_quick_check(left, right)

    assert result["status"] == "fail"
    assert result["binary_similarity"] < 0.90


def test_binary_quick_check_reports_error_for_broken_input(tmp_path):
    from appcore.link_check_compare import run_binary_quick_check

    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"not-an-image")
    valid = _make_sample(tmp_path / "valid.jpg", size=(1200, 800))

    result = run_binary_quick_check(broken, valid)

    assert result["status"] == "error"
    assert "失败" in result["reason"]
```

- [ ] **Step 2: Run the focused test file and confirm the new helper is missing**

Run: `pytest tests/test_link_check_compare.py -q`

Expected: `FAIL` because `run_binary_quick_check` does not exist yet.

- [ ] **Step 3: Implement the in-memory `100x100` binary quick-check**

```python
_BINARY_SIZE = 100
_BINARY_THRESHOLD = 0.90


def _prepare_binary_image(path: str | Path) -> np.ndarray:
    image = Image.open(path)
    image = ImageOps.exif_transpose(image).convert("RGB")
    image.thumbnail((_BINARY_SIZE, _BINARY_SIZE), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (_BINARY_SIZE, _BINARY_SIZE), "white")
    offset = ((_BINARY_SIZE - image.width) // 2, (_BINARY_SIZE - image.height) // 2)
    canvas.paste(image, offset)
    gray = np.asarray(canvas.convert("L"), dtype=np.uint8)
    threshold = filters.threshold_local(gray, 21, offset=8)
    return (gray <= threshold).astype(np.uint8)


def run_binary_quick_check(candidate_path: str | Path, reference_path: str | Path) -> dict:
    try:
        candidate = _prepare_binary_image(candidate_path)
        reference = _prepare_binary_image(reference_path)
    except Exception as exc:
        return {
            "status": "error",
            "binary_similarity": 0.0,
            "foreground_overlap": 0.0,
            "threshold": _BINARY_THRESHOLD,
            "reason": f"二值快检执行失败：{exc}",
        }

    identical = float(np.mean(candidate == reference))
    foreground_union = (candidate == 1) | (reference == 1)
    if np.any(foreground_union):
        overlap = float(np.sum((candidate == 1) & (reference == 1)) / np.sum(foreground_union))
    else:
        overlap = 1.0
    status = "pass" if identical >= _BINARY_THRESHOLD else "fail"
    return {
        "status": status,
        "binary_similarity": round(identical, 4),
        "foreground_overlap": round(overlap, 4),
        "threshold": _BINARY_THRESHOLD,
        "reason": (
            "参考图已匹配，二值相似度达到阈值，直接通过"
            if status == "pass"
            else "参考图已匹配，但二值相似度低于阈值，判定需要替换"
        ),
    }
```

- [ ] **Step 4: Re-run the comparison tests and confirm they pass**

Run: `pytest tests/test_link_check_compare.py -q`

Expected: all tests `PASS`, including the existing deterministic match tests.

- [ ] **Step 5: Commit the comparison-layer upgrade**

```bash
git add appcore/link_check_compare.py tests/test_link_check_compare.py
git commit -m "feat: add binary quick check for link check"
```

## Task 2: Add Same-Image Gemini Routing Through Image Translation Channels

**Files:**
- Create: `appcore/link_check_same_image.py`
- Modify: `appcore/gemini_image.py`
- Modify: `tests/test_gemini_image.py`
- Create: `tests/test_link_check_same_image.py`

- [ ] **Step 1: Write the failing same-image LLM tests**

```python
def test_same_image_judgment_uses_image_translate_channel(monkeypatch, tmp_path):
    from appcore import link_check_same_image as module

    site = tmp_path / "site.jpg"
    ref = tmp_path / "ref.jpg"
    site.write_bytes(b"site")
    ref.write_bytes(b"ref")

    monkeypatch.setattr(module, "_resolve_channel", lambda: "cloud")
    monkeypatch.setattr(
        module,
        "_call_same_image_model",
        lambda **kwargs: {"status": "done", "answer": "是", "model": kwargs["model"], "channel": kwargs["channel"]},
    )

    result = module.judge_same_image(site, ref)

    assert result["status"] == "done"
    assert result["answer"] == "是"
    assert result["channel"] == "cloud"
    assert result["model"] == "gemini-3.1-flash-lite-preview"


def test_same_image_judgment_returns_error_without_crashing(monkeypatch, tmp_path):
    from appcore import link_check_same_image as module

    site = tmp_path / "site.jpg"
    ref = tmp_path / "ref.jpg"
    site.write_bytes(b"site")
    ref.write_bytes(b"ref")

    monkeypatch.setattr(module, "_resolve_channel", lambda: "openrouter")
    monkeypatch.setattr(module, "_call_same_image_model", side_effect=RuntimeError("provider down"))

    result = module.judge_same_image(site, ref)

    assert result["status"] == "error"
    assert result["answer"] == ""
    assert "provider down" in result["reason"]
```

- [ ] **Step 2: Run the same-image tests and confirm the new module is missing**

Run: `pytest tests/test_link_check_same_image.py -q`

Expected: `FAIL` with `ModuleNotFoundError`.

- [ ] **Step 3: Add a reusable two-image Gemini helper and the same-image module**

```python
# appcore/gemini_image.py
def analyze_images(
    prompt: str,
    *,
    media_paths: list[str | Path],
    model: str,
    service: str,
) -> dict:
    channel = _resolve_channel()
    # Reuse existing channel selection and return {"text": "...", "channel": channel, "model": resolved_model}
```

```python
# appcore/link_check_same_image.py
_AISTUDIO_MODEL = "gemini-3.1-flash-lite-preview"
_CLOUD_MODEL = "gemini-3.1-flash-lite-preview"
_OPENROUTER_MODEL = "google/gemini-3.1-flash-lite-preview"


def _build_prompt() -> str:
    return (
        "你会收到两张图片：第一张是网站抓取图，第二张是参考图。"
        "忽略尺寸差异、压缩差异、导出格式差异，只判断视觉上它们是否属于同一张基础图片。"
        "不要做语言质量分析，不要解释原因，只返回“是”或“不是”。"
    )


def judge_same_image(site_path: str | Path, reference_path: str | Path) -> dict:
    channel = _resolve_channel()
    model = _OPENROUTER_MODEL if channel == "openrouter" else _CLOUD_MODEL if channel == "cloud" else _AISTUDIO_MODEL
    try:
        raw = analyze_images(
            _build_prompt(),
            media_paths=[site_path, reference_path],
            model=model,
            service="link_check_same_image",
        )
    except Exception as exc:
        return {
            "status": "error",
            "answer": "",
            "channel": channel,
            "channel_label": CHANNEL_LABELS.get(channel, channel),
            "model": model,
            "reason": str(exc),
        }

    answer = "是" if "是" in (raw.get("text") or "") else "不是"
    return {
        "status": "done",
        "answer": answer,
        "channel": channel,
        "channel_label": CHANNEL_LABELS.get(channel, channel),
        "model": model,
        "reason": "",
    }
```

- [ ] **Step 4: Re-run the same-image and Gemini image tests**

Run: `pytest tests/test_link_check_same_image.py tests/test_gemini_image.py -q`

Expected: all targeted tests `PASS`.

- [ ] **Step 5: Commit the same-image LLM layer**

```bash
git add appcore/gemini_image.py appcore/link_check_same_image.py tests/test_gemini_image.py tests/test_link_check_same_image.py
git commit -m "feat: add same image llm judgment for link check"
```

## Task 3: Rewire Runtime for Matched vs. Unmatched Branches

**Files:**
- Modify: `appcore/link_check_runtime.py`
- Modify: `appcore/link_check_gemini.py`
- Modify: `appcore/task_state.py`
- Modify: `tests/test_link_check_runtime.py`

- [ ] **Step 1: Write the failing runtime tests for the new branches**

```python
def test_runtime_uses_binary_pass_for_matched_reference(monkeypatch):
    from appcore.link_check_runtime import LinkCheckRuntime

    # setup task omitted for brevity
    monkeypatch.setattr("appcore.link_check_runtime.find_best_reference", lambda *a, **k: {
        "status": "matched",
        "score": 0.93,
        "reference_path": str(ref_path),
    })
    monkeypatch.setattr("appcore.link_check_runtime.run_binary_quick_check", lambda *a, **k: {
        "status": "pass",
        "binary_similarity": 0.94,
        "foreground_overlap": 0.90,
        "threshold": 0.90,
        "reason": "ok",
    })
    monkeypatch.setattr("appcore.link_check_runtime.judge_same_image", lambda *a, **k: {
        "status": "done",
        "answer": "是",
        "channel": "cloud",
        "channel_label": "Google Cloud (Vertex AI)",
        "model": "gemini-3.1-flash-lite-preview",
        "reason": "",
    })
    analyze = monkeypatch.patch("appcore.link_check_runtime.analyze_image")

    LinkCheckRuntime(fetcher=DummyFetcher()).start("lc-binary-pass")

    saved = task_state.get("lc-binary-pass")
    assert saved["items"][0]["analysis"]["decision"] == "pass"
    assert saved["items"][0]["analysis"]["decision_source"] == "binary_quick_check"
    assert saved["items"][0]["same_image_llm"]["answer"] == "是"
    analyze.assert_not_called()


def test_runtime_falls_back_to_language_gemini_for_unmatched_reference(monkeypatch):
    monkeypatch.setattr("appcore.link_check_runtime.find_best_reference", lambda *a, **k: {
        "status": "not_matched",
        "score": 0.42,
        "reference_path": "",
    })
    monkeypatch.setattr("appcore.link_check_runtime.analyze_image", lambda *a, **k: {
        "decision": "replace",
        "has_text": True,
        "detected_language": "en",
        "language_match": False,
        "text_summary": "English text",
        "quality_score": 15,
        "quality_reason": "wrong language",
        "needs_replacement": True,
    })

    LinkCheckRuntime(fetcher=DummyFetcher()).start("lc-unmatched")

    saved = task_state.get("lc-unmatched")
    assert saved["items"][0]["binary_quick_check"]["status"] == "skipped"
    assert saved["items"][0]["same_image_llm"]["status"] == "skipped"
    assert saved["items"][0]["analysis"]["decision_source"] == "gemini_language_check"
```

- [ ] **Step 2: Run the runtime tests and confirm they fail on missing fields/logic**

Run: `pytest tests/test_link_check_runtime.py -q`

Expected: `FAIL` because runtime does not populate `binary_quick_check`, `same_image_llm`, or `decision_source`.

- [ ] **Step 3: Update task defaults, runtime branching, and normalized analysis payloads**

```python
# appcore/task_state.py
"progress": {
    "total": 0,
    "downloaded": 0,
    "analyzed": 0,
    "compared": 0,
    "binary_checked": 0,
    "same_image_llm_done": 0,
    "failed": 0,
},
"summary": {
    "pass_count": 0,
    "no_text_count": 0,
    "replace_count": 0,
    "review_count": 0,
    "reference_unmatched_count": 0,
    "reference_matched_count": 0,
    "binary_checked_count": 0,
    "binary_direct_pass_count": 0,
    "binary_direct_replace_count": 0,
    "same_image_llm_done_count": 0,
    "same_image_llm_yes_count": 0,
    "overall_decision": "running",
}
```

```python
# appcore/link_check_runtime.py
if reference_match["status"] == "matched":
    result["binary_quick_check"] = run_binary_quick_check(item["local_path"], reference_path)
    task["progress"]["binary_checked"] += 1
    result["same_image_llm"] = judge_same_image(item["local_path"], reference_path)
    if result["same_image_llm"]["status"] == "done":
        task["progress"]["same_image_llm_done"] += 1
    if result["binary_quick_check"]["status"] == "pass":
        result["analysis"] = {
            "decision": "pass",
            "decision_source": "binary_quick_check",
            "quality_reason": "参考图已匹配且二值快检通过，跳过语言模型",
            "needs_replacement": False,
        }
    elif result["binary_quick_check"]["status"] == "fail":
        result["analysis"] = {
            "decision": "replace",
            "decision_source": "binary_quick_check",
            "quality_reason": "参考图已匹配但二值快检未通过，判定需要替换",
            "needs_replacement": True,
        }
    else:
        result["analysis"] = analyze_image(...)
        result["analysis"]["decision_source"] = "gemini_language_check"
else:
    result["binary_quick_check"] = {"status": "skipped", "binary_similarity": 0.0, "foreground_overlap": 0.0, "threshold": 0.90, "reason": "未匹配到参考图，跳过二值快检"}
    result["same_image_llm"] = {"status": "skipped", "answer": "", "channel": "", "channel_label": "", "model": "", "reason": "未匹配到参考图，跳过同图判断"}
    result["analysis"] = analyze_image(...)
    result["analysis"]["decision_source"] = "gemini_language_check"
```

- [ ] **Step 4: Re-run the runtime tests and confirm they pass**

Run: `pytest tests/test_link_check_runtime.py -q`

Expected: all runtime tests `PASS`.

- [ ] **Step 5: Commit the runtime orchestration changes**

```bash
git add appcore/link_check_runtime.py appcore/link_check_gemini.py appcore/task_state.py tests/test_link_check_runtime.py
git commit -m "feat: route matched link check pairs through binary review"
```

## Task 4: Expose the New Fields Through Routes and the UI

**Files:**
- Modify: `web/routes/link_check.py`
- Modify: `web/static/link_check.js`
- Modify: `web/templates/link_check.html`
- Modify: `tests/test_link_check_routes.py`

- [ ] **Step 1: Write the failing API/UI assertions**

```python
def test_get_task_serializes_binary_and_same_image_sections(authed_user_client_no_db, monkeypatch):
    monkeypatch.setattr(
        store,
        "get",
        lambda task_id: {
            "id": task_id,
            "type": "link_check",
            "_user_id": 2,
            "status": "done",
            "link_url": "https://shop.example.com/de/products/demo",
            "target_language": "de",
            "target_language_name": "德语",
            "progress": {"total": 1, "downloaded": 1, "analyzed": 1, "compared": 1, "binary_checked": 1, "same_image_llm_done": 1, "failed": 0},
            "summary": {"overall_decision": "done"},
            "reference_images": [{"id": "ref-1", "filename": "ref.jpg", "local_path": "C:/tmp/ref.jpg"}],
            "items": [{
                "id": "site-1",
                "kind": "carousel",
                "source_url": "https://img/site.jpg",
                "_local_path": "C:/tmp/site.jpg",
                "analysis": {"decision": "pass", "decision_source": "binary_quick_check"},
                "reference_match": {"status": "matched", "score": 0.9, "reference_id": "ref-1"},
                "binary_quick_check": {"status": "pass", "binary_similarity": 0.93, "foreground_overlap": 0.89, "threshold": 0.90, "reason": "ok"},
                "same_image_llm": {"status": "done", "answer": "是", "channel": "cloud", "channel_label": "Google Cloud (Vertex AI)", "model": "gemini-3.1-flash-lite-preview", "reason": ""},
                "status": "done",
                "error": "",
            }],
        },
    )

    payload = authed_user_client_no_db.get("/api/link-check/tasks/lc-1").get_json()

    assert payload["items"][0]["binary_quick_check"]["binary_similarity"] == 0.93
    assert payload["items"][0]["same_image_llm"]["answer"] == "是"
```

- [ ] **Step 2: Run the route test file and confirm the new assertions fail**

Run: `pytest tests/test_link_check_routes.py -q`

Expected: `FAIL` because the serializer and frontend do not expose the new sections yet.

- [ ] **Step 3: Serialize and render quick-check + same-image data**

```python
# web/routes/link_check.py
"items": [
    {
        "id": item["id"],
        "kind": item["kind"],
        "source_url": item["source_url"],
        "site_preview_url": f"/api/link-check/tasks/{task_id}/images/site/{item['id']}",
        "analysis": dict(item.get("analysis") or {}),
        "reference_match": dict(item.get("reference_match") or {}),
        "binary_quick_check": dict(item.get("binary_quick_check") or {}),
        "same_image_llm": dict(item.get("same_image_llm") or {}),
        "status": item.get("status") or "pending",
        "error": item.get("error") or "",
    }
    for item in task.get("items", [])
]
```

```javascript
// web/static/link_check.js
const binary = item.binary_quick_check || {};
const sameImage = item.same_image_llm || {};

<div class="lc-meta-row"><strong>二值快检结果</strong><span>${binary.status || "-"}</span></div>
<div class="lc-meta-row"><strong>二值相似度</strong><span>${formatPercent(binary.binary_similarity)}</span></div>
<div class="lc-meta-row"><strong>前景重合度</strong><span>${formatPercent(binary.foreground_overlap)}</span></div>
<div class="lc-meta-row"><strong>当前阈值</strong><span>${formatPercent(binary.threshold)}</span></div>
<div class="lc-meta-row"><strong>二值快检说明</strong><span>${binary.reason || "-"}</span></div>
<div class="lc-meta-row"><strong>大模型相同图片判断</strong><span>${sameImage.answer || sameImage.status || "-"}</span></div>
<div class="lc-meta-row"><strong>大模型判断通道</strong><span>${sameImage.channel_label || "-"}</span></div>
<div class="lc-meta-row"><strong>大模型判断模型</strong><span>${sameImage.model || "-"}</span></div>
<div class="lc-meta-row"><strong>最终判定来源</strong><span>${analysis.decision_source || "-"}</span></div>
```

- [ ] **Step 4: Re-run route tests and a focused UI smoke test**

Run: `pytest tests/test_link_check_routes.py -q`

Expected: all route tests `PASS`.

- [ ] **Step 5: Commit the route/UI payload changes**

```bash
git add web/routes/link_check.py web/static/link_check.js web/templates/link_check.html tests/test_link_check_routes.py
git commit -m "feat: show binary review and same image llm in link check ui"
```

## Task 5: End-to-End Verification for the Upgraded Flow

**Files:**
- Modify: `tests/test_link_check_runtime.py`
- Modify: `tests/test_link_check_routes.py`
- Modify: `tests/test_link_check_compare.py`
- Create: `tests/test_link_check_same_image.py`

- [ ] **Step 1: Run the full targeted suite**

Run: `pytest tests/test_link_check_compare.py tests/test_link_check_same_image.py tests/test_link_check_runtime.py tests/test_link_check_routes.py -q`

Expected: all tests `PASS`.

- [ ] **Step 2: Run a Python syntax check over the touched runtime and web files**

Run: `python -m py_compile appcore\link_check_compare.py appcore\link_check_same_image.py appcore\link_check_runtime.py web\routes\link_check.py web\static\link_check.js`

Expected: Python files compile cleanly. If `py_compile` is used, omit the JS file and instead rely on the browser-facing test plus manual inspection.

- [ ] **Step 3: Manually smoke-test the page contract with Flask test client**

Run:

```powershell
@'
from web.app import create_app
app = create_app()
print(app.url_map)
'@ | python -
```

Expected: output includes `/link-check`, `/api/link-check/tasks`, `/api/link-check/tasks/<task_id>`.

- [ ] **Step 4: Commit the verification pass**

```bash
git add tests/test_link_check_compare.py tests/test_link_check_same_image.py tests/test_link_check_runtime.py tests/test_link_check_routes.py
git commit -m "test: cover binary review link check flow"
```

## Self-Review

**Spec coverage**
- Optional reference images: covered by existing route flow, preserved in Tasks 3-4.
- Deterministic same-image matching remains first-layer screening: preserved in Task 1 and Task 3.
- `matched` pairs now use binary quick-check as final decision: covered by Tasks 1 and 3.
- Exact binary metrics exposed in UI: covered by Tasks 1 and 4.
- Same-image LLM uses `AI Studio / Vertex / OpenRouter` image-translation channel chain: covered by Task 2.
- Same-image LLM is display-only and not the final arbiter: enforced in Task 3.
- Unmatched or missing-reference images still use the original language/quality Gemini path: covered by Task 3.
- API and frontend surface the new result sections: covered by Task 4.

**Placeholder scan**
- No `TODO`, `TBD`, or “similar to previous task” placeholders remain.
- Each task lists exact files, concrete commands, and code scaffolding for the intended change.

**Type consistency**
- `reference_match.status` remains `matched | weak_match | not_matched | not_provided`.
- `binary_quick_check.status` is consistently `pass | fail | skipped | error`.
- `same_image_llm.status` is consistently `done | skipped | error`.
- `analysis.decision_source` is consistently `binary_quick_check` or `gemini_language_check`.

## Notes

- This upgrade intentionally does **not** make same-image LLM results part of the final business decision.
- This plan assumes inline execution in the isolated worktree unless the user explicitly requests delegated subagents.
