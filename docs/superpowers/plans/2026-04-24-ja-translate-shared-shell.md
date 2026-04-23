# JA Shared-Shell Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild `ja_translate` on top of the multi-translate shared detail shell without regressing existing multi-language flows, while keeping the Japanese translation, duration-control, and subtitle segmentation logic intact.

**Architecture:** Extract the current multi-translate detail page into a reusable shell, then make the Japanese routes and runner satisfy that shell’s API/state contract. Keep `JapaneseTranslateRunner` as the language-specific core, but add the multi-style `voice_match` pause, shared round-file protocol, and normalized duration-round payload so the existing selector and Duration Loop UI can render Japanese tasks directly.

**Tech Stack:** Python, Flask, Jinja2, vanilla JS, pytest, existing `appcore.runtime_*`, `web.routes.*`, `web.templates.*`, and `pipeline.*` helpers.

---

## File Map

- Create: `web/templates/_translate_detail_shell.html`
  Shared detail-page shell used by both `multi_translate` and `ja_translate`.

- Modify: `web/templates/multi_translate_detail.html`
  Convert to a thin wrapper that sets variables and includes the shared shell.

- Modify: `web/templates/ja_translate_detail.html`
  Convert to a thin wrapper that sets `detail_mode="ja"` and includes the shared shell.

- Modify: `web/templates/_task_workbench_scripts.html`
  Expose `detailMode` / shared selector config and keep shared shell rendering mode-aware.

- Modify: `web/templates/_voice_selector_multi.html`
  Keep the shared selector template but make wording and data attributes neutral enough for Japanese tasks.

- Modify: `web/static/voice_selector_multi.js`
  Replace hard-coded `/api/multi-translate/...` calls with config-driven endpoints and keep the selector reusable for `ja_translate`.

- Create: `web/services/translate_detail_protocol.py`
  Shared protocol helpers for voice-library payloads, confirm-voice normalization, and round-file resolution.

- Modify: `web/routes/multi_translate.py`
  Route existing multi-language endpoints through the shared protocol helper without changing behavior.

- Modify: `web/routes/ja_translate.py`
  Add `voice-library`, `rematch`, `confirm-voice`, and `round-file` endpoints compatible with the shared shell.

- Modify: `appcore/runtime_ja.py`
  Add the multi-style `voice_match` stage and normalize Japanese duration-loop state/artifact fields.

- Create: `tests/test_translate_detail_shell_templates.py`
  Regression tests for shared template extraction and JS contract.

- Modify: `tests/test_multi_translate_routes.py`
  Lock current multi behavior while the shared protocol is extracted.

- Modify: `tests/test_ja_translate_routes.py`
  Add route coverage for the new Japanese shared-shell contract.

- Create: `tests/test_runtime_ja_shared_shell.py`
  Focused runtime tests for Japanese voice-match insertion and duration payload normalization.

---

### Task 1: Extract a Shared Detail Shell Without Changing Multi Behavior

**Files:**
- Create: `web/templates/_translate_detail_shell.html`
- Modify: `web/templates/multi_translate_detail.html`
- Modify: `web/templates/ja_translate_detail.html`
- Create: `tests/test_translate_detail_shell_templates.py`

- [ ] **Step 1: Write the failing template regression tests**

```python
from pathlib import Path


def test_multi_and_ja_detail_templates_include_shared_shell():
    root = Path(__file__).resolve().parents[1]
    multi = (root / "web" / "templates" / "multi_translate_detail.html").read_text(encoding="utf-8")
    ja = (root / "web" / "templates" / "ja_translate_detail.html").read_text(encoding="utf-8")

    assert '{% include "_translate_detail_shell.html" %}' in multi
    assert '{% include "_translate_detail_shell.html" %}' in ja


def test_shared_shell_contains_mode_specific_layout_rules():
    root = Path(__file__).resolve().parents[1]
    shared = (root / "web" / "templates" / "_translate_detail_shell.html").read_text(encoding="utf-8")

    assert "detail_mode == 'multi'" in shared
    assert "detail_mode == 'ja'" in shared
    assert '{% include "_voice_selector_multi.html" %}' in shared
    assert '{% include "_task_workbench.html" %}' in shared
```

- [ ] **Step 2: Run the template test and confirm it fails**

Run: `python -m pytest tests/test_translate_detail_shell_templates.py -q`

Expected: fail because `_translate_detail_shell.html` does not exist yet and the detail templates still inline their own structure.

