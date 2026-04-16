"""Tests for TTS duration convergence helpers."""
import pytest

from appcore.runtime import _compute_next_target


class TestComputeNextTarget:
    def test_round2_shrink_when_audio_over_video(self):
        # video=30, lo=27, hi=33, audio=35 (over hi)
        td, tw, direction = _compute_next_target(
            round_index=2, last_audio_duration=35.0, wps=2.5, video_duration=30.0,
        )
        assert direction == "shrink"
        assert td == pytest.approx(30.0)  # aim at video_duration
        assert tw == round(30.0 * 2.5)  # 75

    def test_round2_expand_when_audio_below_lower_bound(self):
        # video=30, lo=27, audio=25 (below lo)
        td, tw, direction = _compute_next_target(
            round_index=2, last_audio_duration=25.0, wps=2.5, video_duration=30.0,
        )
        assert direction == "expand"
        assert td == pytest.approx(30.0)
        assert tw == round(30.0 * 2.5)  # 75

    def test_round3_adaptive_overcorrection_when_still_long(self):
        # video=30, center=30, audio=33
        # raw = 30 - 0.5*(33 - 30) = 28.5 → within [27, 33] clamp
        td, tw, direction = _compute_next_target(
            round_index=3, last_audio_duration=33.0, wps=2.5, video_duration=30.0,
        )
        assert direction == "shrink"
        assert td == pytest.approx(28.5)
        assert tw == round(28.5 * 2.5)  # 71

    def test_round3_adaptive_overcorrection_when_still_short(self):
        # video=30, center=30, audio=25
        # raw = 30 - 0.5*(25 - 30) = 32.5 → within [27, 33] clamp
        td, tw, direction = _compute_next_target(
            round_index=3, last_audio_duration=25.0, wps=2.5, video_duration=30.0,
        )
        assert direction == "expand"
        assert td == pytest.approx(32.5)
        assert tw == round(32.5 * 2.5)  # 81

    def test_target_words_floor_at_3(self):
        # Tiny video + small wps → target_words would be ~0 without floor.
        td, tw, direction = _compute_next_target(
            round_index=2, last_audio_duration=5.0, wps=0.01, video_duration=1.0,
        )
        assert tw >= 3

    def test_short_video(self):
        # video=2 → lo=1.8, hi=2.2; round 2 shrink aims at video=2
        td, tw, direction = _compute_next_target(
            round_index=2, last_audio_duration=5.0, wps=2.5, video_duration=2.0,
        )
        assert direction == "shrink"
        assert td == pytest.approx(2.0)
        assert tw >= 3


import os
import json
from unittest.mock import MagicMock, patch

class TestDurationLoopRound1Only:
    def _make_runner(self):
        from appcore.events import EventBus
        from appcore.runtime import PipelineRunner
        bus = EventBus()
        runner = PipelineRunner(bus=bus, user_id=1)
        return runner

    def test_round1_converges_returns_final_immediately(self, tmp_path, monkeypatch):
        """round 1 音频时长在区间内时，循环返回 final_round=1。"""
        runner = self._make_runner()

        def fake_gen_full_audio(tts_segments, voice_id, task_dir, variant=None, **kw):
            out = os.path.join(task_dir, f"tts_full.{variant}.mp3")
            with open(out, "wb") as f:
                f.write(b"fake")
            return {"full_audio_path": out, "segments": [{"index": 0, "tts_path": out, "tts_duration": 28.5}]}

        def fake_get_audio_duration(path):
            return 28.5  # Within [27, 33] for video=30

        def fake_gen_tts_script(loc, **kwargs):
            return {"full_text": "Short text.", "blocks": [{"index": 0, "text": "Short.",
                                                              "sentence_indices": [0],
                                                              "source_segment_indices": [0]}],
                    "subtitle_chunks": []}

        monkeypatch.setattr("pipeline.tts.generate_full_audio", fake_gen_full_audio)
        monkeypatch.setattr("pipeline.tts._get_audio_duration", fake_get_audio_duration)
        monkeypatch.setattr("pipeline.translate.generate_tts_script", fake_gen_tts_script)
        monkeypatch.setattr("pipeline.speech_rate_model.get_rate", lambda v, l: 15.0)
        monkeypatch.setattr("pipeline.speech_rate_model.update_rate", lambda *a, **kw: None)

        from appcore import task_state
        task_state.create("tdl-r1-conv", "v.mp4", str(tmp_path),
                          original_filename="v.mp4", user_id=1)

        import importlib
        loc_mod = importlib.import_module("pipeline.localization")
        monkeypatch.setattr(loc_mod, "build_tts_segments",
                            lambda script, segs: [{"index": 0, "tts_text": "Short.", "tts_duration": 0.0}])

        initial_localized = {
            "full_text": "Short text.",
            "sentences": [{"index": 0, "text": "Short text.", "source_segment_indices": [0]}],
        }
        voice = {"id": 1, "elevenlabs_voice_id": "test-voice"}

        result = runner._run_tts_duration_loop(
            task_id="tdl-r1-conv",
            task_dir=str(tmp_path),
            loc_mod=loc_mod,
            provider="openrouter",
            video_duration=30.0,
            voice=voice,
            initial_localized_translation=initial_localized,
            source_full_text="Source zh.",
            source_language="zh",
            elevenlabs_api_key="fake-key",
            script_segments=[{"index": 0, "text": "x", "start_time": 0, "end_time": 3}],
            variant="normal",
        )

        assert result["final_round"] == 1
        assert result["tts_audio_path"].endswith("tts_full.round_1.mp3")
        assert len(result["rounds"]) == 1
        assert result["rounds"][0]["round"] == 1
        assert result["rounds"][0]["audio_duration"] == 28.5


