# English Redub Speed-Aware Voice Match Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an isolated `英语视频重新配音` module for English-in/English-out redubbing, with task-level script mode and admin-configurable speed-aware voice matching.

**Architecture:** Keep existing Omni/Multi behavior unchanged by adding a new `english_redub` project type, blueprint, templates, runner, settings helper, and voice-speed ranking module. The new runner subclasses `OmniTranslateRunner`, uses fixed English plugin config, and overrides only the English redub-specific seams: voice matching and original-script translation assembly.

**Tech Stack:** Python 3.12, Flask blueprints/templates, existing `task_state`/`translation_route_store`, pytest, SQLite/MySQL-compatible migrations.

---

## Document Anchor

- Spec: `docs/superpowers/specs/2026-05-18-english-redub-speed-aware-voice-match-design.md`
- Routing and CSRF rules: `AGENTS.md#Verification（每次改动后顺序执行）`
- Template guardrails: `web/templates/CLAUDE.md`, `web/static/CLAUDE.md`

## File Structure

- Create `appcore/english_redub_settings.py`: validate and persist `english_redub_voice_match_strategy`.
- Create `appcore/runtime_english_redub.py`: isolated runner with fixed English config, script-mode dispatch, and speed-aware voice matching.
- Create `appcore/voice_preview_speech_rate.py`: DAO for `voice_preview_speech_rate`.
- Create `pipeline/voice_match_speed.py`: pure speech-rate computation and candidate reranking.
- Create `web/services/english_redub_pipeline_runner.py`: Socket.IO runner adapter mirroring Omni.
- Create `web/routes/english_redub.py`: new list/detail/API blueprint for `english_redub`.
- Create `web/templates/english_redub_list.html`: English-only upload page with script mode switch.
- Create `web/templates/english_redub_detail.html`: shared detail shell configuration.
- Modify `web/app.py`, `web/templates/layout.html`, `web/templates/_translate_detail_shell.html`, `appcore/permissions.py`, `web/routes/settings.py`, `web/templates/settings.html`.
- Add migration `db/migrations/2026_05_18_voice_preview_speech_rate.sql`.
- Add tests: `tests/test_english_redub_settings.py`, `tests/test_english_redub_voice_match_speed.py`, `tests/test_english_redub_runtime.py`, `tests/test_english_redub_routes.py`, plus narrow template/settings assertions.

## Task 1: Settings Helper

**Files:**
- Create: `appcore/english_redub_settings.py`
- Test: `tests/test_english_redub_settings.py`

- [ ] **Step 1: Write failing tests**

```python
from appcore import english_redub_settings as s


def test_voice_match_strategy_defaults_to_legacy(monkeypatch):
    monkeypatch.setattr(s.settings_store, "get_setting", lambda key, default=None: None)

    assert s.get_voice_match_strategy() == "legacy"


def test_voice_match_strategy_rejects_invalid_values(monkeypatch):
    calls = []
    monkeypatch.setattr(s.settings_store, "set_setting", lambda key, value: calls.append((key, value)))

    assert s.set_voice_match_strategy("timbre_speed") == "timbre_speed"
    assert calls == [(s.SETTING_VOICE_MATCH_STRATEGY, "timbre_speed")]
```

- [ ] **Step 2: Verify red**

Run: `pytest tests/test_english_redub_settings.py -q`

Expected: import failure for `appcore.english_redub_settings`.

- [ ] **Step 3: Implement minimal helper**

```python
from __future__ import annotations

from appcore import settings as settings_store

SETTING_VOICE_MATCH_STRATEGY = "english_redub_voice_match_strategy"
STRATEGY_LEGACY = "legacy"
STRATEGY_TIMBRE_SPEED = "timbre_speed"
VALID_VOICE_MATCH_STRATEGIES = {STRATEGY_LEGACY, STRATEGY_TIMBRE_SPEED}


def normalize_voice_match_strategy(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in VALID_VOICE_MATCH_STRATEGIES:
        return normalized
    return STRATEGY_LEGACY


def get_voice_match_strategy() -> str:
    return normalize_voice_match_strategy(
        settings_store.get_setting(SETTING_VOICE_MATCH_STRATEGY, STRATEGY_LEGACY)
    )


def set_voice_match_strategy(value: str | None) -> str:
    normalized = normalize_voice_match_strategy(value)
    settings_store.set_setting(SETTING_VOICE_MATCH_STRATEGY, normalized)
    return normalized
```

- [ ] **Step 4: Verify green**

Run: `pytest tests/test_english_redub_settings.py -q`

Expected: all tests pass.

## Task 2: Speech-Rate Candidate Reranking

**Files:**
- Create: `pipeline/voice_match_speed.py`
- Create: `appcore/voice_preview_speech_rate.py`
- Create: `db/migrations/2026_05_18_voice_preview_speech_rate.sql`
- Test: `tests/test_english_redub_voice_match_speed.py`