- [ ] **Step 3: Create the shared shell and reduce both detail pages to wrappers**

```jinja2
{# web/templates/_translate_detail_shell.html #}
{% if project.deleted_at %}
<a class="back-link" href="{{ back_href }}">{{ back_label }}</a>
<div class="expired-notice">
  <p style="font-size: 32px; margin-bottom: 12px;">任务已过期</p>
  <p>该项目对应的文件已经被清理，无法继续查看或处理。</p>
</div>
{% else %}
<a class="back-link" href="{{ back_href }}">{{ back_label }}</a>
<p class="page-subtitle">
  {{ subtitle_line }}
  <span class="target-lang-badge">目标语言：{{ target_lang | upper }}</span>
  {% if detail_mode == 'ja' %}
  <span class="target-lang-badge">日语字符预算流程</span>
  {% endif %}
</p>
{% if state and state.parent_task_id %}
<p style="margin:-4px 0 12px; font-size:13px;">
  本任务由批次翻译创建 · <a href="/tasks/{{ state.parent_task_id }}" style="color:oklch(56% 0.16 230)">查看父批次任务 →</a>
</p>
{% endif %}
{% include "_voice_selector_multi.html" %}
{% include "_task_workbench.html" %}
{% endif %}
```

```jinja2
{# web/templates/multi_translate_detail.html #}
{% extends "layout.html" %}
{% if not project.deleted_at %}
{% set allow_upload = false %}
{% set show_back_link = false %}
{% set task_id = project.id %}
{% set initial_task = state %}
{% set api_base = '/api/multi-translate' %}
{% set url_for_detail = '/multi-translate/__TASK_ID__' %}
{% set voice_language = target_lang %}
{% set default_source_language = 'en' %}
{% set detail_mode = 'multi' %}
{% set back_href = '/multi-translate' %}
{% set back_label = '← 返回多语种视频翻译列表' %}
{% set subtitle_line = '中文/英文 → ' ~ (target_lang | upper) ~ ' 本土化翻译' %}
{% endif %}
{% block content %}{% include "_translate_detail_shell.html" %}{% endblock %}
```

```jinja2
{# web/templates/ja_translate_detail.html #}
{% extends "layout.html" %}
{% if not project.deleted_at %}
{% set allow_upload = false %}
{% set show_back_link = false %}
{% set task_id = project.id %}
{% set initial_task = state %}
{% set api_base = '/api/ja-translate' %}
{% set url_for_detail = '/ja-translate/__TASK_ID__' %}
{% set voice_language = 'ja' %}
{% set default_source_language = state.source_language or 'en' %}
{% set detail_mode = 'ja' %}
{% set back_href = '/ja-translate' %}
{% set back_label = '← 返回视频翻译（日语）列表' %}
{% set subtitle_line = '英文/中文 → 日语本土化配音' %}
{% endif %}
{% block content %}{% include "_translate_detail_shell.html" %}{% endblock %}
```

- [ ] **Step 4: Re-run the template regression tests**

Run: `python -m pytest tests/test_translate_detail_shell_templates.py -q`

Expected: `2 passed`

- [ ] **Step 5: Commit the shared-shell extraction**

```bash
git add web/templates/_translate_detail_shell.html web/templates/multi_translate_detail.html web/templates/ja_translate_detail.html tests/test_translate_detail_shell_templates.py
git commit -m "refactor(web): extract shared translate detail shell"
```

### Task 2: Make the Shared Selector and Workbench Scripts API-Base Aware

**Files:**
- Modify: `web/templates/_task_workbench_scripts.html`
- Modify: `web/templates/_voice_selector_multi.html`
- Modify: `web/static/voice_selector_multi.js`
- Modify: `tests/test_translate_detail_shell_templates.py`
- Modify: `tests/test_multi_translate_routes.py`

- [ ] **Step 1: Add failing tests for the config-driven JS contract**

```python
def test_task_workbench_config_exposes_detail_mode_and_selector_endpoints():
    root = Path(__file__).resolve().parents[1]
    script = (root / "web" / "templates" / "_task_workbench_scripts.html").read_text(encoding="utf-8")

    assert "detailMode:" in script
    assert "userDefaultVoiceApi:" in script


def test_voice_selector_uses_configured_api_base_instead_of_hardcoded_multi_routes():
    root = Path(__file__).resolve().parents[1]
    script = (root / "web" / "static" / "voice_selector_multi.js").read_text(encoding="utf-8")

    assert "const apiBase = ((window.TASK_WORKBENCH_CONFIG || {}).apiBase || '/api/multi-translate').replace(/\\/$/, '');" in script
    assert "fetch(`${apiBase}/${taskId}/voice-library`)" in script
    assert "fetch(`${apiBase}/${taskId}/confirm-voice`" in script
    assert "fetch(`${apiBase}/${taskId}/subtitle-preview`" in script
    assert "`/api/multi-translate/${taskId}/subtitle-preview`" not in script
```