class TestDurationLoopMultiRound:
    def _setup(self, monkeypatch, tmp_path, audio_durations):
        """audio_durations: list of durations returned in sequence per round."""
        from appcore import task_state
        task_state.create("tdl-multi", "v.mp4", str(tmp_path),
                          original_filename="v.mp4", user_id=1)
        call_counter = {"i": 0}

        def fake_gen_full_audio(tts_segments, voice_id, task_dir, variant=None, **kw):
            out = os.path.join(task_dir, f"tts_full.{variant}.mp3")
            with open(out, "wb") as f:
                f.write(b"fake")
            return {"full_audio_path": out,
                    "segments": [{"index": 0, "tts_path": out, "tts_duration": 1.0}]}

        def fake_get_audio_duration(path):
            idx = call_counter["i"]
            call_counter["i"] += 1
            return audio_durations[min(idx, len(audio_durations) - 1)]

        def fake_gen_tts_script(loc, **kwargs):
            return {"full_text": loc.get("full_text", ""), "blocks": [], "subtitle_chunks": []}

        def fake_gen_rewrite(**kwargs):
            # Pretend rewrite shortens by 30%
            prev = kwargs["prev_localized_translation"]
            new_text = prev["full_text"][: int(len(prev["full_text"]) * 0.7)]
            return {
                "full_text": new_text,
                "sentences": [{"index": 0, "text": new_text, "source_segment_indices": [0]}],
            }

        monkeypatch.setattr("pipeline.tts.generate_full_audio", fake_gen_full_audio)
        monkeypatch.setattr("pipeline.tts._get_audio_duration", fake_get_audio_duration)
        monkeypatch.setattr("pipeline.translate.generate_tts_script", fake_gen_tts_script)
        monkeypatch.setattr("pipeline.translate.generate_localized_rewrite", fake_gen_rewrite)
        monkeypatch.setattr("pipeline.speech_rate_model.get_rate", lambda v, l: 15.0)
        monkeypatch.setattr("pipeline.speech_rate_model.update_rate", lambda *a, **kw: None)

        import importlib
        loc_mod = importlib.import_module("pipeline.localization")
        monkeypatch.setattr(loc_mod, "build_tts_segments", lambda s, sg: [])
        # stub the rewrite builder
        monkeypatch.setattr(loc_mod, "build_localized_rewrite_messages",
                            lambda **kw: [{"role": "system", "content": ""},
                                          {"role": "user", "content": ""}], raising=False)

        from appcore.events import EventBus
        from appcore.runtime import PipelineRunner
        runner = PipelineRunner(bus=EventBus(), user_id=1)

        initial = {"full_text": "A" * 400, "sentences": [{"index": 0, "text": "A" * 400, "source_segment_indices": [0]}]}
        return runner, loc_mod, initial

    def test_round2_shrink_converges(self, tmp_path, monkeypatch):
        # video=30, lo=27, hi=33; round 1: 35 (>hi), round 2: 28.5 (in range)
        runner, loc_mod, initial = self._setup(monkeypatch, tmp_path, [35.0, 28.5])
        result = runner._run_tts_duration_loop(
            task_id="tdl-multi", task_dir=str(tmp_path), loc_mod=loc_mod,
            provider="openrouter", video_duration=30.0,
            voice={"id": 1, "elevenlabs_voice_id": "v"},
            initial_localized_translation=initial,
            source_full_text="Source", source_language="zh",
            elevenlabs_api_key="k", script_segments=[{"index": 0, "text": "x", "start_time": 0, "end_time": 3}],
            variant="normal",
        )
        assert result["final_round"] == 2
        assert len(result["rounds"]) == 2
        assert result["rounds"][1].get("direction") == "shrink"
        # round 1 record has no direction (no rewrite)
        assert "direction" not in result["rounds"][0]

    def test_all_rounds_exhausted_picks_best(self, tmp_path, monkeypatch):
        # 5 rounds all > hi=33 (video=30). Best = last (34.0, closest to 30).
        runner, loc_mod, initial = self._setup(
            monkeypatch, tmp_path, [40.0, 38.0, 36.0, 35.0, 34.0],
        )
        result = runner._run_tts_duration_loop(
            task_id="tdl-multi", task_dir=str(tmp_path), loc_mod=loc_mod,
            provider="openrouter", video_duration=30.0,
            voice={"id": 1, "elevenlabs_voice_id": "v"},
            initial_localized_translation=initial,
            source_full_text="Source", source_language="zh",
            elevenlabs_api_key="k",
            script_segments=[{"index": 0, "text": "x", "start_time": 0, "end_time": 3}],
            variant="normal",
        )
        # Best = last round (34.0 is closest to 30 among the 5)
        assert result["final_round"] == 5
        assert len(result["rounds"]) == 5
        from appcore import task_state
        task = task_state.get("tdl-multi")
        assert task["tts_duration_status"] == "converged"

    def test_intermediate_files_written(self, tmp_path, monkeypatch):
        runner, loc_mod, initial = self._setup(monkeypatch, tmp_path, [35.0, 28.5])
        runner._run_tts_duration_loop(
            task_id="tdl-multi", task_dir=str(tmp_path), loc_mod=loc_mod,
            provider="openrouter", video_duration=30.0,
            voice={"id": 1, "elevenlabs_voice_id": "v"},
            initial_localized_translation=initial,
            source_full_text="Source", source_language="zh",
            elevenlabs_api_key="k",
            script_segments=[{"index": 0, "text": "x", "start_time": 0, "end_time": 3}],
            variant="normal",
        )
        # round 1 only produces tts_script + audio (no localized, since initial reused)
        assert (tmp_path / "tts_script.round_1.json").exists()
        assert (tmp_path / "tts_full.round_1.mp3").exists()
        # round 2 produces all three
        assert (tmp_path / "localized_translation.round_2.json").exists()
        assert (tmp_path / "tts_script.round_2.json").exists()
        assert (tmp_path / "tts_full.round_2.mp3").exists()


