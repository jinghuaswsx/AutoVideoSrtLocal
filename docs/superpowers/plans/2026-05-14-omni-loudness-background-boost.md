# Omni Loudness Background Boost Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add per-task Omni loudness profiles for `标准`、`增强背景`、`手动调整`, then make `loudness_match` and final compose use the selected background volume safely.

**Architecture:** Put the profile math in `appcore.audio_loudness` as JSON-friendly pure helpers. Store the user choice in task state via an Omni API endpoint, then have `PipelineRunner._step_loudness_match()` resolve an `effective_background_volume` and persist it into `task.separation` for both B postmix reuse and A/compose fallback. Render the profile controls inside `_separation_card.html`, keeping Omni-only behavior guarded by `api_base`.

**Tech Stack:** Python 3.12, Flask route tests with patched `web.store`, ffmpeg wrapper runtime, Jinja template partial JavaScript, pytest.

---

## Anchors

- Spec: `docs/superpowers/specs/2026-05-14-omni-loudness-background-boost-design.md`
- Existing behavior: `docs/superpowers/plans/2026-05-05-vocal-separation-handoff.md`
- Dynamic resume: `docs/superpowers/specs/2026-05-07-omni-dynamic-resume-and-prompt-display-fix.md`
- Template rules: `web/templates/CLAUDE.md`
- Frontend token/CSRF rules: `web/static/CLAUDE.md`

## File Map

- Modify: `appcore/audio_loudness.py`
  - Owns pure constants, profile validation, automatic boost math, manual boost math, and final profile resolution.
- Create: `tests/test_loudness_background_profiles.py`
  - Fast pure tests that do not require ffmpeg.
- Modify: `web/routes/omni_translate.py`
  - Adds `POST /api/omni-translate/<task_id>/loudness-profile`.
- Modify: `tests/test_omni_translate_routes.py`
  - Route tests for all three profiles and invalid manual percentages.
- Modify: `appcore/runtime/_pipeline_runner.py`
  - Reads selected profile, prepares stable source TTS backups, uses effective background volume in B/A paths, and makes compose use `separation.effective_background_volume`.
- Create: `tests/test_runtime_loudness_profiles.py`
  - Runtime tests for manual profile resolution, source backup restore, and compose fallback volume.
- Modify: `web/templates/_separation_card.html`
  - Renders profile pills, manual percentage modal, profile save requests, and actual algorithm labels.
- Modify: `tests/test_translate_detail_shell_templates.py`
  - Static template assertions for UI labels, endpoint, CSRF, modal, and actual algorithm lookup.

---

### Task 1: Pure Loudness Profile Helpers

**Files:**
- Modify: `appcore/audio_loudness.py`
- Create: `tests/test_loudness_background_profiles.py`

- [ ] **Step 1: Write failing pure helper tests**

Create `tests/test_loudness_background_profiles.py`:

```python
import math

import pytest

from appcore.audio_loudness import (
    BOOST_MAX_BACKGROUND_VOLUME,
    LOUDNESS_PROFILE_AUTO_BOOST,
    LOUDNESS_PROFILE_MANUAL_BOOST,
    LOUDNESS_PROFILE_STANDARD,
    resolve_background_volume_profile,
    validate_loudness_profile,
)


def test_standard_profile_uses_current_background_volume():
    result = resolve_background_volume_profile(
        LOUDNESS_PROFILE_STANDARD,
        standard_volume=0.8,
        accompaniment_lufs=-24.0,
        tts_reference_lufs=-13.0,
    )

    assert result["profile"] == LOUDNESS_PROFILE_STANDARD
    assert result["background_volume"] == 0.8
    assert result["effective_background_volume"] == 0.8
    assert result["background_boost"]["enabled"] is False
    assert result["manual_boost"]["enabled"] is False


def test_auto_boost_raises_background_toward_target_gap_and_caps():
    result = resolve_background_volume_profile(
        LOUDNESS_PROFILE_AUTO_BOOST,
        standard_volume=0.8,
        accompaniment_lufs=-24.0,
        tts_reference_lufs=-13.0,
    )

    assert result["profile"] == LOUDNESS_PROFILE_AUTO_BOOST
    assert result["background_boost"]["enabled"] is True
    assert result["background_boost"]["target_gap_lu"] == 10.0
    assert math.isclose(result["background_boost"]["raw_volume"], 0.8 * (10 ** (1 / 20)), rel_tol=1e-6)
    assert result["effective_background_volume"] > 0.8
    assert result["effective_background_volume"] <= BOOST_MAX_BACKGROUND_VOLUME


def test_auto_boost_caps_at_max_volume():
    result = resolve_background_volume_profile(
        LOUDNESS_PROFILE_AUTO_BOOST,
        standard_volume=1.2,
        accompaniment_lufs=-35.0,
        tts_reference_lufs=-13.0,
    )

    assert result["effective_background_volume"] == BOOST_MAX_BACKGROUND_VOLUME
    assert result["background_boost"]["capped"] is True


def test_auto_boost_near_silent_accompaniment_falls_back_to_standard():
    result = resolve_background_volume_profile(
        LOUDNESS_PROFILE_AUTO_BOOST,
        standard_volume=0.8,
        accompaniment_lufs=-70.0,
        tts_reference_lufs=-13.0,
    )

    assert result["effective_background_volume"] == 0.8
    assert result["background_boost"]["enabled"] is False
    assert result["background_boost"]["fallback_reason"] == "accompaniment_near_silence"


@pytest.mark.parametrize(
    ("pct", "expected"),
    [(10, 0.88), (50, 1.2), (100, 1.6)],
)
def test_manual_boost_scales_standard_volume_linearly(pct, expected):
    result = resolve_background_volume_profile(
        LOUDNESS_PROFILE_MANUAL_BOOST,
        standard_volume=0.8,
        manual_boost_pct=pct,
    )

    assert result["profile"] == LOUDNESS_PROFILE_MANUAL_BOOST
    assert result["manual_boost"]["enabled"] is True
    assert result["manual_boost"]["boost_pct"] == pct
    assert math.isclose(result["effective_background_volume"], expected, rel_tol=1e-9)


def test_manual_boost_caps_at_max_volume():
    result = resolve_background_volume_profile(
        LOUDNESS_PROFILE_MANUAL_BOOST,
        standard_volume=1.2,
        manual_boost_pct=100,
    )

    assert result["manual_boost"]["raw_volume"] == 2.4
    assert result["effective_background_volume"] == BOOST_MAX_BACKGROUND_VOLUME
    assert result["manual_boost"]["capped"] is True


@pytest.mark.parametrize("pct", [0, 5, 55, 101, "abc", None])
def test_validate_loudness_profile_rejects_invalid_manual_pct(pct):
    with pytest.raises(ValueError):
        validate_loudness_profile(LOUDNESS_PROFILE_MANUAL_BOOST, pct)


def test_validate_loudness_profile_normalizes_non_manual_profiles():
    assert validate_loudness_profile(None, None) == (LOUDNESS_PROFILE_STANDARD, None)
    assert validate_loudness_profile(LOUDNESS_PROFILE_AUTO_BOOST, None) == (
        LOUDNESS_PROFILE_AUTO_BOOST,
        None,
    )
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
pytest tests/test_loudness_background_profiles.py -q
```