- [ ] **Step 2: Run the JS/template regression tests and confirm they fail**

Run: `python -m pytest tests/test_translate_detail_shell_templates.py tests/test_multi_translate_routes.py -q`

Expected: fail because `voice_selector_multi.js` still hard-codes `/api/multi-translate/...` and the workbench config does not expose `detailMode` / `userDefaultVoiceApi`.

- [ ] **Step 3: Wire all shared-shell fetches through `TASK_WORKBENCH_CONFIG`**

```html
{# web/templates/_task_workbench_scripts.html #}
const TASK_WORKBENCH_CONFIG = {
  taskId: {{ task_id|tojson }},
  initialTask: {{ initial_task|tojson }},
  allowUpload: {{ 'true' if allow_upload else 'false' }},
  detailUrlTemplate: {{ url_for_detail|tojson }},
  apiBase: {{ api_base|tojson }},
  voiceLanguage: {{ voice_language|tojson }},
  defaultSourceLanguage: {{ default_source_language|tojson }},
  detailMode: {{ (detail_mode|default('multi'))|tojson }},
  userDefaultVoiceApi: {{ '/api/multi-translate/user-default-voice'|tojson }},
};
```

```javascript
// web/static/voice_selector_multi.js
const apiBase = ((window.TASK_WORKBENCH_CONFIG || {}).apiBase || '/api/multi-translate').replace(/\/$/, '');
const detailMode = (window.TASK_WORKBENCH_CONFIG || {}).detailMode || 'multi';
const userDefaultVoiceApi = (window.TASK_WORKBENCH_CONFIG || {}).userDefaultVoiceApi || '/api/multi-translate/user-default-voice';

const subtitlePreviewUrl = `${apiBase}/${taskId}/subtitle-preview`;
const voiceLibraryUrl = `${apiBase}/${taskId}/voice-library`;
const rematchUrl = `${apiBase}/${taskId}/rematch`;
const confirmVoiceUrl = `${apiBase}/${taskId}/confirm-voice`;
const sourceVideoArtifactUrl = `${apiBase}/${taskId}/artifact/source_video`;
const hardVideoArtifactUrl = `${apiBase}/${taskId}/artifact/hard_video`;
```

```javascript
// web/static/voice_selector_multi.js
const resp = await fetch(voiceLibraryUrl);
const previewResp = await fetch(subtitlePreviewUrl, { cache: "no-store" });
const confirmResp = await fetch(confirmVoiceUrl, {
  method: "POST",
  headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken() },
  body: JSON.stringify(body),
});
await fetch(userDefaultVoiceApi, {
  method: "PUT",
  headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken() },
  body: JSON.stringify({ lang, voice_id: voiceId, voice_name: voiceName }),
});
```

- [ ] **Step 4: Re-run the selector/workbench regression tests**

Run: `python -m pytest tests/test_translate_detail_shell_templates.py tests/test_multi_translate_routes.py -q`

Expected: targeted template and route tests pass again with the new config-driven URLs.

- [ ] **Step 5: Commit the script contract changes**

```bash
git add web/templates/_task_workbench_scripts.html web/templates/_voice_selector_multi.html web/static/voice_selector_multi.js tests/test_translate_detail_shell_templates.py tests/test_multi_translate_routes.py
git commit -m "refactor(web): generalize shared voice selector config"
```

### Task 3: Introduce a Shared Detail-Protocol Helper and Keep `multi_translate` Stable

**Files:**
- Create: `web/services/translate_detail_protocol.py`
- Modify: `web/routes/multi_translate.py`
- Create: `tests/test_translate_detail_protocol.py`
- Modify: `tests/test_multi_translate_routes.py`

- [ ] **Step 1: Write the failing protocol-helper tests**

