# Multi-Translate Language Adapters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire existing German, French, and Japanese language-specific translation/TTS/subtitle logic into the multi-translate pipeline while leaving generic languages unchanged.

**Architecture:** Add a narrow adapter layer inside `appcore/runtime_multi.py` that resolves behavior by `target_lang`. German and French continue using the shared five-round TTS loop with language-specific localization modules and admin prompt resolver content; Japanese overrides translation, TTS, and subtitle steps to reuse `pipeline.ja_translate` character-budget logic.

**Tech Stack:** Python 3.12, Flask task state services, `pipeline.localization_*`, `pipeline.ja_translate`, pytest.

---

## File Structure

- Modify `appcore/runtime_multi.py`
  - Add adapter classes near `_PromptLocalizationAdapter`.
  - Route `_get_localization_module`, `_step_translate`, `_run_default_tts_loop`, and `_step_subtitle` through adapters.
  - Keep default behavior for languages without adapters.
- Modify `tests/test_runtime_multi_translate.py`
  - Add adapter-resolution and Japanese translation/TTS behavior tests.
- Modify `tests/test_runtime_multi_subtitle.py`
  - Add Japanese subtitle override test.
- Run existing tests that cover language registry, prompts, TTS duration, and multi-translate runtime.

## Task 1: Adapter Resolution

**Files:**
- Modify: `appcore/runtime_multi.py`
- Test: `tests/test_runtime_multi_translate.py`

- [ ] **Step 1: Write failing adapter resolution tests**

Add these tests to `tests/test_runtime_multi_translate.py`:

```python
def test_multi_resolves_dedicated_localization_modules_for_de_fr():
    runner = _make_runner()

    de_adapter = runner._get_language_adapter({"target_lang": "de"})
    fr_adapter = runner._get_language_adapter({"target_lang": "fr"})
    es_adapter = runner._get_language_adapter({"target_lang": "es"})

    assert de_adapter.__name__ == "pipeline.localization_de"
    assert fr_adapter.__name__ == "pipeline.localization_fr"
    assert es_adapter.__name__ == "multi_translate.localization.es"
    assert de_adapter.build_tts_segments is not None
    assert fr_adapter.build_tts_segments is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_runtime_multi_translate.py::test_multi_resolves_dedicated_localization_modules_for_de_fr -q`

Expected: FAIL because `_get_language_adapter` does not exist.

- [ ] **Step 3: Implement adapter classes and resolver**

In `appcore/runtime_multi.py`, extend the existing `_PromptLocalizationAdapter` area:

```python
class _ModuleLocalizationAdapter(_PromptLocalizationAdapter):
    def __init__(self, lang: str, module_name: str):
        super().__init__(lang)
        import importlib
        self.module = importlib.import_module(module_name)
        self.__name__ = self.module.__name__
        self.validate_tts_script = getattr(self.module, "validate_tts_script", validate_tts_script)
        self.build_tts_segments = getattr(self.module, "build_tts_segments", build_tts_segments)


class _JapaneseMultiTranslateAdapter(_PromptLocalizationAdapter):
    def __init__(self):
        super().__init__("ja")
        from pipeline import ja_translate
        self.module = ja_translate
        self.__name__ = "pipeline.ja_translate"
```

Add resolver methods to `MultiTranslateRunner`:

```python
def _get_language_adapter(self, task_or_lang):
    lang = (
        task_or_lang if isinstance(task_or_lang, str)
        else self._resolve_target_lang(task_or_lang)
    )
    if lang == "de":
        return _ModuleLocalizationAdapter("de", "pipeline.localization_de")
    if lang == "fr":
        return _ModuleLocalizationAdapter("fr", "pipeline.localization_fr")
    if lang == "ja":
        return _JapaneseMultiTranslateAdapter()
    return _PromptLocalizationAdapter(lang)

def _get_localization_module(self, task: dict):
    return self._get_language_adapter(task)
```

- [ ] **Step 4: Run adapter test to verify it passes**

Run: `python -m pytest tests/test_runtime_multi_translate.py::test_multi_resolves_dedicated_localization_modules_for_de_fr -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add appcore/runtime_multi.py tests/test_runtime_multi_translate.py
git commit -m "feat(multi): resolve language-specific adapters"
```

## Task 2: German/French Prompt Builder Integration

**Files:**
- Modify: `appcore/runtime_multi.py`
- Test: `tests/test_runtime_multi_translate.py`

- [ ] **Step 1: Write failing prompt resolver test**

Add:

```python
def test_de_fr_adapters_keep_admin_prompt_resolver(monkeypatch):
    calls = []

    def fake_resolve(slot, lang):
        calls.append((slot, lang))
        return {"content": f"{slot}:{lang}"}

    monkeypatch.setattr("appcore.runtime_multi.resolve_prompt_config", fake_resolve)

    runner = _make_runner()
    de_adapter = runner._get_language_adapter({"target_lang": "de"})
    fr_adapter = runner._get_language_adapter({"target_lang": "fr"})

    de_adapter.build_tts_script_messages({"full_text": "Hallo"})
    de_adapter.build_localized_rewrite_messages(
        "source", {"full_text": "Hallo"}, 10, "shrink", source_language="en",
    )
    fr_adapter.build_tts_script_messages({"full_text": "Bonjour"})
    fr_adapter.build_localized_rewrite_messages(
        "source", {"full_text": "Bonjour"}, 10, "expand", source_language="en",
    )

    assert ("base_tts_script", "de") in calls
    assert ("base_rewrite", "de") in calls
    assert ("base_tts_script", "fr") in calls
    assert ("base_rewrite", "fr") in calls
```

- [ ] **Step 2: Run test to verify it fails if adapter bypasses resolver**

Run: `python -m pytest tests/test_runtime_multi_translate.py::test_de_fr_adapters_keep_admin_prompt_resolver -q`

Expected: PASS after Task 1 if `_ModuleLocalizationAdapter` inherits resolver-based builders; FAIL if the module constants are used directly.

- [ ] **Step 3: Keep resolver-based builders**

No new code is needed if `_ModuleLocalizationAdapter` inherits `_PromptLocalizationAdapter.build_tts_script_messages` and `build_localized_rewrite_messages`.

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_runtime_multi_translate.py::test_multi_resolves_dedicated_localization_modules_for_de_fr tests/test_runtime_multi_translate.py::test_de_fr_adapters_keep_admin_prompt_resolver -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add appcore/runtime_multi.py tests/test_runtime_multi_translate.py
git commit -m "test(multi): preserve localized prompt resolver usage"
```

## Task 3: Japanese Translation Override

**Files:**
- Modify: `appcore/runtime_multi.py`
- Test: `tests/test_runtime_multi_translate.py`

- [ ] **Step 1: Write failing Japanese translation test**

Add:

```python
def test_multi_ja_translate_uses_character_budget_localizer(tmp_path, monkeypatch):
    from appcore import task_state

    monkeypatch.setattr(task_state, "_db_upsert", lambda *a, **kw: None)
    monkeypatch.setattr(task_state, "_sync_task_to_db", lambda *a, **kw: None)

    task_id = "multi-ja-translate"
    video = tmp_path / "source.mp4"
    video.write_bytes(b"video")
    task_state.create(task_id, str(video), str(tmp_path), user_id=1)
    task_state.update(
        task_id,
        target_lang="ja",
        source_language="en",
        selected_voice_id="ja-voice",
        script_segments=[{"index": 0, "text": "Keep this bottle clean.", "start_time": 0, "end_time": 3}],
        variants={},
    )

    captured = {}

    def fake_generate(**kwargs):
        captured.update(kwargs)
        return {
            "full_text": "ボトルを清潔に保てます",
            "sentences": [{"index": 0, "text": "ボトルを清潔に保てます", "source_segment_indices": [0]}],
            "_messages": [{"role": "system", "content": "ja"}],
            "_usage": {},
        }

    monkeypatch.setattr("appcore.runtime_multi.ja_translate.generate_ja_localized_translation", fake_generate)
    monkeypatch.setattr("appcore.runtime_multi.ja_translate.build_source_full_text", lambda segs: "Keep this bottle clean.")
    monkeypatch.setattr("pipeline.extract.get_video_duration", lambda path: 3.0)
    monkeypatch.setattr("appcore.runtime_multi._build_review_segments", lambda segs, loc: [])
    monkeypatch.setattr("appcore.runtime_multi.build_asr_artifact", lambda *a, **kw: {})
    monkeypatch.setattr("appcore.runtime_multi.build_translate_artifact", lambda *a, **kw: {})

    runner = _make_runner()
    runner._step_translate(task_id)

    assert captured["voice_id"] == "ja-voice"
    updated = task_state.get(task_id)
    assert updated["localized_translation"]["full_text"] == "ボトルを清潔に保てます"
    assert updated["variants"]["normal"]["localized_translation"]["full_text"] == "ボトルを清潔に保てます"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_runtime_multi_translate.py::test_multi_ja_translate_uses_character_budget_localizer -q`

Expected: FAIL because `MultiTranslateRunner._step_translate` still uses generic `generate_localized_translation`.

- [ ] **Step 3: Implement adapter translation override**

Add `run_translate()` to `_JapaneseMultiTranslateAdapter` and route it from `MultiTranslateRunner._step_translate` before the generic LLM call:

```python
def _step_translate(self, task_id: str) -> None:
    task = task_state.get(task_id)
    adapter = self._get_language_adapter(task)
    if getattr(adapter, "handles_translate", False):
        return adapter.run_translate(self, task_id)
    ...