class TestPromoteFinalArtifacts:
    def test_promotes_round_n_to_normal(self, tmp_path):
        from appcore.runtime import PipelineRunner
        from appcore.events import EventBus
        runner = PipelineRunner(bus=EventBus(), user_id=1)

        # Create round_2 source file
        src = tmp_path / "tts_full.round_2.mp3"
        src.write_bytes(b"audio data")

        runner._promote_final_artifacts(
            task_dir=str(tmp_path),
            final_round=2,
            variant="normal",
        )

        dst = tmp_path / "tts_full.normal.mp3"
        assert dst.exists()
        assert dst.read_bytes() == b"audio data"


class TestTrimTailSegments:
    def test_trim_drops_trailing_blocks_until_within_video(self, tmp_path, monkeypatch):
        """Audio > video → drop trailing blocks until audio ≤ video."""
        from appcore.runtime import PipelineRunner
        from appcore.events import EventBus

        # Mock ffmpeg concat: just create a target file, don't really run
        import subprocess
        real_run = subprocess.run

        def fake_run(cmd, *args, **kwargs):
            if isinstance(cmd, list) and cmd[0] == "ffmpeg":
                out_path = cmd[-1]
                with open(out_path, "wb") as f:
                    f.write(b"trimmed audio")
                r = MagicMock()
                r.returncode = 0
                r.stderr = ""
                return r
            return real_run(cmd, *args, **kwargs)

        monkeypatch.setattr("subprocess.run", fake_run)

        runner = PipelineRunner(bus=EventBus(), user_id=1)
        # Prepare 4 segments totaling 24s; video=20s — need to drop at least the last one (6s)
        seg_dir = tmp_path / "tts_segments" / "round_1"
        seg_dir.mkdir(parents=True)
        segs = []
        for i, dur in enumerate([6.0, 6.0, 6.0, 6.0]):
            p = seg_dir / f"seg_{i:04d}.mp3"
            p.write_bytes(b"x")
            segs.append({"index": i, "tts_path": str(p), "tts_duration": dur})

        tts_script = {
            "full_text": "Block0 Block1 Block2 Block3",
            "blocks": [
                {"index": 0, "text": "Block0", "sentence_indices": [0], "source_segment_indices": [0]},
                {"index": 1, "text": "Block1", "sentence_indices": [1], "source_segment_indices": [1]},
                {"index": 2, "text": "Block2", "sentence_indices": [2], "source_segment_indices": [2]},
                {"index": 3, "text": "Block3", "sentence_indices": [3], "source_segment_indices": [3]},
            ],
            "subtitle_chunks": [
                {"index": 0, "text": "Block0", "block_indices": [0], "sentence_indices": [0], "source_segment_indices": [0]},
                {"index": 1, "text": "Block1", "block_indices": [1], "sentence_indices": [1], "source_segment_indices": [1]},
                {"index": 2, "text": "Block2", "block_indices": [2], "sentence_indices": [2], "source_segment_indices": [2]},
                {"index": 3, "text": "Block3", "block_indices": [3], "sentence_indices": [3], "source_segment_indices": [3]},
            ],
        }
        loc = {
            "full_text": "S0 S1 S2 S3",
            "sentences": [
                {"index": 0, "text": "S0", "source_segment_indices": [0]},
                {"index": 1, "text": "S1", "source_segment_indices": [1]},
                {"index": 2, "text": "S2", "source_segment_indices": [2]},
                {"index": 3, "text": "S3", "source_segment_indices": [3]},
            ],
        }

        result = runner._trim_tail_segments(
            task_dir=str(tmp_path), round_variant="round_1",
            tts_segments=segs, tts_script=tts_script, localized_translation=loc,
            video_duration=20.0,
        )

        assert result["skipped"] is False
        assert result["removed_count"] == 1
        assert result["removed_duration"] == pytest.approx(6.0)
        assert result["final_duration"] == pytest.approx(18.0)
        assert len(result["tts_segments"]) == 3
        assert [b["index"] for b in result["tts_script"]["blocks"]] == [0, 1, 2]
        assert [s["index"] for s in result["localized_translation"]["sentences"]] == [0, 1, 2]
        assert result["tts_script"]["full_text"] == "Block0 Block1 Block2"
        assert result["localized_translation"]["full_text"] == "S0 S1 S2"
        assert len(result["tts_script"]["subtitle_chunks"]) == 3

    def test_trim_skipped_when_audio_within_video(self, tmp_path):
        from appcore.runtime import PipelineRunner
        from appcore.events import EventBus
        runner = PipelineRunner(bus=EventBus(), user_id=1)
        segs = [{"index": 0, "tts_path": "/x", "tts_duration": 10.0}]
        result = runner._trim_tail_segments(
            task_dir=str(tmp_path), round_variant="round_1",
            tts_segments=segs, tts_script={"blocks": [], "subtitle_chunks": []},
            localized_translation={"sentences": []},
            video_duration=20.0,
        )
        assert result == {"skipped": True}


