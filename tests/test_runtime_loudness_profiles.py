from __future__ import annotations

import os
from types import SimpleNamespace

from appcore.events import EventBus
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


def test_loudness_match_clean_background_records_cleaned_accompaniment(
    monkeypatch, tmp_path,
):
    task_state = _disable_task_state_db(monkeypatch)
    runner = PipelineRunner(bus=EventBus(), user_id=1)
    task_id = "clean-background-loudness"
    task_dir = tmp_path / task_id
    task_dir.mkdir()
    audio_path = _write(task_dir / "tts.mp3", "original")
    accomp_path = _write(task_dir / "accompaniment.wav", "electric-background")

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
        loudness_profile="clean_background",
    )
    monkeypatch.setattr(
        "pipeline.audio_separation.load_settings",
        lambda: SimpleNamespace(background_volume=0.8),
    )

    def fake_clean(input_path, output_path):
        assert input_path == accomp_path
        with open(output_path, "w", encoding="utf-8") as fh:
            fh.write("clean-background")
        return output_path

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

    monkeypatch.setattr("appcore.audio_loudness.clean_electronic_background", fake_clean)
    monkeypatch.setattr("appcore.audio_loudness.normalize_to_lufs", fake_normalize)

    runner._step_loudness_match(task_id, str(task_dir))

    sep = task_state.get(task_id)["separation"]
    assert sep["effective_background_volume"] == 0.8
    assert sep["cleaned_accompaniment_path"].endswith(
        "loudness_match/accompaniment.clean.wav"
    )
    assert open(sep["cleaned_accompaniment_path"], encoding="utf-8").read() == "clean-background"
    assert sep["tts_loudness"]["profile"] == "clean_background"
    assert sep["tts_loudness"]["background_cleanup"]["enabled"] is True
    assert sep["tts_loudness"]["background_suppression"]["enabled"] is False


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


def test_compose_fallback_clean_background_uses_filtered_accompaniment(
    monkeypatch, tmp_path,
):
    task_state = _disable_task_state_db(monkeypatch)
    runner = PipelineRunner(bus=EventBus(), user_id=1)
    task_id = "compose-clean-background"
    task_dir = tmp_path / task_id
    task_dir.mkdir()
    tts_path = _write(task_dir / "tts.mp3", "tts")
    accomp_path = _write(task_dir / "accompaniment.wav", "electric-background")

    task_state.create(task_id, "video.mp4", str(task_dir), user_id=1)
    task_state.update(
        task_id,
        separation={
            "status": "done",
            "vocals_lufs": -13.0,
            "accompaniment_path": accomp_path,
            "effective_background_volume": 0.8,
            "tts_loudness": {
                "profile": "clean_background",
                "background_cleanup": {"enabled": True, "mode": "de_electric"},
                "variants": [{"variant": "normal"}],
            },
        },
        loudness_profile="clean_background",
    )
    monkeypatch.setattr(
        "pipeline.audio_separation.load_settings",
        lambda: SimpleNamespace(background_volume=0.8),
    )
    captured = {}

    def fake_clean(input_path, output_path):
        assert input_path == accomp_path
        with open(output_path, "w", encoding="utf-8") as fh:
            fh.write("clean-background")
        return output_path

    def fake_mix_with_background(**kwargs):
        captured.update(kwargs)
        with open(kwargs["output_path"], "w", encoding="utf-8") as fh:
            fh.write("mixed")
        return kwargs["output_path"]

    monkeypatch.setattr("appcore.audio_loudness.clean_electronic_background", fake_clean)
    monkeypatch.setattr("appcore.audio_loudness.mix_with_background", fake_mix_with_background)

    result = runner._maybe_mix_background_for_compose(
        task_id,
        tts_audio_path=tts_path,
        task_dir=str(task_dir),
        variant="normal",
    )

    assert result.endswith("final_audio_mixed.normal.wav")
    assert captured["background_volume"] == 0.8
    assert captured["background_path"].endswith("background_clean.normal.wav")
    assert open(captured["background_path"], encoding="utf-8").read() == "clean-background"


def test_compose_fallback_respects_zero_effective_background_volume(monkeypatch, tmp_path):
    task_state = _disable_task_state_db(monkeypatch)
    runner = PipelineRunner(bus=EventBus(), user_id=1)
    task_id = "compose-zero-background-volume"
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
            "effective_background_volume": 0.0,
            "background_volume": 0.8,
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
    assert captured["background_volume"] == 0.0