```python
import pytest

from web.services.translate_detail_protocol import (
    build_voice_library_payload,
    normalize_confirm_voice_payload,
    resolve_round_file_entry,
)


def test_build_voice_library_payload_marks_ready_only_for_waiting_or_done():
    payload = build_voice_library_payload(
        state={"target_lang": "ja", "steps": {"extract": "done", "asr": "done", "voice_match": "running"}},
        owner_user_id=7,
        items=[{"voice_id": "v1"}],
        total=1,
        default_voice=None,
    )
    assert payload["voice_match_ready"] is False
    assert payload["pipeline"]["voice_match"] == "running"


def test_normalize_confirm_voice_payload_falls_back_to_defaults():
    normalized = normalize_confirm_voice_payload(
        body={},
        lang="ja",
        default_voice_id="voice-default",
    )
    assert normalized["voice_id"] == "voice-default"
    assert normalized["subtitle_font"] == "Impact"
    assert normalized["subtitle_size"] == 14
    assert normalized["subtitle_position_y"] == 0.68


def test_resolve_round_file_entry_rejects_unknown_kind():
    with pytest.raises(KeyError):
        resolve_round_file_entry({"localized_translation": ("x.json", "application/json")}, 1, "missing")
```

- [ ] **Step 2: Run the new helper tests and confirm they fail**

Run: `python -m pytest tests/test_translate_detail_protocol.py -q`

Expected: fail because `web.services.translate_detail_protocol` does not exist yet.

- [ ] **Step 3: Implement the shared helper and route multi-translate through it**

```python
# web/services/translate_detail_protocol.py
def build_voice_library_payload(*, state, owner_user_id, items, total, default_voice):
    steps = state.get("steps", {}) or {}
    pipeline = {
        "extract": steps.get("extract", "pending"),
        "asr": steps.get("asr", "pending"),
        "voice_match": steps.get("voice_match", "pending"),
    }
    return {
        "items": items,
        "total": total,
        "candidates": state.get("voice_match_candidates", []),
        "fallback_voice_id": state.get("voice_match_fallback_voice_id"),
        "selected_voice_id": state.get("selected_voice_id"),
        "pipeline": pipeline,
        "voice_match_ready": pipeline["voice_match"] in ("waiting", "done"),
        "default_voice": default_voice,
    }


def normalize_confirm_voice_payload(*, body, lang, default_voice_id):
    voice_id = (body.get("voice_id") or "").strip() or default_voice_id
    if not voice_id:
        raise ValueError(f"no default voice available for {lang}")
    return {
        "voice_id": voice_id,
        "voice_name": (body.get("voice_name") or "").strip() or None,
        "subtitle_font": (body.get("subtitle_font") or "Impact").strip(),
        "subtitle_size": int(body.get("subtitle_size") or 14),
        "subtitle_position_y": float(body.get("subtitle_position_y") or 0.68),
        "subtitle_position": (body.get("subtitle_position") or "bottom").strip(),
    }


def resolve_round_file_entry(allowed_round_kinds, round_index, kind):
    if round_index not in (1, 2, 3, 4, 5):
        raise KeyError(round_index)
    filename_pattern, mime = allowed_round_kinds[kind]
    return filename_pattern.format(r=round_index), mime
```

```python
# web/routes/multi_translate.py
payload = build_voice_library_payload(
    state=state,
    owner_user_id=owner_user_id,
    items=data.get("items", []),
    total=data.get("total", 0),
    default_voice=default_voice,
)
return jsonify(payload)
```

- [ ] **Step 4: Re-run helper and multi route tests**

Run: `python -m pytest tests/test_translate_detail_protocol.py tests/test_multi_translate_routes.py -q`

Expected: helper tests pass and existing multi route coverage stays green.

- [ ] **Step 5: Commit the shared protocol helper**

```bash
git add web/services/translate_detail_protocol.py web/routes/multi_translate.py tests/test_translate_detail_protocol.py tests/test_multi_translate_routes.py
git commit -m "refactor(routes): share translate detail protocol helpers"
```

### Task 4: Add the Shared-Shell Route Contract to `ja_translate`

**Files:**
- Modify: `web/routes/ja_translate.py`
- Modify: `tests/test_ja_translate_routes.py`

- [ ] **Step 1: Add failing route tests for the Japanese shared-shell endpoints**