Expected: import failure for `BOOST_MAX_BACKGROUND_VOLUME` or `resolve_background_volume_profile`.

- [ ] **Step 3: Add constants and helper functions**

Append these definitions after `EBUR128_FLOOR = -70.0` in `appcore/audio_loudness.py`:

```python
LOUDNESS_PROFILE_STANDARD = "standard"
LOUDNESS_PROFILE_AUTO_BOOST = "bg_boost"
LOUDNESS_PROFILE_MANUAL_BOOST = "manual_boost"
LOUDNESS_PROFILES = {
    LOUDNESS_PROFILE_STANDARD,
    LOUDNESS_PROFILE_AUTO_BOOST,
    LOUDNESS_PROFILE_MANUAL_BOOST,
}

BOOST_TARGET_GAP_LU = 10.0
BOOST_MAX_BACKGROUND_VOLUME = 1.8
DEFAULT_MANUAL_BOOST_PCT = 50
```

Append these helper functions above `measure_integrated_lufs()`:

```python
def validate_loudness_profile(
    profile: str | None,
    manual_boost_pct: object | None,
) -> tuple[str, int | None]:
    """Validate and normalize per-task loudness profile input."""
    normalized = (profile or LOUDNESS_PROFILE_STANDARD).strip()
    if normalized not in LOUDNESS_PROFILES:
        raise ValueError(
            "loudness_profile must be one of: standard, bg_boost, manual_boost"
        )
    if normalized != LOUDNESS_PROFILE_MANUAL_BOOST:
        return normalized, None

    try:
        pct = int(manual_boost_pct)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError("manual_boost_pct must be one of 10, 20, 30, 40, 50, 60, 70, 80, 90, 100") from exc
    if pct < 10 or pct > 100 or pct % 10 != 0:
        raise ValueError("manual_boost_pct must be one of 10, 20, 30, 40, 50, 60, 70, 80, 90, 100")
    return normalized, pct


def _empty_background_boost_summary(reason: str) -> dict:
    return {
        "enabled": False,
        "mode": "auto",
        "fallback_reason": reason,
        "target_gap_lu": BOOST_TARGET_GAP_LU,
        "max_volume": BOOST_MAX_BACKGROUND_VOLUME,
    }


def _empty_manual_boost_summary(reason: str = "profile_not_manual_boost") -> dict:
    return {
        "enabled": False,
        "mode": "manual",
        "fallback_reason": reason,
        "max_volume": BOOST_MAX_BACKGROUND_VOLUME,
    }


def resolve_background_volume_profile(
    profile: str | None,
    *,
    standard_volume: float,
    accompaniment_lufs: float | None = None,
    tts_reference_lufs: float | None = None,
    manual_boost_pct: object | None = None,
) -> dict:
    """Resolve selected loudness profile into a concrete background volume.

    The returned dict is stored directly in task state, so keep it JSON-friendly.
    """
    normalized_profile, normalized_manual_pct = validate_loudness_profile(
        profile, manual_boost_pct,
    )
    standard = float(standard_volume)
    result = {
        "profile": normalized_profile,
        "manual_boost_pct": normalized_manual_pct,
        "background_volume": standard,
        "effective_background_volume": standard,
        "background_boost": _empty_background_boost_summary("profile_not_bg_boost"),
        "manual_boost": _empty_manual_boost_summary(),
    }

    if normalized_profile == LOUDNESS_PROFILE_STANDARD:
        return result

    if normalized_profile == LOUDNESS_PROFILE_MANUAL_BOOST:
        pct = normalized_manual_pct if normalized_manual_pct is not None else DEFAULT_MANUAL_BOOST_PCT
        raw_volume = standard * (1.0 + (pct / 100.0))
        effective = min(BOOST_MAX_BACKGROUND_VOLUME, raw_volume)
        result["effective_background_volume"] = effective
        result["manual_boost"] = {
            "enabled": True,
            "mode": "manual",
            "boost_pct": pct,
            "standard_volume": standard,
            "raw_volume": raw_volume,
            "effective_volume": effective,
            "max_volume": BOOST_MAX_BACKGROUND_VOLUME,
            "capped": effective < raw_volume,
        }
        return result

    if accompaniment_lufs is None:
        result["background_boost"] = _empty_background_boost_summary(
            "accompaniment_lufs_unavailable"
        )
        return result
    if is_likely_silence(float(accompaniment_lufs)):
        result["background_boost"] = _empty_background_boost_summary(
            "accompaniment_near_silence"
        )
        return result
    if tts_reference_lufs is None:
        result["background_boost"] = _empty_background_boost_summary(
            "tts_reference_lufs_unavailable"
        )
        return result

    target_bg_lufs = float(tts_reference_lufs) - BOOST_TARGET_GAP_LU
    needed_gain_lu = target_bg_lufs - float(accompaniment_lufs)
    raw_volume = standard * (10 ** (needed_gain_lu / 20.0))
    boosted = max(standard, raw_volume)
    effective = min(BOOST_MAX_BACKGROUND_VOLUME, boosted)
    result["effective_background_volume"] = effective
    result["background_boost"] = {
        "enabled": True,
        "mode": "auto",
        "standard_volume": standard,
        "target_gap_lu": BOOST_TARGET_GAP_LU,
        "max_volume": BOOST_MAX_BACKGROUND_VOLUME,
        "accompaniment_lufs": float(accompaniment_lufs),
        "tts_reference_lufs": float(tts_reference_lufs),
        "target_bg_lufs": target_bg_lufs,
        "needed_gain_lu": needed_gain_lu,
        "raw_volume": raw_volume,
        "effective_volume": effective,
        "capped": effective < boosted,
        "fallback_reason": "",
    }
    return result
```