- [ ] **Step 1: Write failing tests**

```python
from pipeline.voice_match_speed import (
    compute_source_speech_rate,
    rank_speed_aware_candidates,
)


def test_compute_source_speech_rate_ignores_tiny_fragments():
    utterances = [
        {"text": "buy this now", "start_time": 0.0, "end_time": 1.0},
        {"text": "ok", "start_time": 1.05, "end_time": 1.2},
        {"text": "it fits your daily routine", "start_time": 2.0, "end_time": 4.0},
    ]

    rate = compute_source_speech_rate(utterances)

    assert rate["sample_utterance_count"] == 2
    assert rate["ignored_utterance_count"] == 1
    assert 2.6 < rate["source_words_per_second"] < 2.9


def test_rank_speed_aware_keeps_similarity_floor():
    candidates = [
        {"voice_id": "top", "similarity": 0.90},
        {"voice_id": "fast", "similarity": 0.86},
        {"voice_id": "low", "similarity": 0.70},
    ]
    preview_rates = {"top": 2.0, "fast": 3.8, "low": 3.8}
    source_rate = {"source_words_per_second": 3.8}

    ranked = rank_speed_aware_candidates(candidates, source_rate, preview_rates, top_k=2)

    assert [row["voice_id"] for row in ranked] == ["fast", "top"]
    assert "low" not in [row["voice_id"] for row in ranked]
    assert ranked[0]["speed_match_score"] > ranked[1]["speed_match_score"]
```

- [ ] **Step 2: Verify red**

Run: `pytest tests/test_english_redub_voice_match_speed.py -q`

Expected: import failure for `pipeline.voice_match_speed`.

- [ ] **Step 3: Implement pure ranking and DAO**

```python
def rank_speed_aware_candidates(candidates, source_rate, preview_rates, *, top_k=10):
    top_similarity = max(float(c.get("similarity") or 0.0) for c in candidates)
    floor = top_similarity - 0.08
    source_wps = float(source_rate.get("source_words_per_second") or 0.0)
    ranked = []
    for candidate in candidates:
        similarity = float(candidate.get("similarity") or 0.0)
        if similarity < floor:
            continue
        voice_id = str(candidate.get("voice_id") or "").strip()
        preview_wps = preview_rates.get(voice_id)
        speed_score = _speed_score(source_wps, preview_wps)
        if speed_score is None:
            combined = similarity
        else:
            combined = similarity * 0.75 + speed_score * 0.25
        row = dict(candidate)
        row["speed_match_score"] = speed_score
        row["combined_score"] = combined
        ranked.append(row)
    ranked.sort(key=lambda row: row["combined_score"], reverse=True)
    return ranked[:top_k]
```

Migration creates `voice_preview_speech_rate` with unique key `(voice_id, language, preview_url_hash)` and indexes `(language, voice_id)`.

- [ ] **Step 4: Verify green**

Run: `pytest tests/test_english_redub_voice_match_speed.py -q`

Expected: all tests pass.

## Task 3: English Redub Runner

**Files:**
- Create: `appcore/runtime_english_redub.py`
- Test: `tests/test_english_redub_runtime.py`

- [ ] **Step 1: Write failing tests**

```python
from appcore.runtime_english_redub import (
    ENGLISH_REDUB_DEFAULT_PLUGIN_CONFIG,
    EnglishRedubRunner,
)


def test_default_plugin_config_is_english_sentence_reconcile():
    assert ENGLISH_REDUB_DEFAULT_PLUGIN_CONFIG["translate_algo"] == "shot_char_limit"
    assert ENGLISH_REDUB_DEFAULT_PLUGIN_CONFIG["tts_strategy"] == "sentence_reconcile"
    assert ENGLISH_REDUB_DEFAULT_PLUGIN_CONFIG["subtitle"] == "sentence_units"


def test_script_mode_defaults_to_original(monkeypatch):
    monkeypatch.setattr("appcore.task_state.get", lambda task_id: {"type": "english_redub"})
    runner = EnglishRedubRunner()

    assert runner._resolve_script_mode("t-1") == "original"


def test_original_translate_builds_av_sentences(monkeypatch):
    updates = {}
    monkeypatch.setattr("appcore.task_state.get", lambda task_id: {
        "task_dir": "",
        "source_language": "en",
        "target_lang": "en",
        "script_segments": [{"index": 0, "text": "Hello world", "start_time": 0, "end_time": 1.5}],
        "variants": {},
    })
    monkeypatch.setattr("appcore.task_state.update", lambda task_id, **kwargs: updates.update(kwargs))
    monkeypatch.setattr("appcore.task_state.set_artifact", lambda *args, **kwargs: None)
    runner = EnglishRedubRunner()
    monkeypatch.setattr(runner, "_set_step", lambda *args, **kwargs: None)

    runner._step_translate_original("t-1")

    assert updates["localized_translation"]["full_text"] == "Hello world"
    assert updates["variants"]["av"]["sentences"][0]["text"] == "Hello world"
```