```python
import json
from pathlib import Path


def test_ja_voice_library_route_returns_shared_payload(authed_client_no_db, monkeypatch):
    monkeypatch.setattr(
        "web.routes.ja_translate.db_query_one",
        lambda *args, **kwargs: {
            "state_json": json.dumps({"target_lang": "ja", "steps": {"extract": "done", "asr": "done", "voice_match": "waiting"}}, ensure_ascii=False),
            "user_id": 1,
        },
    )
    monkeypatch.setattr("appcore.voice_library_browse.list_voices", lambda **kwargs: {"items": [{"voice_id": "ja-1"}], "total": 1})
    monkeypatch.setattr("appcore.video_translate_defaults.resolve_default_voice", lambda *args, **kwargs: None)

    resp = authed_client_no_db.get("/api/ja-translate/task-ja/voice-library")

    assert resp.status_code == 200
    assert resp.get_json()["items"][0]["voice_id"] == "ja-1"
    assert resp.get_json()["voice_match_ready"] is True


def test_ja_confirm_voice_route_persists_selection_and_resumes_alignment(authed_client_no_db, monkeypatch):
    monkeypatch.setattr(
        "web.routes.ja_translate.db_query_one",
        lambda *args, **kwargs: {"state_json": json.dumps({"target_lang": "ja"}, ensure_ascii=False)},
    )
    monkeypatch.setattr("web.routes.ja_translate.db_execute", lambda *args, **kwargs: None)
    resumed = {}
    monkeypatch.setattr("appcore.task_state.update", lambda task_id, **kwargs: resumed.update({"task_id": task_id, "kwargs": kwargs}))
    monkeypatch.setattr("appcore.task_state.set_step", lambda *args, **kwargs: None)
    monkeypatch.setattr("appcore.task_state.set_current_review_step", lambda *args, **kwargs: None)
    monkeypatch.setattr("web.routes.ja_translate.ja_pipeline_runner.resume", lambda task_id, start_step, user_id=None: resumed.update({"resume_step": start_step, "user_id": user_id}))
    monkeypatch.setattr("appcore.video_translate_defaults.resolve_default_voice", lambda *args, **kwargs: "ja-default")

    resp = authed_client_no_db.post("/api/ja-translate/task-ja/confirm-voice", json={})

    assert resp.status_code == 200
    assert resumed["kwargs"]["selected_voice_id"] == "ja-default"
    assert resumed["resume_step"] == "alignment"


def test_ja_round_file_route_maps_shared_kind_names(tmp_path, authed_client_no_db, monkeypatch):
    from web.routes import ja_translate as r

    task_dir = tmp_path / "task-ja"
    task_dir.mkdir()
    target = task_dir / "ja_localized_rewrite_messages.round_2.json"
    target.write_text('{"ok": true}', encoding="utf-8")
    monkeypatch.setattr(r, "_get_viewable_task", lambda task_id: {"task_dir": str(task_dir)})

    resp = authed_client_no_db.get("/api/ja-translate/task-ja/round-file/2/localized_rewrite_messages")

    assert resp.status_code == 200
    assert resp.mimetype == "application/json"
```

- [ ] **Step 2: Run the Japanese route tests and confirm they fail**

Run: `python -m pytest tests/test_ja_translate_routes.py -q`

Expected: fail because the new endpoints are missing from `web.routes.ja_translate`.

- [ ] **Step 3: Implement `voice-library`, `rematch`, `confirm-voice`, and `round-file` for Japanese tasks**

```python
# web/routes/ja_translate.py
@bp.route("/api/ja-translate/<task_id>/voice-library", methods=["GET"])
@login_required
def voice_library_for_task(task_id: str):
    row = _query_viewable_project(task_id, "state_json, user_id")
    if not row:
        abort(404)
    state = json.loads(row["state_json"] or "{}")
    data = list_voices(language="ja", gender=request.args.get("gender") or None, q=request.args.get("q") or None, page=1, page_size=500)
    default_voice = _lookup_default_voice("ja", row.get("user_id") or current_user.id)
    payload = build_voice_library_payload(
        state=state,
        owner_user_id=row.get("user_id") or current_user.id,
        items=data.get("items", []),
        total=data.get("total", 0),
        default_voice=default_voice,
    )
    return jsonify(payload)
```