- [ ] **Step 4: Run helper tests**

Run:

```bash
pytest tests/test_loudness_background_profiles.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit helper layer**

Run:

```bash
git add appcore/audio_loudness.py tests/test_loudness_background_profiles.py
git commit -m "feat(audio): add loudness background profile helpers" -m "Docs-anchor: docs/superpowers/specs/2026-05-14-omni-loudness-background-boost-design.md#自动增强算法"
```

---

### Task 2: Omni Loudness Profile API

**Files:**
- Modify: `web/routes/omni_translate.py`
- Modify: `tests/test_omni_translate_routes.py`

- [ ] **Step 1: Write failing route tests**

Append these tests before the source-language section in `tests/test_omni_translate_routes.py`:

```python
def test_loudness_profile_route_saves_standard_without_starting_runner(
    authed_client_no_db,
):
    fake_task = {
        "_user_id": 1,
        "loudness_profile": "bg_boost",
        "separation": {"tts_loudness": {"profile": "bg_boost"}},
    }
    with patch("web.routes.omni_translate.store") as mock_store, \
         patch("web.routes.omni_translate.omni_pipeline_runner") as mock_runner:
        mock_store.get.return_value = fake_task
        resp = authed_client_no_db.post(
            "/api/omni-translate/t-1/loudness-profile",
            json={"profile": "standard"},
        )

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["profile"] == "standard"
    assert body["manual_boost_pct"] is None
    assert body["applied_profile"] == "bg_boost"
    assert body["needs_resume"] is True
    mock_store.update.assert_called_once_with(
        "t-1",
        loudness_profile="standard",
        loudness_manual_boost_pct=None,
    )
    mock_runner.resume.assert_not_called()


def test_loudness_profile_route_saves_manual_boost_pct(authed_client_no_db):
    fake_task = {
        "_user_id": 1,
        "separation": {"tts_loudness": {"profile": "standard"}},
    }
    with patch("web.routes.omni_translate.store") as mock_store:
        mock_store.get.return_value = fake_task
        resp = authed_client_no_db.post(
            "/api/omni-translate/t-1/loudness-profile",
            json={"profile": "manual_boost", "manual_boost_pct": 50},
        )

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["profile"] == "manual_boost"
    assert body["manual_boost_pct"] == 50
    assert body["applied_profile"] == "standard"
    assert body["applied_manual_boost_pct"] is None
    assert body["needs_resume"] is True
    mock_store.update.assert_called_once_with(
        "t-1",
        loudness_profile="manual_boost",
        loudness_manual_boost_pct=50,
    )


@pytest.mark.parametrize("pct", [0, 5, 55, 101, "abc", None])
def test_loudness_profile_route_rejects_invalid_manual_pct(
    authed_client_no_db, pct,
):
    fake_task = {"_user_id": 1}
    with patch("web.routes.omni_translate.store") as mock_store:
        mock_store.get.return_value = fake_task
        resp = authed_client_no_db.post(
            "/api/omni-translate/t-1/loudness-profile",
            json={"profile": "manual_boost", "manual_boost_pct": pct},
        )

    assert resp.status_code == 400
    assert "manual_boost_pct" in resp.get_json()["error"]
    mock_store.update.assert_not_called()


def test_loudness_profile_route_rejects_unknown_profile(authed_client_no_db):
    fake_task = {"_user_id": 1}
    with patch("web.routes.omni_translate.store") as mock_store:
        mock_store.get.return_value = fake_task
        resp = authed_client_no_db.post(
            "/api/omni-translate/t-1/loudness-profile",
            json={"profile": "louder"},
        )

    assert resp.status_code == 400
    assert "loudness_profile" in resp.get_json()["error"]
    mock_store.update.assert_not_called()
```

- [ ] **Step 2: Run route tests and verify failure**

Run:

```bash
pytest tests/test_omni_translate_routes.py::test_loudness_profile_route_saves_standard_without_starting_runner tests/test_omni_translate_routes.py::test_loudness_profile_route_saves_manual_boost_pct tests/test_omni_translate_routes.py::test_loudness_profile_route_rejects_invalid_manual_pct tests/test_omni_translate_routes.py::test_loudness_profile_route_rejects_unknown_profile -q
```

Expected: 404 because the route does not exist.

- [ ] **Step 3: Add route helper and endpoint**

Add this import near the other appcore imports in `web/routes/omni_translate.py`:

```python
from appcore.audio_loudness import validate_loudness_profile
```

Add these helpers after `_resume_cleanup_updates()`:

```python
def _applied_loudness_profile(task: dict) -> tuple[str | None, int | None]:
    tl = ((task.get("separation") or {}).get("tts_loudness") or {})
    applied_profile = tl.get("profile")
    applied_manual_pct = tl.get("manual_boost_pct")
    return applied_profile, applied_manual_pct


def _loudness_profile_needs_resume(
    *,
    selected_profile: str,
    selected_manual_pct: int | None,
    applied_profile: str | None,
    applied_manual_pct: int | None,
) -> bool:
    if applied_profile != selected_profile:
        return True
    if selected_profile == "manual_boost":
        return applied_manual_pct != selected_manual_pct
    return False
```

Add this route immediately before the existing `/resume` route:

```python
@bp.route("/api/omni-translate/<task_id>/loudness-profile", methods=["POST"])
@login_required
def set_loudness_profile(task_id):
    recover_task_if_needed(task_id)
    task = _get_viewable_task(task_id)
    if not task:
        return _json_response({"error": "Task not found"}, 404)

    body = request.get_json(silent=True) or {}
    try:
        profile, manual_pct = validate_loudness_profile(
            body.get("profile"),
            body.get("manual_boost_pct"),
        )
    except ValueError as exc:
        return _json_response({"error": str(exc)}, 400)

    applied_profile, applied_manual_pct = _applied_loudness_profile(task)
    store.update(
        task_id,
        loudness_profile=profile,
        loudness_manual_boost_pct=manual_pct,
    )
    return _json_response({
        "status": "ok",
        "profile": profile,
        "manual_boost_pct": manual_pct,
        "applied_profile": applied_profile,
        "applied_manual_boost_pct": applied_manual_pct,
        "needs_resume": _loudness_profile_needs_resume(
            selected_profile=profile,
            selected_manual_pct=manual_pct,
            applied_profile=applied_profile,
            applied_manual_pct=applied_manual_pct,
        ),
    })
