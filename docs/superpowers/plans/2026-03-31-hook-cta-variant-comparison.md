# Hook CTA Variant Comparison Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `normal` and `hook_cta` English variants that share the Chinese upstream but independently produce translation, TTS, subtitles, videos, and CapCut exports, with side-by-side comparison in the web UI.

**Architecture:** Keep the Chinese pipeline single-path through ASR, alignment, and `source_full_text_zh`, then fork into `variants.normal` and `variants.hook_cta` at localized translation. Each variant carries its own text artifacts, media artifacts, timeline manifest, and export outputs, while the UI renders a generic `variant_compare` artifact layout for the English-facing steps.

**Tech Stack:** Flask, Flask-SocketIO, OpenRouter via OpenAI SDK, ElevenLabs, Volcengine ASR, pytest, vanilla JavaScript

---

## File Structure

- Modify: `pipeline/localization.py`
  Variant-specific prompts, validation helpers, and variant metadata.
- Modify: `pipeline/translate.py`
  Variant-aware localized translation and TTS script generation.
- Modify: `pipeline/tts.py`
  Variant-specific output naming and metadata persistence.
- Modify: `pipeline/timeline.py`
  Variant-specific manifest generation and naming.
- Modify: `pipeline/compose.py`
  Variant-specific soft/hard output handling.
- Modify: `pipeline/capcut.py`
  Variant-specific draft/export naming and manifest content.
- Modify: `web/store.py`
  Add `variants.normal` and `variants.hook_cta` state scaffolding.
- Modify: `web/services/pipeline_runner.py`
  Orchestrate the shared Chinese upstream and variant fan-out.
- Modify: `web/preview_artifacts.py`
  Add `variant_compare` artifact payload builders.
- Modify: `web/routes/task.py`
  Support variant-scoped artifact lookup and downloads.
- Modify: `web/templates/index.html`
  Render compare columns for translation, TTS, subtitle, compose, and export.
- Modify: `tests/test_localization.py`
  Add prompt and validation tests for the new variant rules.
- Modify: `tests/test_pipeline_runner.py`
  Verify variant fan-out orchestration and partial-failure behavior.
- Modify: `tests/test_preview_artifacts.py`
  Verify compare-layout artifact payloads.
- Modify: `tests/test_web_routes.py`
  Verify task payload and download behavior for variant outputs.
- Modify: `tests/test_capcut_export.py`
  Verify variant-specific export naming and coexistence.
- Modify: `tests/test_timeline.py`
  Verify per-variant manifests do not overwrite each other.

## Task 1: Add variant-aware localization prompts and state schema

**Files:**
- Modify: `pipeline/localization.py`
- Modify: `web/store.py`
- Modify: `tests/test_localization.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_hook_cta_prompt_mentions_first_three_seconds_and_single_cta():
    from pipeline.localization import build_localized_translation_messages

    messages = build_localized_translation_messages(
        source_full_text_zh="测试中文",
        script_segments=[{"index": 0, "text": "测试中文"}],
        variant="hook_cta",
    )

    system_prompt = messages[0]["content"]
    assert "first 3 spoken seconds" in system_prompt
    assert "exactly one clear purchase CTA" in system_prompt


def test_store_create_initializes_two_variants():
    from web import store

    task = store.create("task-variants", "video.mp4", "output/task-variants")

    assert set(task["variants"].keys()) == {"normal", "hook_cta"}
    assert task["variants"]["normal"]["label"] == "普通版"
    assert task["variants"]["hook_cta"]["label"] == "黄金3秒 + CTA版"
```

- [ ] **Step 2: Run the focused tests to verify they fail**

Run: `pytest tests/test_localization.py tests/test_web_routes.py -k "hook_cta_prompt or initializes_two_variants" -q`

Expected: FAIL because neither variant-specific prompt generation nor variant task scaffolding exists yet.

- [ ] **Step 3: Write the minimal implementation**

```python
# pipeline/localization.py
VARIANT_LABELS = {
    "normal": "普通版",
    "hook_cta": "黄金3秒 + CTA版",
}

HOOK_CTA_TRANSLATION_SYSTEM_PROMPT = """...
- Sentence 1 must function as the first-3-seconds hook.
- Treat the first 3 spoken seconds as roughly the first 7-10 English words.
- The full script must contain exactly one clear purchase CTA.
..."""

def build_localized_translation_messages(source_full_text_zh, script_segments, variant="normal"):
    prompt = HOOK_CTA_TRANSLATION_SYSTEM_PROMPT if variant == "hook_cta" else LOCALIZED_TRANSLATION_SYSTEM_PROMPT
    ...


# web/store.py
def _empty_variant_state(label: str) -> dict:
    return {
        "label": label,
        "localized_translation": {},
        "tts_script": {},
        "tts_result": {},
        "english_asr_result": {},
        "corrected_subtitle": {},
        "timeline_manifest": {},
        "result": {},
        "exports": {},
        "artifacts": {},
        "preview_files": {},
    }
```