```python
# web/routes/ja_translate.py
@bp.route("/api/ja-translate/<task_id>/confirm-voice", methods=["POST"])
@login_required
def confirm_voice(task_id: str):
    row = db_query_one("SELECT state_json FROM projects WHERE id = %s AND user_id = %s", (task_id, current_user.id))
    if not row:
        abort(404)
    state = json.loads(row["state_json"] or "{}")
    default_voice_id = resolve_default_voice("ja", user_id=current_user.id)
    normalized = normalize_confirm_voice_payload(body=request.get_json() or {}, lang="ja", default_voice_id=default_voice_id)

    state.update({k: v for k, v in normalized.items() if v is not None})
    db_execute("UPDATE projects SET state_json = %s WHERE id = %s", (json.dumps(state, ensure_ascii=False), task_id))
    task_state.update(task_id, selected_voice_id=normalized["voice_id"], selected_voice_name=normalized["voice_name"], voice_id=normalized["voice_id"], subtitle_font=normalized["subtitle_font"], subtitle_size=normalized["subtitle_size"], subtitle_position_y=normalized["subtitle_position_y"], subtitle_position=normalized["subtitle_position"])
    task_state.set_step(task_id, "voice_match", "done")
    task_state.set_current_review_step(task_id, "")
    ja_pipeline_runner.resume(task_id, "alignment", user_id=current_user.id)
    return jsonify({"ok": True, "voice_id": normalized["voice_id"], "voice_name": normalized["voice_name"]})
```

```python
# web/routes/ja_translate.py
_ALLOWED_ROUND_KINDS = {
    "localized_translation": ("localized_translation.round_{r}.json", "application/json"),
    "localized_rewrite_messages": ("ja_localized_rewrite_messages.round_{r}.json", "application/json"),
    "initial_translate_messages": ("ja_localized_translate_messages.json", "application/json"),
    "tts_script": ("tts_script.round_{r}.json", "application/json"),
    "tts_full_audio": ("tts_full.ja_round_{r}.mp3", "audio/mpeg"),
}
```

- [ ] **Step 4: Re-run the Japanese route suite**

Run: `python -m pytest tests/test_ja_translate_routes.py -q`

Expected: the new Japanese route coverage passes and older `ja_translate` route tests remain green.

- [ ] **Step 5: Commit the Japanese shared-shell routes**

```bash
git add web/routes/ja_translate.py tests/test_ja_translate_routes.py
git commit -m "feat(ja): add shared-shell detail routes"
```

### Task 5: Add a Japanese `voice_match` Pause and Normalize Duration-Loop State

**Files:**
- Modify: `appcore/runtime_ja.py`
- Create: `tests/test_runtime_ja_shared_shell.py`
- Modify: `tests/test_ja_translate_pipeline.py`

- [ ] **Step 1: Write failing runtime tests for voice-match insertion and normalized duration state**

```python
import numpy as np
from unittest.mock import patch

from appcore.events import EventBus
from appcore.runtime_ja import JapaneseTranslateRunner


def test_ja_pipeline_inserts_voice_match_after_asr():
    runner = JapaneseTranslateRunner(bus=EventBus(), user_id=1)
    steps = runner._get_pipeline_steps("task-ja", "/tmp/demo.mp4", "/tmp/out")
    names = [name for name, _fn in steps]
    assert names[names.index("asr") + 1] == "voice_match"


def test_ja_voice_match_writes_candidates_to_state():
    runner = JapaneseTranslateRunner(bus=EventBus(), user_id=1)
    task = {
        "task_dir": "/tmp/x",
        "target_lang": "ja",
        "utterances": [{"start_time": 0, "end_time": 10, "text": "hello"}],
        "video_path": "/tmp/x/src.mp4",
    }
    with patch("appcore.task_state.get", return_value=task), \
         patch("appcore.task_state.update") as m_update, \
         patch("appcore.runtime_ja.extract_sample_from_utterances", return_value="/tmp/x/clip.wav"), \
         patch("appcore.runtime_ja.embed_audio_file", return_value=np.zeros(256, dtype=np.float32)), \
         patch("appcore.runtime_ja.resolve_default_voice", return_value="ja-default"), \
         patch("appcore.runtime_ja.match_candidates", return_value=[{"voice_id": "ja-1", "similarity": 0.9}]):
        runner._step_voice_match("task-ja")

    assert m_update.call_args.kwargs["voice_match_candidates"][0]["voice_id"] == "ja-1"


def test_ja_step_tts_sets_shared_final_duration_fields(tmp_path, monkeypatch):
    runner = JapaneseTranslateRunner(bus=EventBus(), user_id=1)
    task = {
        "task_dir": str(tmp_path),
        "video_path": str(tmp_path / "src.mp4"),
        "script_segments": [{"index": 0, "text": "Store caps neatly."}],
        "variants": {"normal": {"localized_translation": {"sentences": [{"index": 0, "text": "帽子をすっきり収納", "source_segment_indices": [0]}]}}},
    }
    monkeypatch.setattr("appcore.task_state.get", lambda task_id: task)
    updates = []
    monkeypatch.setattr("appcore.task_state.update", lambda task_id, **kwargs: updates.append(kwargs))
    monkeypatch.setattr("appcore.task_state.set_artifact", lambda *args, **kwargs: None)
    monkeypatch.setattr("appcore.task_state.set_preview_file", lambda *args, **kwargs: None)
    monkeypatch.setattr("appcore.runtime_ja.get_video_duration", lambda path: 11.7)
    monkeypatch.setattr("appcore.runtime_ja._tts_final_target_range", lambda duration: (10.7, 13.7))
    monkeypatch.setattr("appcore.runtime_ja.resolve_key", lambda *args, **kwargs: "eleven-key")
    monkeypatch.setattr("appcore.runtime_ja.ja_translate.build_ja_tts_script", lambda localized: {"full_text": "帽子をすっきり収納", "blocks": [], "subtitle_chunks": []})
    monkeypatch.setattr("appcore.runtime_ja.ja_translate.build_ja_tts_segments", lambda script, segs: [{"translated": "帽子をすっきり収納"}])
    monkeypatch.setattr("appcore.runtime_ja.generate_full_audio", lambda *args, **kwargs: {"full_audio_path": str(tmp_path / "tts_full.ja_round_1.mp3"), "segments": [{"tts_duration": 11.9}]})
    monkeypatch.setattr("appcore.runtime_ja._get_audio_duration", lambda path: 11.9)
    monkeypatch.setattr("appcore.runtime_ja.build_timeline_manifest", lambda *args, **kwargs: {})
    monkeypatch.setattr("appcore.runtime_ja.build_tts_artifact", lambda *args, **kwargs: {})
    monkeypatch.setattr("appcore.runtime_ja.speech_rate_model.update_rate", lambda *args, **kwargs: None)

    runner._step_tts("task-ja", str(tmp_path))

    final_update = [u for u in updates if "tts_final_round" in u][-1]
    assert final_update["tts_final_round"] == 1
    assert final_update["tts_final_reason"] == "converged"
    assert final_update["tts_duration_status"] == "converged"
```