```

The Japanese method must write the same state keys as the generic step:

```python
variant_state["localized_translation"] = localized_translation
task_state.update(
    task_id,
    source_full_text_zh=source_full_text,
    localized_translation=localized_translation,
    variants=variants,
    segments=review_segments,
    _segments_confirmed=not requires_confirmation,
)
```

- [ ] **Step 4: Run Japanese translation test**

Run: `python -m pytest tests/test_runtime_multi_translate.py::test_multi_ja_translate_uses_character_budget_localizer -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add appcore/runtime_multi.py tests/test_runtime_multi_translate.py
git commit -m "feat(multi): use Japanese character-budget translation"
```

## Task 4: Japanese TTS Override

**Files:**
- Modify: `appcore/runtime_multi.py`
- Test: `tests/test_runtime_multi_translate.py`

- [ ] **Step 1: Write failing Japanese TTS test**

Add:

```python
def test_multi_ja_tts_uses_japanese_tts_builders(tmp_path, monkeypatch):
    from appcore import task_state

    monkeypatch.setattr(task_state, "_db_upsert", lambda *a, **kw: None)
    monkeypatch.setattr(task_state, "_sync_task_to_db", lambda *a, **kw: None)

    task_id = "multi-ja-tts"
    video = tmp_path / "source.mp4"
    video.write_bytes(b"video")
    task_state.create(task_id, str(video), str(tmp_path), user_id=1)
    task_state.update(
        task_id,
        target_lang="ja",
        source_language="en",
        selected_voice_id="ja-voice",
        video_path=str(video),
        script_segments=[{"index": 0, "text": "Keep this bottle clean.", "start_time": 0, "end_time": 3}],
        variants={"normal": {"localized_translation": {
            "full_text": "ボトルを清潔に保てます",
            "sentences": [{"index": 0, "text": "ボトルを清潔に保てます", "source_segment_indices": [0]}],
        }}},
    )

    calls = {"script": 0, "segments": 0}

    monkeypatch.setattr("pipeline.extract.get_video_duration", lambda path: 3.0)
    monkeypatch.setattr("appcore.runtime_multi.resolve_key", lambda *a, **kw: "eleven-key")
    monkeypatch.setattr("appcore.runtime_multi.ja_translate.count_visible_japanese_chars", lambda text: 12)

    def fake_script(localized):
        calls["script"] += 1
        return {"full_text": localized["full_text"], "blocks": [{"index": 0, "text": localized["full_text"]}], "subtitle_chunks": []}

    def fake_segments(script, segments):
        calls["segments"] += 1
        return [{"index": 0, "tts_text": script["full_text"], "source_segment_indices": [0]}]

    monkeypatch.setattr("appcore.runtime_multi.ja_translate.build_ja_tts_script", fake_script)
    monkeypatch.setattr("appcore.runtime_multi.ja_translate.build_ja_tts_segments", fake_segments)
    monkeypatch.setattr("appcore.runtime_multi.generate_full_audio", lambda *a, **kw: {
        "full_audio_path": str(tmp_path / "tts_full.ja_round_1.mp3"),
        "segments": [{"index": 0, "tts_duration": 3.1, "tts_text": "ボトルを清潔に保てます"}],
    })
    monkeypatch.setattr("appcore.runtime_multi._get_audio_duration", lambda path: 3.1)
    monkeypatch.setattr("appcore.runtime_multi.build_timeline_manifest", lambda *a, **kw: {})
    monkeypatch.setattr("appcore.runtime_multi.build_tts_artifact", lambda *a, **kw: {})
    monkeypatch.setattr("appcore.runtime_multi.ai_billing.log_request", lambda *a, **kw: None)

    runner = _make_runner()
    runner._run_default_tts_loop(task_id, str(tmp_path))

    assert calls == {"script": 1, "segments": 1}
    updated = task_state.get(task_id)
    assert updated["tts_final_round"] == 1
    assert updated["tts_duration_status"] == "converged"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_runtime_multi_translate.py::test_multi_ja_tts_uses_japanese_tts_builders -q`

Expected: FAIL because `_run_default_tts_loop` uses the generic word-count duration loop.

- [ ] **Step 3: Implement `run_tts()` on the Japanese adapter**

Port the smallest compatible subset from `JapaneseTranslateRunner._step_tts`:

```python
if getattr(adapter, "handles_tts", False):
    return adapter.run_tts(self, task_id, task_dir)