```

- [ ] **Step 4: Run route tests**

Run:

```bash
pytest tests/test_omni_translate_routes.py::test_loudness_profile_route_saves_standard_without_starting_runner tests/test_omni_translate_routes.py::test_loudness_profile_route_saves_manual_boost_pct tests/test_omni_translate_routes.py::test_loudness_profile_route_rejects_invalid_manual_pct tests/test_omni_translate_routes.py::test_loudness_profile_route_rejects_unknown_profile -q
```

Expected: all selected route tests pass.

- [ ] **Step 5: Commit route endpoint**

Run:

```bash
git add web/routes/omni_translate.py tests/test_omni_translate_routes.py
git commit -m "feat(omni): save loudness profile selection" -m "Docs-anchor: docs/superpowers/specs/2026-05-14-omni-loudness-background-boost-design.md#后端接口"
```

---

### Task 3: Runtime Loudness Profile Integration

**Files:**
- Modify: `appcore/runtime/_pipeline_runner.py`
- Create: `tests/test_runtime_loudness_profiles.py`

- [ ] **Step 1: Write failing runtime tests**

Create `tests/test_runtime_loudness_profiles.py`:

```python
from __future__ import annotations

import os
from types import SimpleNamespace

from appcore.event_bus import EventBus
from appcore.runtime import PipelineRunner


def _disable_task_state_db(monkeypatch):
    import appcore.task_state as task_state

    monkeypatch.setattr(task_state, "_db_upsert", lambda *args, **kwargs: None)
    monkeypatch.setattr(task_state, "_sync_task_to_db", lambda *args, **kwargs: None)
    monkeypatch.setattr(task_state, "set_expires_at", lambda *args, **kwargs: None)
    return task_state


def _write(path, text):
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_loudness_match_manual_profile_writes_effective_volume_and_summary(
    monkeypatch, tmp_path,
):
    task_state = _disable_task_state_db(monkeypatch)
    runner = PipelineRunner(bus=EventBus(), user_id=1)
    task_id = "manual-loudness-profile"
    task_dir = tmp_path / task_id
    task_dir.mkdir()
    audio_path = _write(task_dir / "tts.mp3", "original")
    accomp_path = _write(task_dir / "accompaniment.wav", "background")

    task_state.create(task_id, "video.mp4", str(task_dir), user_id=1)
    task_state.update(
        task_id,
        separation={
            "status": "done",
            "vocals_lufs": -13.0,
            "video_lufs": None,
            "accompaniment_path": accomp_path,
        },
        variants={"normal": {"tts_audio_path": audio_path}},
        loudness_profile="manual_boost",
        loudness_manual_boost_pct=50,
    )

    monkeypatch.setattr(
        "pipeline.audio_separation.load_settings",
        lambda: SimpleNamespace(background_volume=0.8),
    )

    def fake_normalize(input_path, output_path, *, target_lufs):
        assert open(input_path, encoding="utf-8").read() == "original"
        with open(output_path, "w", encoding="utf-8") as fh:
            fh.write("normalized")
        return SimpleNamespace(
            input_lufs=-20.0,
            target_lufs=target_lufs,
            output_lufs=target_lufs,
            deviation_lu=0.0,
            deviation_pct=0.0,
            converged=True,
        )

    monkeypatch.setattr("appcore.audio_loudness.normalize_to_lufs", fake_normalize)

    runner._step_loudness_match(task_id, str(task_dir))

    task = task_state.get(task_id)
    sep = task["separation"]
    assert sep["effective_background_volume"] == 1.2
    assert sep["tts_loudness"]["profile"] == "manual_boost"
    assert sep["tts_loudness"]["manual_boost_pct"] == 50
    assert sep["tts_loudness"]["manual_boost"]["effective_volume"] == 1.2
    assert sep["tts_loudness"]["variants"][0]["source_backup_origin"] == "current_tts_audio"
    assert os.path.isfile(task_dir / "loudness_match" / "source.normal.mp3")


def test_loudness_match_auto_profile_measures_accompaniment_when_missing(
    monkeypatch, tmp_path,
):
    task_state = _disable_task_state_db(monkeypatch)
    runner = PipelineRunner(bus=EventBus(), user_id=1)
    task_id = "auto-loudness-profile"
    task_dir = tmp_path / task_id
    task_dir.mkdir()
    audio_path = _write(task_dir / "tts.mp3", "original")
    accomp_path = _write(task_dir / "accompaniment.wav", "background")

    task_state.create(task_id, "video.mp4", str(task_dir), user_id=1)
    task_state.update(
        task_id,
        separation={
            "status": "done",
            "vocals_lufs": -13.0,
            "video_lufs": None,
            "accompaniment_path": accomp_path,
        },
        variants={"normal": {"tts_audio_path": audio_path}},
        loudness_profile="bg_boost",
    )
    monkeypatch.setattr(
        "pipeline.audio_separation.load_settings",
        lambda: SimpleNamespace(background_volume=0.8),
    )

    def fake_measure(path):
        assert path == accomp_path
        return -24.0

    def fake_normalize(input_path, output_path, *, target_lufs):
        with open(output_path, "w", encoding="utf-8") as fh:
            fh.write("normalized")
        return SimpleNamespace(
            input_lufs=-20.0,
            target_lufs=target_lufs,
            output_lufs=target_lufs,
            deviation_lu=0.0,
            deviation_pct=0.0,
            converged=True,
        )

    monkeypatch.setattr("appcore.audio_loudness.measure_integrated_lufs", fake_measure)
    monkeypatch.setattr("appcore.audio_loudness.normalize_to_lufs", fake_normalize)

    runner._step_loudness_match(task_id, str(task_dir))

    sep = task_state.get(task_id)["separation"]
    assert sep["accompaniment_lufs"] == -24.0
    assert sep["effective_background_volume"] > 0.8
    assert sep["tts_loudness"]["profile"] == "bg_boost"
    assert sep["tts_loudness"]["background_boost"]["enabled"] is True
    assert sep["tts_loudness"]["background_boost"]["accompaniment_lufs"] == -24.0