- [ ] **Step 2: Verify red**

Run: `pytest tests/test_english_redub_runtime.py -q`

Expected: import failure for `appcore.runtime_english_redub`.

- [ ] **Step 3: Implement runner**

```python
class EnglishRedubRunner(OmniTranslateRunner):
    project_type = "english_redub"
    profile_code = "omni"

    def _resolve_script_mode(self, task_id: str) -> str:
        task = task_state.get(task_id) or {}
        mode = str(task.get("script_mode") or "original").strip().lower()
        return mode if mode in {"original", "rewrite"} else "original"

    def _resolve_plugin_config(self, task_id: str) -> dict:
        task = task_state.get(task_id) or {}
        cfg = task.get("plugin_config") or ENGLISH_REDUB_DEFAULT_PLUGIN_CONFIG
        return validate_plugin_config(cfg)

    def _translate_step(self, task_id: str) -> None:
        if self._resolve_script_mode(task_id) == "original":
            self._step_translate_original(task_id)
        else:
            self.profile.translate(self, task_id)
```

`_get_pipeline_steps` mirrors Omni but maps `"translate"` to `_translate_step`.

- [ ] **Step 4: Verify green**

Run: `pytest tests/test_english_redub_runtime.py -q`

Expected: all tests pass.

## Task 4: Web Runner Adapter and Routes

**Files:**
- Create: `web/services/english_redub_pipeline_runner.py`
- Create: `web/routes/english_redub.py`
- Modify: `web/app.py`
- Test: `tests/test_english_redub_routes.py`

- [ ] **Step 1: Write failing route tests**

```python
def test_english_redub_list_page_is_registered(authed_client_no_db, monkeypatch):
    monkeypatch.setattr("web.routes.english_redub.recover_all_interrupted_tasks", lambda: None)
    monkeypatch.setattr("web.routes.english_redub.translation_route_store.list_projects_with_creator", lambda **kwargs: [])

    resp = authed_client_no_db.get("/english-redub")

    assert resp.status_code == 200
    assert "英语视频重新配音" in resp.get_data(as_text=True)


def test_english_redub_start_persists_fixed_english_and_script_mode(authed_client_no_db, monkeypatch, tmp_path):
    updates = {}
    monkeypatch.setattr("web.routes.english_redub.save_uploaded_video", lambda *args, **kwargs: (str(tmp_path / "v.mp4"), 1, "video/mp4"))
    monkeypatch.setattr("web.routes.english_redub._ensure_uploaded_video_thumbnail", lambda *args, **kwargs: "")
    monkeypatch.setattr("web.routes.english_redub.english_redub_pipeline_runner.start", lambda *args, **kwargs: True)
    monkeypatch.setattr("web.routes.english_redub.store.create", lambda *args, **kwargs: None)
    monkeypatch.setattr("web.routes.english_redub.store.update", lambda task_id, **kwargs: updates.update(kwargs))
    monkeypatch.setattr("web.routes.english_redub.store.set_preview_file", lambda *args, **kwargs: None)

    data = {
        "video": (io.BytesIO(b"video"), "demo.mp4"),
        "script_mode": "rewrite",
    }
    resp = authed_client_no_db.post("/api/english-redub/start", data=data, content_type="multipart/form-data")

    assert resp.status_code == 201
    assert updates["type"] == "english_redub"
    assert updates["source_language"] == "en"
    assert updates["target_lang"] == "en"
    assert updates["script_mode"] == "rewrite"
```

- [ ] **Step 2: Verify red**

Run: `pytest tests/test_english_redub_routes.py -q`

Expected: route import or 404 failure.

- [ ] **Step 3: Implement route module**

Start from `web/routes/omni_translate.py` behavior but keep these hard-coded differences:

```python
bp = Blueprint("english_redub", __name__)
PROJECT_TYPE = "english_redub"
API_BASE = "/api/english-redub"
PAGE_BASE = "/english-redub"
FIXED_LANG = "en"
```

`upload_and_start()` accepts only `script_mode in {"original", "rewrite"}`, writes `source_language="en"` and `target_lang="en"`, and calls `english_redub_pipeline_runner.start(...)`.

- [ ] **Step 4: Verify green**

Run: `pytest tests/test_english_redub_routes.py -q`

Expected: all tests pass.

## Task 5: Templates, Menu, Permissions

**Files:**
- Create: `web/templates/english_redub_list.html`
- Create: `web/templates/english_redub_detail.html`
- Modify: `web/templates/layout.html`
- Modify: `web/templates/_translate_detail_shell.html`
- Modify: `appcore/permissions.py`
- Test: `tests/test_english_redub_templates.py`