- [ ] **Step 2: Run the new Japanese runtime tests and confirm they fail**

Run: `python -m pytest tests/test_runtime_ja_shared_shell.py tests/test_ja_translate_pipeline.py -q`

Expected: fail because `JapaneseTranslateRunner` does not yet expose `voice_match` and still writes `tts_duration_status="done"` without the shared final fields.

- [ ] **Step 3: Add the Japanese `voice_match` stage and shared duration fields**

```python
# appcore/runtime_ja.py
def _step_voice_match(self, task_id: str) -> None:
    task = task_state.get(task_id)
    utterances = task.get("utterances") or []
    video_path = task.get("video_path")
    default_voice_id = resolve_default_voice("ja", user_id=self.user_id)

    self._set_step(task_id, "voice_match", "running", "日语音色库加载中...")
    candidates = []
    query_embedding_b64 = None
    if utterances and video_path:
        clip = extract_sample_from_utterances(video_path, utterances, out_dir=task["task_dir"], min_duration=8.0)
        vec = embed_audio_file(clip)
        candidates = match_candidates(vec, language="ja", top_k=10, exclude_voice_ids={default_voice_id} if default_voice_id else None) or []
        query_embedding_b64 = base64.b64encode(serialize_embedding(vec)).decode("ascii")

    task_state.update(task_id, voice_match_candidates=candidates, voice_match_fallback_voice_id=None if candidates else default_voice_id, voice_match_query_embedding=query_embedding_b64)
    task_state.set_current_review_step(task_id, "voice_match")
    self._set_step(task_id, "voice_match", "waiting", "日语音色库已就绪，请选择 TTS 音色")
```

```python
# appcore/runtime_ja.py
def _get_pipeline_steps(self, task_id: str, video_path: str, task_dir: str) -> list:
    steps = super()._get_pipeline_steps(task_id, video_path, task_dir)
    out = []
    for name, fn in steps:
        out.append((name, fn))
        if name == "asr":
            out.append(("voice_match", lambda: self._step_voice_match(task_id)))
    return out
```