def test_loudness_match_restores_source_backup_on_repeat_run(monkeypatch, tmp_path):
    task_state = _disable_task_state_db(monkeypatch)
    runner = PipelineRunner(bus=EventBus(), user_id=1)
    task_id = "repeat-loudness-profile"
    task_dir = tmp_path / task_id
    task_dir.mkdir()
    audio_path = _write(task_dir / "tts.mp3", "original")
    accomp_path = _write(task_dir / "accompaniment.wav", "background")

    task_state.create(task_id, "video.mp4", str(task_dir), user_id=1)
    task_state.update(
        task_id,
        separation={
            "status": "done",
            "vocals_lufs": -13.0,
            "video_lufs": None,
            "accompaniment_path": accomp_path,
        },
        variants={"normal": {"tts_audio_path": audio_path}},
        loudness_profile="manual_boost",
        loudness_manual_boost_pct=50,
    )
    monkeypatch.setattr(
        "pipeline.audio_separation.load_settings",
        lambda: SimpleNamespace(background_volume=0.8),
    )

    seen_inputs = []

    def fake_normalize(input_path, output_path, *, target_lufs):
        seen_inputs.append(open(input_path, encoding="utf-8").read())
        with open(output_path, "w", encoding="utf-8") as fh:
            fh.write("normalized")
        return SimpleNamespace(
            input_lufs=-20.0,
            target_lufs=target_lufs,
            output_lufs=target_lufs,
            deviation_lu=0.0,
            deviation_pct=0.0,
            converged=True,
        )

    monkeypatch.setattr("appcore.audio_loudness.normalize_to_lufs", fake_normalize)

    runner._step_loudness_match(task_id, str(task_dir))
    runner._step_loudness_match(task_id, str(task_dir))

    assert seen_inputs == ["original", "original"]


def test_compose_fallback_uses_effective_background_volume(monkeypatch, tmp_path):
    task_state = _disable_task_state_db(monkeypatch)
    runner = PipelineRunner(bus=EventBus(), user_id=1)
    task_id = "compose-effective-volume"
    task_dir = tmp_path / task_id
    task_dir.mkdir()
    tts_path = _write(task_dir / "tts.mp3", "tts")
    accomp_path = _write(task_dir / "accompaniment.wav", "background")

    task_state.create(task_id, "video.mp4", str(task_dir), user_id=1)
    task_state.update(
        task_id,
        separation={
            "status": "done",
            "vocals_lufs": -13.0,
            "accompaniment_path": accomp_path,
            "effective_background_volume": 1.2,
            "tts_loudness": {"variants": [{"variant": "normal"}]},
        },
    )
    monkeypatch.setattr(
        "pipeline.audio_separation.load_settings",
        lambda: SimpleNamespace(background_volume=0.8),
    )
    captured = {}

    def fake_mix_with_background(**kwargs):
        captured.update(kwargs)
        with open(kwargs["output_path"], "w", encoding="utf-8") as fh:
            fh.write("mixed")
        return kwargs["output_path"]

    monkeypatch.setattr("appcore.audio_loudness.mix_with_background", fake_mix_with_background)

    result = runner._maybe_mix_background_for_compose(
        task_id,
        tts_audio_path=tts_path,
        task_dir=str(task_dir),
        variant="normal",
    )

    assert result.endswith("final_audio_mixed.normal.wav")
    assert captured["background_volume"] == 1.2
```

- [ ] **Step 2: Run runtime tests and verify failure**

Run:

```bash
pytest tests/test_runtime_loudness_profiles.py -q
```

Expected: failures because `effective_background_volume` is not written, source backups are not stable, and compose uses global `background_volume`.

- [ ] **Step 3: Add source-backup and profile helper methods to `PipelineRunner`**

Add these methods before `_step_loudness_match()` in `appcore/runtime/_pipeline_runner.py`:

```python
    def _clear_loudness_source_backups(self, task_dir: str) -> None:
        loudness_dir = os.path.join(task_dir, "loudness_match")
        if not os.path.isdir(loudness_dir):
            return
        for name in os.listdir(loudness_dir):
            if name.startswith("source.") and name.endswith(".mp3"):
                try:
                    os.unlink(os.path.join(loudness_dir, name))
                except OSError:
                    log.warning("[loudness_match] failed to remove old source backup: %s", name)

    def _prepare_loudness_source_audio(
        self,
        *,
        audio_path: str,
        loudness_dir: str,
        variant_name: str,
    ) -> str:
        import shutil

        source_path = os.path.join(loudness_dir, f"source.{variant_name}.mp3")
        if os.path.isfile(source_path):
            shutil.copy2(source_path, audio_path)
            return "existing_source_backup"
        shutil.copy2(audio_path, source_path)
        return "current_tts_audio"
```

Add this call near the top of `_step_tts()` after `task = task_state.get(task_id)`:

```python
        self._clear_loudness_source_backups(task_dir)
```

- [ ] **Step 4: Resolve selected profile in `_step_loudness_match()`**

Inside `_step_loudness_match()`, extend the `appcore.audio_loudness` import to include:

```python
            resolve_background_volume_profile,
```

Immediately after `bg_volume = float(settings.background_volume)`, insert:

```python
        accompaniment_lufs = separation.get("accompaniment_lufs")
        if accompaniment_lufs is None and os.path.isfile(accompaniment_path):
            try:
                accompaniment_lufs = measure_integrated_lufs(accompaniment_path)
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "[loudness_match] accompaniment LUFS measure failed: %s", exc,
                )
        profile_summary = resolve_background_volume_profile(
            task.get("loudness_profile"),
            standard_volume=bg_volume,
            accompaniment_lufs=accompaniment_lufs,
            tts_reference_lufs=separation.get("vocals_lufs"),
            manual_boost_pct=task.get("loudness_manual_boost_pct"),
        )
        effective_bg_volume = float(profile_summary["effective_background_volume"])
```

In the `_b_overall_match()` call inside `_step_loudness_match()`, replace the existing `bg_volume=bg_volume` argument with:

```python
                        bg_volume=effective_bg_volume,
```

Before the existing temporary backup block in the variants loop, call the stable source helper:

```python
            source_backup_origin = self._prepare_loudness_source_audio(
                audio_path=audio_path,
                loudness_dir=loudness_dir,
                variant_name=variant_name,
            )
```

After `summary["variant"] = variant_name`, add:

```python
            summary["source_backup_origin"] = source_backup_origin