```

The adapter should:

- build `ja_translate.build_ja_tts_script`
- build `ja_translate.build_ja_tts_segments`
- generate audio with `generate_full_audio`
- measure `_get_audio_duration`
- run up to 5 rounds with `rewrite_ja_localized_translation`
- select converged or best round
- write `tts_script.normal.json`, `tts_result.normal.json`, `timeline_manifest.normal.json`, `tts_duration_rounds.json`
- update multi-compatible task/variant state

- [ ] **Step 4: Run Japanese TTS test**

Run: `python -m pytest tests/test_runtime_multi_translate.py::test_multi_ja_tts_uses_japanese_tts_builders -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add appcore/runtime_multi.py tests/test_runtime_multi_translate.py
git commit -m "feat(multi): use Japanese character-budget TTS"
```

## Task 5: Japanese Subtitle Override

**Files:**
- Modify: `appcore/runtime_multi.py`
- Test: `tests/test_runtime_multi_subtitle.py`

- [ ] **Step 1: Write failing Japanese subtitle test**

Add:

```python
def test_multi_ja_subtitle_uses_timed_japanese_chunks(tmp_path, monkeypatch):
    from appcore import task_state
    from appcore.events import EventBus
    from appcore.runtime_multi import MultiTranslateRunner

    monkeypatch.setattr(task_state, "_db_upsert", lambda *a, **kw: None)
    monkeypatch.setattr(task_state, "_sync_task_to_db", lambda *a, **kw: None)

    task_id = "multi-ja-subtitle"
    task_state.create(task_id, "v.mp4", str(tmp_path), user_id=1)
    task_state.update(
        task_id,
        target_lang="ja",
        variants={"normal": {
            "tts_script": {"subtitle_chunks": [{"text": "ボトルを清潔に", "block_indices": [0]}]},
            "segments": [{"index": 0, "tts_duration": 2.0, "tts_text": "ボトルを清潔に"}],
        }},
    )

    called = {"timed": 0, "asr": 0}
    monkeypatch.setattr("appcore.runtime_multi.ja_translate.build_timed_subtitle_chunks", lambda script, segs: called.__setitem__("timed", 1) or [
        {"index": 0, "text": "ボトルを清潔に", "start_time": 0.0, "end_time": 2.0}
    ])
    monkeypatch.setattr("appcore.runtime_multi.asr_router.transcribe", lambda *a, **kw: called.__setitem__("asr", 1))

    runner = MultiTranslateRunner(bus=EventBus(), user_id=1)
    runner._step_subtitle(task_id, str(tmp_path))

    assert called == {"timed": 1, "asr": 0}
    assert task_state.get(task_id)["srt_path"].endswith("subtitle.normal.srt")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_runtime_multi_subtitle.py::test_multi_ja_subtitle_uses_timed_japanese_chunks -q`

Expected: FAIL because generic subtitle ASR realign path is still used.

- [ ] **Step 3: Implement `run_subtitle()` on the Japanese adapter**

At the top of `_step_subtitle`:

```python
adapter = self._get_language_adapter(task)
if getattr(adapter, "handles_subtitle", False):
    return adapter.run_subtitle(self, task_id, task_dir)
```

The Japanese adapter should use:

```python
corrected_chunks = ja_translate.build_timed_subtitle_chunks(tts_script, tts_segments)
srt_content = build_srt_from_chunks(corrected_chunks, weak_boundary_words=ja_rules.WEAK_STARTERS)
srt_content = ja_rules.post_process_srt(srt_content)
```

- [ ] **Step 4: Run subtitle test**

Run: `python -m pytest tests/test_runtime_multi_subtitle.py::test_multi_ja_subtitle_uses_timed_japanese_chunks -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add appcore/runtime_multi.py tests/test_runtime_multi_subtitle.py
git commit -m "feat(multi): use Japanese timed subtitles"
```

## Task 6: Regression Verification

**Files:**
- No production file changes.

- [ ] **Step 1: Run focused tests**

Run:

```bash
python -m pytest tests/test_runtime_multi_translate.py tests/test_runtime_multi_subtitle.py tests/test_runtime_ja_shared_shell.py tests/test_ja_translate_pipeline.py tests/test_languages_registry.py tests/test_tts_duration_loop.py tests/test_translate_profiles.py tests/test_prompt_defaults.py -q
```

Expected: all selected tests pass.

- [ ] **Step 2: Run diff check**

Run: `git diff --check`

Expected: no whitespace errors.

- [ ] **Step 3: Review final status**

Run: `git status --short`

Expected: clean after commits, or only intentional uncommitted plan progress if implementation is still in progress.