class TestStepTtsIntegration:
    def test_step_tts_persists_final_artifacts_to_variant_state(self, tmp_path, monkeypatch):
        """_step_tts 把 loop 最终产物写回 task.variants[normal] 并覆盖 normal 文件名。"""
        from appcore import task_state
        from appcore.events import EventBus
        from appcore.runtime import PipelineRunner

        task_id = "step-tts-int"
        task = task_state.create(task_id, str(tmp_path / "v.mp4"), str(tmp_path),
                                  original_filename="v.mp4", user_id=1)
        # Prime with script_segments + localized_translation (what translate step would have set)
        task_state.update(
            task_id,
            script_segments=[{"index": 0, "text": "x", "start_time": 0.0, "end_time": 3.0}],
            source_full_text_zh="中文原文",
            source_language="zh",
            localized_translation={
                "full_text": "EN text.",
                "sentences": [{"index": 0, "text": "EN text.", "source_segment_indices": [0]}],
            },
            variants={"normal": {"label": "普通版", "localized_translation": {
                "full_text": "EN text.",
                "sentences": [{"index": 0, "text": "EN text.", "source_segment_indices": [0]}],
            }}},
            voice_id=None,
            recommended_voice_id=None,
            voice_gender="male",
        )

        def fake_gen_full_audio(tts_segments, voice_id, task_dir, variant=None, **kw):
            out = os.path.join(task_dir, f"tts_full.{variant}.mp3")
            with open(out, "wb") as f:
                f.write(b"audio")
            return {"full_audio_path": out,
                    "segments": [{"index": 0, "tts_path": out, "tts_duration": 28.0}]}

        monkeypatch.setattr("pipeline.tts.generate_full_audio", fake_gen_full_audio)
        monkeypatch.setattr("pipeline.tts._get_audio_duration", lambda p: 28.0)
        monkeypatch.setattr("pipeline.translate.generate_tts_script",
                            lambda loc, **kw: {"full_text": "EN.", "blocks": [], "subtitle_chunks": []})
        monkeypatch.setattr("pipeline.speech_rate_model.get_rate", lambda v, l: 15.0)
        monkeypatch.setattr("pipeline.speech_rate_model.update_rate", lambda *a, **kw: None)
        monkeypatch.setattr("pipeline.extract.get_video_duration", lambda p: 30.0)
        monkeypatch.setattr("appcore.api_keys.resolve_key", lambda u, s, e: "fake-key")
        monkeypatch.setattr("appcore.api_keys.get_key", lambda u, s: None)
        monkeypatch.setattr("appcore.api_keys.resolve_extra", lambda u, s: {})
        monkeypatch.setattr("pipeline.translate.get_model_display_name", lambda p, u: "fake-model")

        from pipeline import localization as loc_mod
        monkeypatch.setattr(loc_mod, "build_tts_segments", lambda s, sg: [])

        # voice resolution: make get_voice_by_id return a fake voice
        monkeypatch.setattr("pipeline.tts.get_voice_by_id",
                            lambda vid, uid: {"id": 99, "elevenlabs_voice_id": "vx", "name": "V"})
        # Skip library fallback by forcing voice_id
        task_state.update(task_id, voice_id=99)

        monkeypatch.setattr("appcore.usage_log.record", lambda *a, **kw: None)

        runner = PipelineRunner(bus=EventBus(), user_id=1)
        runner._step_tts(task_id, str(tmp_path))

        task = task_state.get(task_id)
        assert task["steps"]["tts"] == "done"
        assert (tmp_path / "tts_full.normal.mp3").exists()
        # round_1 file must also exist (intermediate)
        assert (tmp_path / "tts_full.round_1.mp3").exists()
        # variant state updated
        v_state = task["variants"]["normal"]
        assert v_state["tts_audio_path"].endswith("tts_full.normal.mp3")
        assert task["tts_duration_status"] == "converged"