```

When constructing `separation["tts_loudness"]`, include:

```python
            "profile": profile_summary["profile"],
            "manual_boost_pct": profile_summary["manual_boost_pct"],
            "background_volume": profile_summary["background_volume"],
            "effective_background_volume": effective_bg_volume,
            "background_boost": profile_summary["background_boost"],
            "manual_boost": profile_summary["manual_boost"],
```

Replace the existing `separation["background_volume"] = bg_volume` assignment with:

```python
        separation["background_volume"] = profile_summary["background_volume"]
        separation["effective_background_volume"] = effective_bg_volume
        if accompaniment_lufs is not None:
            separation["accompaniment_lufs"] = accompaniment_lufs
```

- [ ] **Step 5: Make compose fallback use effective volume**

In `_maybe_mix_background_for_compose()`, replace:

```python
        settings = sep.load_settings()
```

with:

```python
        settings = sep.load_settings()
        background_volume = float(
            separation.get("effective_background_volume")
            or separation.get("background_volume")
            or settings.background_volume
        )
```

Replace `background_volume=settings.background_volume` with:

```python
                background_volume=background_volume,
```

Replace the log argument and state update:

```python
            settings.background_volume, mixed_path,
```

with:

```python
            background_volume, mixed_path,
```

and:

```python
        separation["background_volume"] = settings.background_volume
```

with:

```python
        separation["effective_background_volume"] = background_volume
```

- [ ] **Step 6: Run runtime tests**

Run:

```bash
pytest tests/test_runtime_loudness_profiles.py tests/test_loudness_background_profiles.py -q
```

Expected: all tests pass.

- [ ] **Step 7: Commit runtime integration**

Run:

```bash
git add appcore/runtime/_pipeline_runner.py tests/test_runtime_loudness_profiles.py tests/test_loudness_background_profiles.py
git commit -m "feat(omni): apply loudness profiles at runtime" -m "Docs-anchor: docs/superpowers/specs/2026-05-14-omni-loudness-background-boost-design.md#运行时设计"
```

---

### Task 4: Loudness Card UI

**Files:**
- Modify: `web/templates/_separation_card.html`
- Modify: `tests/test_translate_detail_shell_templates.py`

- [ ] **Step 1: Write failing static UI tests**

Append this test to `tests/test_translate_detail_shell_templates.py`:

```python
def test_loudness_card_exposes_profile_controls_and_actual_algorithm():
    root = Path(__file__).resolve().parents[1]
    separation = (root / "web" / "templates" / "_separation_card.html").read_text(encoding="utf-8")

    assert "loudness-profile-controls" in separation
    assert "标准" in separation
    assert "增强背景" in separation
    assert "手动调整" in separation
    assert "manualBoostModal" in separation
    assert "manual_boost_pct" in separation
    assert "/loudness-profile" in separation
    assert "X-CSRFToken" in separation
    assert "primary.algorithm || tl.algorithm" in separation
    assert "A_after_B_excess_deviation" in separation
    assert "已选择，点击“从此步继续”后生效" in separation
```

- [ ] **Step 2: Run static UI test and verify failure**

Run:

```bash
pytest tests/test_translate_detail_shell_templates.py::test_loudness_card_exposes_profile_controls_and_actual_algorithm -q
```

Expected: failure on missing `loudness-profile-controls`.

- [ ] **Step 3: Add profile state and API helpers in `_separation_card.html`**

Inside the script block, after `var separation = {{ sep|tojson }} || null;`, add:

```javascript
  var selectedLoudnessProfile = {{ (state.loudness_profile or 'standard')|tojson }};
  var selectedManualBoostPct = Number({{ (state.loudness_manual_boost_pct or 50)|tojson }}) || 50;
  var loudnessProfileSaving = false;

  function csrfToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute("content") : "";
  }

  function profileLabel(profile, manualPct) {
    if (profile === "bg_boost") return "增强背景";
    if (profile === "manual_boost") return "手动 +" + manualPct + "%";
    return "标准";
  }

  function appliedProfile(tl) {
    return (tl && tl.profile) ? tl.profile : null;
  }

  function appliedManualPct(tl) {
    return (tl && tl.manual_boost_pct !== null && tl.manual_boost_pct !== undefined)
      ? Number(tl.manual_boost_pct) : null;
  }

  function profileNeedsResume(tl) {
    var applied = appliedProfile(tl);
    if (!applied) return true;
    if (applied !== selectedLoudnessProfile) return true;
    if (selectedLoudnessProfile === "manual_boost") {
      return appliedManualPct(tl) !== Number(selectedManualBoostPct);
    }
    return false;
  }
```

Add save and modal functions after those helpers:

```javascript
  function saveLoudnessProfile(profile, manualPct) {
    if (loudnessProfileSaving) return Promise.resolve();
    loudnessProfileSaving = true;
    var payload = { profile: profile };
    if (profile === "manual_boost") payload.manual_boost_pct = Number(manualPct);
    return fetch(API_BASE + "/" + TASK_ID + "/loudness-profile", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": csrfToken(),
      },
      body: JSON.stringify(payload),
    }).then(function (r) {
      return r.json().then(function (data) {
        if (!r.ok) throw new Error(data.error || "保存响度方案失败");
        selectedLoudnessProfile = data.profile || profile;
        selectedManualBoostPct = Number(data.manual_boost_pct || manualPct || 50);
        refreshAll();
      });
    }).catch(function (err) {
      var preview = document.getElementById("preview-loudness_match");
      if (preview) {
        preview.dataset.profileError = (err && err.message) ? err.message : "保存响度方案失败";
      }
      refreshAll();
    }).finally(function () {
      loudnessProfileSaving = false;
    });
  }

  function openManualBoostModal() {
    var existing = document.getElementById("manualBoostModal");
    if (existing) existing.remove();
    var options = [10,20,30,40,50,60,70,80,90,100].map(function (pct) {
      var active = pct === Number(selectedManualBoostPct) ? " active" : "";
      return '<button type="button" class="manual-boost-option' + active + '" data-pct="' + pct + '">+' + pct + '%</button>';
    }).join("");
    var wrap = document.createElement("div");
    wrap.id = "manualBoostModal";
    wrap.className = "manual-boost-modal";
    wrap.innerHTML =
      '<div class="manual-boost-dialog" role="dialog" aria-modal="true" aria-label="手动调整背景音">' +
        '<div class="manual-boost-title">手动调整背景音</div>' +
        '<div class="manual-boost-options">' + options + '</div>' +
        '<div class="manual-boost-actions">' +
          '<button type="button" class="manual-boost-cancel">取消</button>' +
          '<button type="button" class="manual-boost-confirm">确认</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(wrap);
    var pendingPct = Number(selectedManualBoostPct) || 50;
    wrap.addEventListener("click", function (e) {
      var option = e.target.closest(".manual-boost-option");
      if (option) {
        pendingPct = Number(option.dataset.pct);
        wrap.querySelectorAll(".manual-boost-option").forEach(function (btn) {
          btn.classList.toggle("active", Number(btn.dataset.pct) === pendingPct);
        });
        return;
      }
      if (e.target.closest(".manual-boost-cancel") || e.target === wrap) {
        wrap.remove();
        return;
      }
      if (e.target.closest(".manual-boost-confirm")) {
        wrap.remove();
        saveLoudnessProfile("manual_boost", pendingPct);
      }
    });
  }