- [ ] **Step 4: Run the focused tests to verify they pass**

Run: `pytest tests/test_localization.py tests/test_web_routes.py -k "hook_cta_prompt or initializes_two_variants" -q`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pipeline/localization.py web/store.py tests/test_localization.py tests/test_web_routes.py
git commit -m "feat: add hook cta variant scaffolding"
```

## Task 2: Fork translation and TTS generation by variant

**Files:**
- Modify: `pipeline/translate.py`
- Modify: `pipeline/localization.py`
- Modify: `tests/test_localization.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_generate_localized_translation_passes_variant_specific_prompt(monkeypatch):
    captured = {}

    def fake_create(**kwargs):
        captured["messages"] = kwargs["messages"]
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=types.SimpleNamespace(
                content='{"full_text":"Hook line.","sentences":[{"index":0,"text":"Hook line.","source_segment_indices":[0]}]}'
            ))]
        )

    monkeypatch.setattr("pipeline.translate.client.chat.completions.create", fake_create)

    generate_localized_translation("中文", [{"index": 0, "text": "中文"}], variant="hook_cta")

    assert "exactly one clear purchase CTA" in captured["messages"][0]["content"]
```

- [ ] **Step 2: Run the focused tests to verify they fail**

Run: `pytest tests/test_localization.py -k variant_specific_prompt -q`

Expected: FAIL because `generate_localized_translation(..., variant=...)` is not implemented.

- [ ] **Step 3: Write the minimal implementation**

```python
def generate_localized_translation(source_full_text_zh: str, script_segments: list[dict], variant: str = "normal") -> dict:
    response = client.chat.completions.create(
        model=_model_name(),
        messages=build_localized_translation_messages(source_full_text_zh, script_segments, variant=variant),
        ...
    )
    ...
```

- [ ] **Step 4: Run the focused tests to verify they pass**

Run: `pytest tests/test_localization.py -k variant_specific_prompt -q`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pipeline/translate.py pipeline/localization.py tests/test_localization.py
git commit -m "feat: add variant-aware translation prompts"
```

## Task 3: Orchestrate shared upstream plus variant fan-out

**Files:**
- Modify: `web/services/pipeline_runner.py`
- Modify: `web/store.py`
- Modify: `tests/test_pipeline_runner.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_pipeline_runner_populates_both_variants_after_translation(monkeypatch):
    ...
    assert task["variants"]["normal"]["localized_translation"]["full_text"] == "Normal copy"
    assert task["variants"]["hook_cta"]["localized_translation"]["full_text"] == "Hook CTA copy"
```

- [ ] **Step 2: Run the focused tests to verify they fail**

Run: `pytest tests/test_pipeline_runner.py -k both_variants_after_translation -q`

Expected: FAIL because the runner still writes single-path translation fields.

- [ ] **Step 3: Write the minimal implementation**

```python
for variant in ("normal", "hook_cta"):
    localized = generate_localized_translation(..., variant=variant)
    task["variants"][variant]["localized_translation"] = localized
    ...
    tts_script = generate_tts_script(localized)
    task["variants"][variant]["tts_script"] = tts_script
    ...
```

- [ ] **Step 4: Run the focused tests to verify they pass**

Run: `pytest tests/test_pipeline_runner.py -k both_variants_after_translation -q`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add web/services/pipeline_runner.py web/store.py tests/test_pipeline_runner.py
git commit -m "feat: fork english pipeline into two variants"
```

## Task 4: Persist variant-specific media, subtitle, video, and CapCut outputs

**Files:**
- Modify: `pipeline/tts.py`
- Modify: `pipeline/timeline.py`
- Modify: `pipeline/compose.py`
- Modify: `pipeline/capcut.py`
- Modify: `tests/test_capcut_export.py`
- Modify: `tests/test_timeline.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_capcut_export_names_archives_by_variant(tmp_path):
    export = export_capcut_project(..., variant="hook_cta")
    assert export["archive_path"].endswith("capcut_hook_cta.zip")


def test_timeline_manifest_can_be_saved_per_variant_without_name_collision():
    assert "timeline_manifest.normal.json" != "timeline_manifest.hook_cta.json"
```

- [ ] **Step 2: Run the focused tests to verify they fail**

Run: `pytest tests/test_capcut_export.py tests/test_timeline.py -k "variant or collision" -q`

Expected: FAIL because exports and manifests are still single-name outputs.

- [ ] **Step 3: Write the minimal implementation**

```python
def _variant_suffix(variant: str) -> str:
    return f".{variant}"

