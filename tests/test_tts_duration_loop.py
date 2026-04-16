"""Tests for TTS duration convergence helpers."""
import pytest

from appcore.runtime import _compute_next_target


class TestComputeNextTarget:
    def test_round2_shrink_when_audio_over_video(self):
        # video=30, audio=35 (over by 5)
        td, tc, direction = _compute_next_target(
            round_index=2, last_audio_duration=35.0, cps=15.0, video_duration=30.0,
        )
        assert direction == "shrink"
        assert td == pytest.approx(28.0)  # video - 2.0
        assert tc == round(28.0 * 15.0)  # 420

    def test_round2_expand_when_audio_below_lower_bound(self):
        # video=30, lo=27, audio=25 (under lo by 2)
        td, tc, direction = _compute_next_target(
            round_index=2, last_audio_duration=25.0, cps=15.0, video_duration=30.0,
        )
        assert direction == "expand"
        assert td == pytest.approx(29.0)  # video - 1.0
        assert tc == round(29.0 * 15.0)  # 435

    def test_round3_adaptive_overcorrection_when_still_long(self):
        # video=30, center=28.5, audio=33 (still long by ~4.5 from center)
        # target = center - 0.5 * (33 - 28.5) = 28.5 - 2.25 = 26.25
        # clamp: max(lo+0.3, min(hi-0.3, 26.25)) = max(27.3, min(29.7, 26.25)) = 27.3
        td, tc, direction = _compute_next_target(
            round_index=3, last_audio_duration=33.0, cps=15.0, video_duration=30.0,
        )
        assert direction == "shrink"
        assert td == pytest.approx(27.3)  # clamped to duration_lo + 0.3

    def test_round3_adaptive_overcorrection_when_still_short(self):
        # video=30, center=28.5, audio=25 (still short)
        # target = 28.5 - 0.5 * (25 - 28.5) = 28.5 + 1.75 = 30.25
        # clamp to hi - 0.3 = 29.7
        td, tc, direction = _compute_next_target(
            round_index=3, last_audio_duration=25.0, cps=15.0, video_duration=30.0,
        )
        assert direction == "expand"
        assert td == pytest.approx(29.7)  # clamped to duration_hi - 0.3

    def test_target_chars_floor_at_10(self):
        # Tiny video + small cps → target_chars would be ~0
        td, tc, direction = _compute_next_target(
            round_index=2, last_audio_duration=5.0, cps=0.1, video_duration=1.0,
        )
        assert tc >= 10

    def test_short_video_below_3s_lo_is_zero(self):
        # video=2 → duration_lo = 0
        td, tc, direction = _compute_next_target(
            round_index=2, last_audio_duration=5.0, cps=15.0, video_duration=2.0,
        )
        # round 2 shrink → target = video - 2.0 = 0.0; target_chars clamped to >=10
        assert direction == "shrink"
        assert tc >= 10


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
            return 28.5  # Within [27, 30] for video=30

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
        # round 1: 35s (over), round 2: 28.5s (in range for video=30)
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

    def test_three_rounds_exhausted_raises(self, tmp_path, monkeypatch):
        # All three rounds return audio longer than video
        runner, loc_mod, initial = self._setup(monkeypatch, tmp_path, [40.0, 38.0, 36.0])
        with pytest.raises(RuntimeError, match="3 轮内未收敛"):
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
        from appcore import task_state
        task = task_state.get("tdl-multi")
        assert task["tts_duration_status"] == "failed"

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