```

- [ ] **Step 4: Render profile pills and status text**

Add this function before `renderLoudnessMatch(sep)`:

```javascript
  function renderLoudnessProfileControls(tl) {
    var buttons = [
      { profile: "standard", label: "标准", hint: "按当前背景音量混入，优先保证配音清晰。" },
      { profile: "bg_boost", label: "增强背景", hint: "自动提高 BGM/环境音，最高 1.8，避免盖住配音。" },
      { profile: "manual_boost", label: "手动调整", hint: "按所选比例提高背景音，最高 1.8。" },
    ].map(function (item) {
      var active = selectedLoudnessProfile === item.profile ? " active" : "";
      var pct = item.profile === "manual_boost" ? ' data-manual-pct="' + selectedManualBoostPct + '"' : "";
      return '<button type="button" class="loudness-profile-pill' + active + '" data-profile="' + item.profile + '"' + pct + '>' +
        item.label +
      '</button>';
    }).join("");
    var status = profileNeedsResume(tl)
      ? "已选择，点击“从此步继续”后生效。"
      : "当前结果已按此方案生成。";
    return '<div class="loudness-profile-controls">' +
      '<div class="loudness-profile-pills">' + buttons + '</div>' +
      '<div class="loudness-profile-hint">' + profileLabel(selectedLoudnessProfile, selectedManualBoostPct) + '：' + status + '</div>' +
    '</div>';
  }
```

Inside `renderLoudnessMatch(sep)`, render controls before the existing detail. If `tl` is missing, render only the controls:

```javascript
    if (!tl || !tl.variants || !tl.variants.length) {
      setHtmlIfChanged(preview, renderLoudnessProfileControls(tl));
      return;
    }
```

At the final `setHtmlIfChanged()` call inside `renderLoudnessMatch()`, prepend the controls:

```javascript
    setHtmlIfChanged(preview, '<div class="loudness-detail">' +
        renderLoudnessProfileControls(tl) +
        originHint +
        '<div class="loudness-algo-row">' + algoBadge + '</div>' +
        coreRows +
      '</div>');
```

Add a click listener after `refreshAll();`:

```javascript
  document.addEventListener("click", function (e) {
    var pill = e.target.closest("#preview-loudness_match .loudness-profile-pill");
    if (!pill) return;
    var profile = pill.dataset.profile;
    if (profile === "manual_boost") {
      openManualBoostModal();
      return;
    }
    saveLoudnessProfile(profile, null);
  });
```

In `pollLatest()`, update selected task-level fields:

```javascript
        if (task) {
          if (task.separation) separation = task.separation;
          selectedLoudnessProfile = task.loudness_profile || selectedLoudnessProfile || "standard";
          selectedManualBoostPct = Number(task.loudness_manual_boost_pct || selectedManualBoostPct || 50);
        }
```

- [ ] **Step 5: Fix actual algorithm display**

In `renderLoudnessMatch(sep)`, replace:

```javascript
    var algorithm = tl.algorithm || "A";
```

with:

```javascript
    var algorithm = primary.algorithm || tl.algorithm || "A";
    var algorithmBase = algorithm === "B" ? "B" : "A";
```

Change B condition checks from `algorithm === "B"` to `algorithmBase === "B"`.

Replace `algoBadge` text construction with:

```javascript
    var algoLabel = "人声对人声（TTS vs vocals · 兜底）";
    if (algorithm === "B") algoLabel = "整体对整体（mp4 vs 原视频整体）";
    if (algorithm === "A_after_B_excess_deviation") algoLabel = "B 整体偏差过大后兜底";
    if (algorithm === "A_after_B_failure") algoLabel = "B 执行失败后兜底";
    var algoBadge =
      '<span class="algo-badge algo-' + algorithmBase + '">' +
        '算法 ' + algorithmBase + '：' + algoLabel +
      '</span>';
```

- [ ] **Step 6: Add scoped CSS**

Append these styles in `_separation_card.html` near existing `#preview-loudness_match` styles:

```css
  #preview-loudness_match .loudness-profile-controls {
    margin-bottom: 10px;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  #preview-loudness_match .loudness-profile-pills {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }
  #preview-loudness_match .loudness-profile-pill {
    border: 1px solid var(--border-strong, #bfdbfe);
    background: var(--bg, #f8fafc);
    color: var(--fg-muted, #475569);
    border-radius: 999px;
    padding: 4px 10px;
    font-size: 12px;
    line-height: 1.2;
    cursor: pointer;
  }
  #preview-loudness_match .loudness-profile-pill.active {
    border-color: var(--accent, #0284c7);
    color: var(--accent, #0284c7);
    background: var(--info-bg, #e0f2fe);
    font-weight: 700;
  }
  #preview-loudness_match .loudness-profile-hint {
    color: var(--fg-muted, #64748b);
    font-size: 12px;
    line-height: 1.45;
  }
  .manual-boost-modal {
    position: fixed;
    inset: 0;
    z-index: 10000;
    background: rgba(15, 23, 42, 0.28);
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 16px;
  }
  .manual-boost-dialog {
    width: min(360px, 100%);
    background: var(--bg, #f8fafc);
    border: 1px solid var(--border, #dbeafe);
    border-radius: var(--radius-lg, 12px);
    padding: 16px;
  }
  .manual-boost-title {
    font-size: 15px;
    font-weight: 700;
    color: var(--fg, #0f172a);
    margin-bottom: 12px;
  }
  .manual-boost-options {
    display: grid;
    grid-template-columns: repeat(5, minmax(0, 1fr));
    gap: 8px;
  }
  .manual-boost-option {
    min-height: 32px;
    border: 1px solid var(--border-strong, #bfdbfe);
    border-radius: var(--radius-md, 8px);
    background: var(--bg, #f8fafc);
    color: var(--fg-muted, #475569);
    cursor: pointer;
  }
  .manual-boost-option.active {
    border-color: var(--accent, #0284c7);
    color: var(--accent, #0284c7);
    background: var(--info-bg, #e0f2fe);
    font-weight: 700;
  }
  .manual-boost-actions {
    margin-top: 14px;
    display: flex;
    justify-content: flex-end;
    gap: 8px;
  }
```