# examples
subtitle_path = os.path.join(task_dir, f"subtitle.{variant}.srt")
soft_video = os.path.join(task_dir, f"{task_id}_soft.{variant}.mp4")
hard_video = os.path.join(task_dir, f"{task_id}_hard.{variant}.mp4")
project_dir = Path(output_dir) / f"capcut_{variant}"
```

- [ ] **Step 4: Run the focused tests to verify they pass**

Run: `pytest tests/test_capcut_export.py tests/test_timeline.py -k "variant or collision" -q`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pipeline/tts.py pipeline/timeline.py pipeline/compose.py pipeline/capcut.py tests/test_capcut_export.py tests/test_timeline.py
git commit -m "feat: separate variant media and export outputs"
```

## Task 5: Add compare-layout artifacts and front-end rendering

**Files:**
- Modify: `web/preview_artifacts.py`
- Modify: `web/templates/index.html`
- Modify: `tests/test_preview_artifacts.py`
- Modify: `tests/test_web_routes.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_build_variant_compare_artifact_contains_two_named_columns():
    artifact = build_variant_compare_artifact(
        title="翻译本土化",
        variants={
            "normal": {"label": "普通版", "items": [{"type": "text", "label": "整段英文", "content": "A"}]},
            "hook_cta": {"label": "黄金3秒 + CTA版", "items": [{"type": "text", "label": "整段英文", "content": "B"}]},
        },
    )
    assert artifact["layout"] == "variant_compare"
    assert artifact["variants"]["hook_cta"]["label"] == "黄金3秒 + CTA版"
```

- [ ] **Step 2: Run the focused tests to verify they fail**

Run: `pytest tests/test_preview_artifacts.py tests/test_web_routes.py -k variant_compare -q`

Expected: FAIL because compare-layout artifacts and front-end support do not exist.

- [ ] **Step 3: Write the minimal implementation**

```javascript
if (artifact.layout === "variant_compare") {
  previewEl.innerHTML = renderVariantCompareArtifact(artifact);
}
```

```python
def build_variant_compare_artifact(title: str, variants: dict) -> dict:
    return {"title": title, "layout": "variant_compare", "variants": variants}
```

- [ ] **Step 4: Run the focused tests to verify they pass**

Run: `pytest tests/test_preview_artifacts.py tests/test_web_routes.py -k variant_compare -q`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add web/preview_artifacts.py web/templates/index.html tests/test_preview_artifacts.py tests/test_web_routes.py
git commit -m "feat: add variant compare previews"
```

## Task 6: Expose variant-specific downloads and artifact lookup

**Files:**
- Modify: `web/routes/task.py`
- Modify: `web/templates/index.html`
- Modify: `tests/test_web_routes.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_download_route_can_return_hook_cta_capcut_archive(...):
    response = client.get("/api/tasks/task-1/download/capcut?variant=hook_cta")
    assert response.status_code == 200
```

- [ ] **Step 2: Run the focused tests to verify they fail**

Run: `pytest tests/test_web_routes.py -k hook_cta_capcut_archive -q`

Expected: FAIL because downloads are not variant-aware.

- [ ] **Step 3: Write the minimal implementation**

```python
variant = request.args.get("variant", "normal")
variant_state = task.get("variants", {}).get(variant, {})
path_map = {
    "soft": variant_state.get("result", {}).get("soft_video"),
    "hard": variant_state.get("result", {}).get("hard_video"),
    "srt": variant_state.get("result", {}).get("srt"),
    "capcut": variant_state.get("exports", {}).get("capcut_archive"),
}
```

- [ ] **Step 4: Run the focused tests to verify they pass**

Run: `pytest tests/test_web_routes.py -k hook_cta_capcut_archive -q`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add web/routes/task.py web/templates/index.html tests/test_web_routes.py
git commit -m "feat: add variant-aware downloads"
```

## Task 7: Full verification and cleanup

**Files:**
- Modify: `readme_codex.md`
- Modify: any touched implementation/test files that still need naming or docs cleanup

- [ ] **Step 1: Update Codex docs for variant outputs**

```md
- `variants.normal`: baseline localized output
- `variants.hook_cta`: hook-first + CTA experiment output
- All English artifacts, videos, and CapCut exports are emitted per variant
```

- [ ] **Step 2: Run focused integration tests**

Run: `pytest tests/test_localization.py tests/test_pipeline_runner.py tests/test_preview_artifacts.py tests/test_web_routes.py tests/test_capcut_export.py tests/test_timeline.py -q`

Expected: PASS

- [ ] **Step 3: Run the full suite**

Run: `pytest tests -q`

Expected: PASS

- [ ] **Step 4: Run syntax verification**

Run: `python -m compileall -q pipeline web main.py config.py`

Expected: no output

- [ ] **Step 5: Commit**

```bash
git add readme_codex.md
git add pipeline web tests
git commit -m "feat: add hook cta variant comparison pipeline"
```

## Self-Review

- Spec coverage: this plan covers prompt rules, variant state, pipeline fan-out, output naming, compare UI, downloads, and verification.
- Placeholder scan: no `TODO` / `TBD` placeholders remain in task steps.
- Type consistency: uses `variants.normal` / `variants.hook_cta` consistently across state, artifacts, and downloads.