- [ ] **Step 1: Write failing template tests**

```python
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_layout_contains_english_redub_menu_entry():
    layout = (ROOT / "web/templates/layout.html").read_text(encoding="utf-8")
    assert "has_permission('english_redub')" in layout
    assert "/english-redub" in layout
    assert "英语视频重新配音" in layout


def test_list_template_contains_script_mode_switch():
    html = (ROOT / "web/templates/english_redub_list.html").read_text(encoding="utf-8")
    assert 'name="script_mode"' in html
    assert 'value="original"' in html
    assert 'value="rewrite"' in html


def test_permissions_register_english_redub():
    source = (ROOT / "appcore/permissions.py").read_text(encoding="utf-8")
    assert '"english_redub"' in source
```

- [ ] **Step 2: Verify red**

Run: `pytest tests/test_english_redub_templates.py -q`

Expected: missing template/menu/permission assertions fail.

- [ ] **Step 3: Implement templates and permission entries**

`english_redub_detail.html`:

```jinja
{% extends "_translate_detail_shell.html" %}
{% set api_base = '/api/english-redub' %}
{% set url_for_detail = '/english-redub/__TASK_ID__' %}
{% set pipeline_kind = 'english_redub' %}
```

`permissions.py` adds `("english_redub", GROUP_BUSINESS, "英语视频重新配音", True, True)` near translation entries and includes it in translator defaults.

- [ ] **Step 4: Verify green**

Run: `pytest tests/test_english_redub_templates.py -q`

Expected: all tests pass.

## Task 6: Admin Switch UI

**Files:**
- Modify: `web/routes/settings.py`
- Modify: `web/templates/settings.html`
- Test: `tests/test_english_redub_settings_tab.py`

- [ ] **Step 1: Write failing tests**

```python
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_settings_template_has_english_redub_voice_match_control():
    html = (ROOT / "web/templates/settings.html").read_text(encoding="utf-8")
    assert "english_redub_voice_match_strategy" in html
    assert "旧音色匹配" in html
    assert "音色 + 语速匹配" in html


def test_settings_route_handles_omni_preset_post():
    py = (ROOT / "web/routes/settings.py").read_text(encoding="utf-8")
    assert "_handle_omni_preset_post()" in py
    assert "english_redub_settings" in py
```

- [ ] **Step 2: Verify red**

Run: `pytest tests/test_english_redub_settings_tab.py -q`

Expected: assertions fail because the control is not present.

- [ ] **Step 3: Add settings form handling**

In `settings.index()`, branch `tab == "omni_preset"` to `_handle_omni_preset_post()`. The handler calls:

```python
from appcore import english_redub_settings

english_redub_settings.set_voice_match_strategy(
    request.form.get("english_redub_voice_match_strategy")
)
```

Pass `english_redub_voice_match_strategy=english_redub_settings.get_voice_match_strategy()` into `settings.html`.

- [ ] **Step 4: Verify green**

Run: `pytest tests/test_english_redub_settings_tab.py -q`

Expected: all tests pass.

## Task 7: Integration Verification

**Files:**
- Run-only verification.

- [ ] **Step 1: Targeted pytest**

Run:

```bash
pytest tests/test_english_redub_settings.py \
  tests/test_english_redub_voice_match_speed.py \
  tests/test_english_redub_runtime.py \
  tests/test_english_redub_routes.py \
  tests/test_english_redub_templates.py \
  tests/test_english_redub_settings_tab.py \
  tests/test_omni_translate_routes.py \
  tests/test_settings_omni_preset_tab.py \
  tests/test_voice_match.py -q
```

Expected: all selected tests pass.

- [ ] **Step 2: Route guard smoke**

Run:

```bash
python -m web.app
```

Open another shell and verify unauthenticated routes:

```bash
curl -I http://127.0.0.1:5000/english-redub
curl -I http://127.0.0.1:5000/english-redub/not-found
```

Expected: unauthenticated requests return redirect or auth response, not 500.

- [ ] **Step 3: Commit**

```bash
git add appcore pipeline web db tests docs/superpowers/plans/2026-05-18-english-redub-speed-aware-voice-match-plan.md
git commit -m "feat: add english redub workflow" -m "Docs-anchor: docs/superpowers/specs/2026-05-18-english-redub-speed-aware-voice-match-design.md#文档锚点"
```

## Self-Review

- Spec coverage: menu, fixed English input/output, isolated `english_redub` project type, admin strategy switch, script mode, speed-aware reranking, and legacy fallback are covered by Tasks 1-6.
- Red-flag scan: this plan contains no unresolved markers and no open-ended implementation instructions.
- Type consistency: `script_mode` uses `"original" | "rewrite"`; voice strategy uses `"legacy" | "timbre_speed"`; project type and permission code both use `english_redub`.