class TestLanguageSpecificRunners:
    def test_de_runner_uses_german_localization_module(self, monkeypatch):
        """DeTranslateRunner._step_tts goes through base class with de localization."""
        from appcore.runtime_de import DeTranslateRunner
        from appcore.events import EventBus
        captured_modules = []

        import importlib
        real_import_module = importlib.import_module

        def tracking_import(name, *a, **kw):
            if "localization" in name:
                captured_modules.append(name)
            return real_import_module(name, *a, **kw)

        monkeypatch.setattr(importlib, "import_module", tracking_import)

        runner = DeTranslateRunner(bus=EventBus(), user_id=1)
        # Just trigger the module resolution via loc_mod lookup
        import importlib as _il
        loc_mod = _il.import_module(runner.localization_module)
        assert loc_mod.__name__ == "pipeline.localization_de"
        assert hasattr(loc_mod, "build_localized_rewrite_messages")
        assert hasattr(loc_mod, "build_tts_script_messages")

    def test_fr_runner_uses_french_localization_module(self):
        from appcore.runtime_fr import FrTranslateRunner
        from appcore.events import EventBus
        runner = FrTranslateRunner(bus=EventBus(), user_id=1)
        import importlib as _il
        loc_mod = _il.import_module(runner.localization_module)
        assert loc_mod.__name__ == "pipeline.localization_fr"
        assert hasattr(loc_mod, "build_localized_rewrite_messages")

    def test_de_runner_does_not_override_step_tts(self):
        """DeTranslateRunner must inherit _step_tts from base (no local override)."""
        from appcore.runtime_de import DeTranslateRunner
        from appcore.runtime import PipelineRunner
        # The bound method should resolve to the same function as base class
        assert DeTranslateRunner._step_tts is PipelineRunner._step_tts

    def test_fr_runner_does_not_override_step_tts(self):
        from appcore.runtime_fr import FrTranslateRunner
        from appcore.runtime import PipelineRunner
        assert FrTranslateRunner._step_tts is PipelineRunner._step_tts