```python
# appcore/runtime_ja.py inside _step_tts
record["artifact_paths"] = {
    "localized_translation": f"localized_translation.round_{round_index}.json",
    "localized_rewrite_messages": f"ja_localized_rewrite_messages.round_{round_index}.json",
    "initial_translate_messages": "ja_localized_translate_messages.json",
    "tts_script": f"tts_script.round_{round_index}.json",
    "tts_full_audio": f"tts_full.ja_round_{round_index}.mp3",
}

final_reason = "converged" if duration_lo <= selected["audio_duration"] <= duration_hi else "best_pick"
final_distance = 0.0 if final_reason == "converged" else min(
    abs(selected["audio_duration"] - duration_lo),
    abs(selected["audio_duration"] - duration_hi),
)

task_state.update(
    task_id,
    tts_duration_rounds=duration_rounds,
    tts_duration_status="converged",
    tts_final_round=selected["round"],
    tts_final_reason=final_reason,
    tts_final_distance=final_distance,
)
```

- [ ] **Step 4: Re-run the Japanese runtime tests**

Run: `python -m pytest tests/test_runtime_ja_shared_shell.py tests/test_ja_translate_pipeline.py -q`

Expected: Japanese runtime tests pass and the old Japanese pipeline tests still stay green.

- [ ] **Step 5: Commit the Japanese runtime normalization**

```bash
git add appcore/runtime_ja.py tests/test_runtime_ja_shared_shell.py tests/test_ja_translate_pipeline.py
git commit -m "feat(ja): add shared-shell voice-match and duration state"
```

### Task 6: Run Cross-Module Regression and Real Smoke Verification

**Files:**
- Modify: `tests/test_multi_translate_routes.py`
- Modify: `tests/test_ja_translate_routes.py`
- Modify: `tests/test_translate_detail_shell_templates.py`
- Modify: `tests/test_runtime_multi_voice_match.py`
- Modify: `tests/test_runtime_ja_shared_shell.py`

- [ ] **Step 1: Run the full targeted regression suite**

Run: `python -m pytest tests/test_translate_detail_shell_templates.py tests/test_translate_detail_protocol.py tests/test_multi_translate_routes.py tests/test_ja_translate_routes.py tests/test_ja_translate_pipeline.py tests/test_runtime_multi_skeleton.py tests/test_runtime_multi_voice_match.py tests/test_runtime_ja_shared_shell.py tests/test_bulk_translate_runtime.py -q`

Expected: all targeted tests pass with no new failures in existing multi-language behavior.

- [ ] **Step 2: Verify the shared shell still renders the multi-language page correctly**

Run: `python -m pytest tests/test_multi_translate_routes.py::test_admin_detail_can_view_other_users_multi_translate_project tests/test_multi_translate_routes.py::test_voice_selector_multi_exposes_single_frame_subtitle_preview -q`

Expected: both tests pass, confirming the shared shell did not regress the current multi-language detail page.

- [ ] **Step 3: Manually smoke-test a Japanese task on the test environment**

Use the test environment at `http://172.30.254.14:8080/` and the sample video:

`G:\BaiduSyncdisk\多平台公用文件夹\第一批视频素材\baseball-cap-organizer\2026.03.25-可堆叠棒球帽收纳盒-原素材-补充素材-B-指派-张晴.mp4`

Verify all of the following in the browser:

- `/ja-translate/<task_id>` returns `200`
- 页面顺序为“声音分离/提取 → ASR → 音色选择 → 分段 → 翻译 → Duration Loop”
- 选择第一个匹配音色后，任务从 `alignment` 继续，而不是重复前置步骤
- Duration Loop 能看到轮次、曲线和“查看 Prompt / 查看本轮译文 / 查看朗读文案”入口
- 多语言现有任务页 `/multi-translate/<task_id>` 外观和交互没有异常

- [ ] **Step 4: Capture the verification result in git status before reporting**

Run: `git status --short`

Expected: only the intended shared-shell, route, runtime, and test files are modified.

- [ ] **Step 5: Commit the final verified refactor**

```bash
git add web/templates/_translate_detail_shell.html web/templates/multi_translate_detail.html web/templates/ja_translate_detail.html web/templates/_task_workbench_scripts.html web/templates/_voice_selector_multi.html web/static/voice_selector_multi.js web/services/translate_detail_protocol.py web/routes/multi_translate.py web/routes/ja_translate.py appcore/runtime_ja.py tests/test_translate_detail_shell_templates.py tests/test_translate_detail_protocol.py tests/test_multi_translate_routes.py tests/test_ja_translate_routes.py tests/test_ja_translate_pipeline.py tests/test_runtime_multi_voice_match.py tests/test_runtime_ja_shared_shell.py tests/test_bulk_translate_runtime.py
git commit -m "feat(ja): move japanese translate onto shared detail shell"
```