- [ ] **Step 7: Run UI static test**

Run:

```bash
pytest tests/test_translate_detail_shell_templates.py::test_loudness_card_exposes_profile_controls_and_actual_algorithm -q
```

Expected: selected static UI test passes.

- [ ] **Step 8: Commit UI**

Run:

```bash
git add web/templates/_separation_card.html tests/test_translate_detail_shell_templates.py
git commit -m "feat(omni): render loudness profile controls" -m "Docs-anchor: docs/superpowers/specs/2026-05-14-omni-loudness-background-boost-design.md#ux-设计"
```

---

### Task 4B: Loudness Card Button Scale And Runtime Logic Label

**Files:**
- Modify: `web/templates/_separation_card.html`
- Modify: `tests/test_translate_detail_shell_templates.py`

- [x] **Step 1: Write failing static UI assertions**

Extend `test_loudness_card_exposes_profile_controls_and_actual_algorithm` in `tests/test_translate_detail_shell_templates.py` with these assertions:

```python
    assert "当前运行逻辑：" in separation
    assert "appliedLoudnessProfileLabel" in separation
    assert "min-width: 104px" in separation
    assert "min-height: 56px" in separation
    assert "font-size: 16px" in separation
    assert "white-space: normal" in separation
    assert "overflow-wrap: anywhere" in separation
```

- [x] **Step 2: Run the focused static UI test and verify failure**

Run:

```bash
pytest tests/test_translate_detail_shell_templates.py::test_loudness_card_exposes_profile_controls_and_actual_algorithm -q
```

Expected: failure on missing `当前运行逻辑：` or the enlarged button CSS strings.

- [x] **Step 3: Add applied profile label helper**

In `web/templates/_separation_card.html`, add a helper that reads `tl.profile` and `tl.manual_boost_pct` from the latest `tts_loudness` summary:

```javascript
  function appliedLoudnessProfileLabel(tl) {
    if (!tl || !tl.profile) return "尚未生成";
    return profileLabel(tl.profile, tl.manual_boost_pct || selectedManualBoostPct);
  }
```

- [x] **Step 4: Render the current runtime logic line**

In `renderLoudnessProfileControls(tl)`, include this line before the selected-status hint:

```javascript
      '<div class="loudness-profile-runtime">当前运行逻辑：' + escapeHtml(appliedLoudnessProfileLabel(tl)) + '</div>' +
```

- [x] **Step 5: Enlarge and harden the pill CSS**

Update `#preview-loudness_match .loudness-profile-pill` so the controls are larger and text stays visible:

```css
    padding: 10px 18px;
    min-width: 104px;
    min-height: 56px;
    font-size: 16px;
    line-height: 1.25;
    white-space: normal;
    overflow-wrap: anywhere;
    text-align: center;
```

- [x] **Step 6: Run focused test and commit**

Run:

```bash
pytest tests/test_translate_detail_shell_templates.py::test_loudness_card_exposes_profile_controls_and_actual_algorithm -q
```

Expected: pass.

Commit:

```bash
git add web/templates/_separation_card.html tests/test_translate_detail_shell_templates.py
git commit -m "fix(omni): enlarge loudness profile controls" -m "Docs-anchor: docs/superpowers/specs/2026-05-14-omni-loudness-background-boost-design.md#ux-设计"
```

---

### Task 5: Regression Verification

**Files:**
- No required source changes unless a test exposes a defect.

- [ ] **Step 1: Run focused Python tests**

Run:

```bash
pytest tests/test_loudness_background_profiles.py tests/test_runtime_loudness_profiles.py tests/test_omni_translate_routes.py tests/test_translate_detail_shell_templates.py -q
```

Expected: all selected tests pass.

- [ ] **Step 2: Run existing prompt/workbench static tests**

Run:

```bash
pytest tests/test_prompt_inspector_assets.py tests/test_omni_preset_e2e_smoke.py -q
```

Expected: all selected tests pass.

- [ ] **Step 3: Run diff whitespace check**

Run:

```bash
git diff --check
```

Expected: no output and exit code 0.

- [ ] **Step 4: Start dev server and verify unauthenticated route guard**

Run:

```bash
python -m web.app
```

In another terminal, run:

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:5000/omni-translate/14e2e3b7-8a76-4459-aa84-7a9a49799d70
```

Expected: `302`. If port 5000 is occupied, start the server on an available local port and use that port in the curl command.

- [ ] **Step 5: Final commit for verification-only fixes**

If verification required small fixes, commit them:

```bash
git add <changed-files>
git commit -m "fix(omni): stabilize loudness profile regressions" -m "Docs-anchor: docs/superpowers/specs/2026-05-14-omni-loudness-background-boost-design.md#验证策略"
```

If no files changed during verification, do not create an empty commit.

---

## Self-Review

- Spec coverage:
  - Three profiles: Tasks 1, 2, 4.
  - Automatic boost with cap: Task 1 and Task 3.
  - Manual `+10%` to `+100%`: Tasks 1, 2, 4.
  - Per-task state and API: Task 2.
  - Resume from `loudness_match`: existing resume route remains unchanged; Task 2 stores state consumed by Task 3.
  - Source backup to prevent repeated loudnorm drift: Task 3.
  - Compose fallback uses effective volume: Task 3.
  - Actual B-to-A algorithm display: Task 4.
  - Enlarged adaptive buttons and current runtime logic label: Task 4B.
  - Verification: Task 5.
- Placeholder scan: no red-flag placeholders and no intentionally incomplete steps.
- Type consistency:
  - Profile values are `standard`, `bg_boost`, `manual_boost`.
  - Task field is `loudness_manual_boost_pct`.
  - Runtime summary field is `effective_background_volume`.
  - Route is `/api/omni-translate/<task_id>/loudness-profile`.
