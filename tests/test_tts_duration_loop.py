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


@pytest.fixture(autouse=True)
def _disable_tts_language_guard(monkeypatch):
    monkeypatch.setattr(
        "appcore.runtime.validate_tts_script_language_or_raise",
        lambda **kwargs: {
            "is_target_language": True,
            "detected_language": kwargs.get("target_language"),
            "confidence": 1.0,
            "reason": "test bypass",
            "problem_excerpt": "",
        },
        raising=False,
    )

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
            return {"full_audio_path": out, "segments": [{"index": 0, "tts_path": out, "tts_duration": 31.5}]}

        def fake_get_audio_duration(path):
            return 31.5  # Within [29, 32] for video=30

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
        assert result["rounds"][0]["audio_duration"] == 31.5

    def test_round1_language_mismatch_fails_tts_step(self, tmp_path, monkeypatch):
        from appcore import task_state
        from appcore.tts_language_guard import TtsLanguageValidationError

        task_id = "tdl-language-mismatch"
        task_state.create(task_id, "v.mp4", str(tmp_path), original_filename="v.mp4", user_id=1)

        def fake_gen_full_audio(tts_segments, voice_id, task_dir, variant=None, **kw):
            out = os.path.join(task_dir, f"tts_full.{variant}.mp3")
            with open(out, "wb") as f:
                f.write(b"fake")
            return {"full_audio_path": out, "segments": []}

        monkeypatch.setattr("pipeline.tts.generate_full_audio", fake_gen_full_audio)
        monkeypatch.setattr("pipeline.tts._get_audio_duration", lambda path: 2.0)
        monkeypatch.setattr(
            "pipeline.translate.generate_tts_script",
            lambda loc, **kwargs: {
                "full_text": "This is English.",
                "blocks": [{"index": 0, "text": "This is English.", "sentence_indices": [0], "source_segment_indices": [0]}],
                "subtitle_chunks": [],
            },
        )
        monkeypatch.setattr("pipeline.speech_rate_model.get_rate", lambda v, l: 15.0)
        monkeypatch.setattr("pipeline.speech_rate_model.update_rate", lambda *a, **kw: None)

        import importlib
        loc_mod = importlib.import_module("pipeline.localization")
        monkeypatch.setattr(loc_mod, "build_tts_segments", lambda script, segs: [])

        guard_calls = []

        def reject_language(**kwargs):
            guard_calls.append(kwargs)
            raise TtsLanguageValidationError(
                "TTS language check failed: target=Spanish detected=English",
                result={
                    "is_target_language": False,
                    "target_language": "Spanish",
                    "detected_language": "English",
                    "confidence": 0.98,
                    "reason": "The TTS script is English.",
                    "problem_excerpt": "This is English.",
                },
            )

        monkeypatch.setattr("appcore.runtime.validate_tts_script_language_or_raise", reject_language)

        runner = self._make_runner()
        with pytest.raises(TtsLanguageValidationError):
            runner._run_tts_duration_loop(
                task_id=task_id,
                task_dir=str(tmp_path),
                loc_mod=loc_mod,
                provider="openrouter",
                video_duration=30.0,
                voice={"id": 1, "elevenlabs_voice_id": "test-voice"},
                initial_localized_translation={
                    "full_text": "Texto español.",
                    "sentences": [{"index": 0, "text": "Texto español.", "source_segment_indices": [0]}],
                },
                source_full_text="Source.",
                source_language="en",
                elevenlabs_api_key="fake-key",
                script_segments=[{"index": 0, "text": "x", "start_time": 0, "end_time": 3}],
                variant="normal",
                target_language_label="es",
            )

        assert guard_calls[0]["text"] == "This is English."
        assert guard_calls[0]["target_language"] == "es"
        task = task_state.get(task_id)
        assert task["steps"]["tts"] == "error"
        assert "TTS language check failed" in task["step_messages"]["tts"]
        saved = json.loads((tmp_path / "tts_language_check.round_1.json").read_text(encoding="utf-8"))
        assert saved["detected_language"] == "English"


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
        # video=30, final range=[29, 32]; round 1: 35 (>hi), round 2: 29.5 (in range)
        runner, loc_mod, initial = self._setup(monkeypatch, tmp_path, [35.0, 29.5])
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

    def test_round1_audio_up_to_video_plus_two_converges_immediately(self, tmp_path, monkeypatch):
        # video=30, final range=[29, 32]; 31.5s should stop at round 1.
        runner, loc_mod, initial = self._setup(monkeypatch, tmp_path, [31.5])
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
        assert result["final_round"] == 1
        assert len(result["rounds"]) == 1
        assert result["rounds"][0]["duration_lo"] == pytest.approx(29.0)
        assert result["rounds"][0]["duration_hi"] == pytest.approx(32.0)
        assert result["rounds"][0]["final_reason"] == "converged"

    def test_round1_audio_below_video_minus_one_continues(self, tmp_path, monkeypatch):
        # video=30, final range=[29, 32]; 28.5s is too short, 29.0s converges.
        runner, loc_mod, initial = self._setup(monkeypatch, tmp_path, [28.5, 29.0])
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
        assert result["final_round"] == 2
        assert len(result["rounds"]) == 2
        assert result["rounds"][0]["audio_duration"] == pytest.approx(28.5)
        assert result["rounds"][1]["audio_duration"] == pytest.approx(29.0)

    def test_round2_stage1_hit_but_not_final_range_continues(self, tmp_path, monkeypatch):
        # video=35
        # old stage1 range: [31.5, 38.5]
        # final range: [34.0, 37.0]
        # round 2 audio=38.2 should NOT stop the loop.
        runner, loc_mod, initial = self._setup(monkeypatch, tmp_path, [40.0, 38.2, 34.0])
        result = runner._run_tts_duration_loop(
            task_id="tdl-multi", task_dir=str(tmp_path), loc_mod=loc_mod,
            provider="openrouter", video_duration=35.0,
            voice={"id": 1, "elevenlabs_voice_id": "v"},
            initial_localized_translation=initial,
            source_full_text="Source", source_language="zh",
            elevenlabs_api_key="k",
            script_segments=[{"index": 0, "text": "x", "start_time": 0, "end_time": 3}],
            variant="normal",
        )
        assert result["final_round"] == 3
        assert len(result["rounds"]) == 3
        assert result["rounds"][1]["audio_duration"] == pytest.approx(38.2)
        assert result["rounds"][2]["audio_duration"] == pytest.approx(34.0)

    def test_all_rounds_exhausted_picks_best(self, tmp_path, monkeypatch):
        # 5 rounds all > hi=32 (video=30). Best = last (34.0, closest to [29, 32]).
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

    def test_all_rounds_exhausted_picks_closest_to_final_range(self, tmp_path, monkeypatch):
        # video=30, final range=[29, 32]
        # Distances to final range are:
        # round1 33.2 -> 1.2
        # round2 28.6 -> 0.4  (should win)
        # round3 35.0 -> 3.0
        # round4 35.1 -> 3.1
        # round5 35.2 -> 3.2
        runner, loc_mod, initial = self._setup(
            monkeypatch, tmp_path, [33.2, 28.6, 35.0, 35.1, 35.2],
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
        assert result["final_round"] == 2
        assert len(result["rounds"]) == 5
        assert result["tts_audio_path"].endswith("tts_full.round_2.mp3")

    def test_intermediate_files_written(self, tmp_path, monkeypatch):
        runner, loc_mod, initial = self._setup(monkeypatch, tmp_path, [35.0, 29.5])
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


class TestRewriteAttemptDiversity:
    """复盘 880694eb…1058c8：5 次 rewrite attempt 必须打破"同一份输出"的死循环。

    这个事故里 Gemini 在 5 次同 prompt + temperature=0.2 下给出字符级相同的德语译文，
    重试机制等于 1 次。修复要求：① 第一次 0.6，第二次起 1.0；② attempt 2+ 必须把
    "前几次给了多少词、目标多少"塞进 prompt（feedback_notes 非空）。
    """

    def _setup_unconverging_rewrite(self, monkeypatch, tmp_path, captured_calls):
        """让 fake_gen_rewrite 永远返回字数远偏 target 的译文，强制内层跑满 5 次。"""
        from appcore import task_state
        task_state.create("tdl-attempts", "v.mp4", str(tmp_path),
                          original_filename="v.mp4", user_id=1)

        def fake_gen_full_audio(tts_segments, voice_id, task_dir, variant=None, **kw):
            out = os.path.join(task_dir, f"tts_full.{variant}.mp3")
            with open(out, "wb") as f:
                f.write(b"fake")
            return {"full_audio_path": out,
                    "segments": [{"index": 0, "tts_path": out, "tts_duration": 1.0}]}

        # round 1 已超长触发 round 2，round 2 怎么写都收敛不了 → 我们只关心 round 2
        # 的 5 次 attempt 行为
        durations = iter([60.0, 60.0])

        def fake_get_audio_duration(path):
            return next(durations, 60.0)

        def fake_gen_tts_script(loc, **kwargs):
            return {"full_text": loc.get("full_text", ""), "blocks": [], "subtitle_chunks": []}

        def fake_gen_rewrite(**kwargs):
            captured_calls.append(kwargs)
            # 永远返回 200 词，远离 target_words，让内层 5 次都打不进窗口
            text = "word " * 200
            return {
                "full_text": text.strip(),
                "sentences": [{"index": 0, "text": text.strip(),
                               "source_segment_indices": [0]}],
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
        monkeypatch.setattr(loc_mod, "build_localized_rewrite_messages",
                            lambda **kw: [{"role": "system", "content": ""},
                                          {"role": "user", "content": ""}],
                            raising=False)

        from appcore.events import EventBus
        from appcore.runtime import PipelineRunner
        runner = PipelineRunner(bus=EventBus(), user_id=1)
        initial = {"full_text": "A" * 400,
                   "sentences": [{"index": 0, "text": "A" * 400,
                                  "source_segment_indices": [0]}]}
        return runner, loc_mod, initial

    def test_attempts_use_temperature_ladder_and_feedback(
        self, tmp_path, monkeypatch,
    ):
        captured: list[dict] = []
        runner, loc_mod, initial = self._setup_unconverging_rewrite(
            monkeypatch, tmp_path, captured,
        )

        runner._run_tts_duration_loop(
            task_id="tdl-attempts", task_dir=str(tmp_path), loc_mod=loc_mod,
            provider="openrouter", video_duration=30.0,
            voice={"id": 1, "elevenlabs_voice_id": "v"},
            initial_localized_translation=initial,
            source_full_text="Source", source_language="zh",
            elevenlabs_api_key="k",
            script_segments=[{"index": 0, "text": "x", "start_time": 0, "end_time": 3}],
            variant="normal",
        )

        # round 1 不调 rewrite。后面每轮都跑满 5 次 attempt（永远不收敛）。
        # 单测只校验前 5 次（第一轮 rewrite 的 5 次 attempt）。
        assert len(captured) >= 5, f"expected ≥5 rewrite calls, got {len(captured)}"
        first_round = captured[:5]

        # ① 温度阶梯：第 1 次 0.6，后 4 次 1.0
        temperatures = [c["temperature"] for c in first_round]
        assert temperatures == [0.6, 1.0, 1.0, 1.0, 1.0], (
            f"temperature ladder broken: {temperatures}"
        )

        # ② attempt 1 不带 feedback；attempt 2+ 带 feedback 且包含前几次的词数
        assert first_round[0]["feedback_notes"] is None
        for i in range(1, 5):
            notes = first_round[i]["feedback_notes"]
            assert notes is not None, f"attempt {i+1} missing feedback_notes"
            # feedback 里必须列出前面 attempt 的 word counts
            assert "200" in notes, f"attempt {i+1} feedback missing prior word count: {notes}"
            assert f"attempt {i+1} of 5" in notes


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

    def test_trim_skipped_when_audio_within_final_range(self, tmp_path):
        from appcore.runtime import PipelineRunner
        from appcore.events import EventBus
        runner = PipelineRunner(bus=EventBus(), user_id=1)
        segs = [{"index": 0, "tts_path": "/x", "tts_duration": 21.5}]
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
                    "segments": [{"index": 0, "tts_path": out, "tts_duration": 30.0}]}

        monkeypatch.setattr("pipeline.tts.generate_full_audio", fake_gen_full_audio)
        monkeypatch.setattr("pipeline.tts._get_audio_duration", lambda p: 30.0)
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

        billing_calls = []
        monkeypatch.setattr(
            "appcore.runtime.ai_billing.log_request",
            lambda **kw: billing_calls.append(kw),
        )

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
        assert [call["use_case_code"] for call in billing_calls] == [
            "video_translate.tts_script",
            "video_translate.tts",
        ]
        assert billing_calls[0]["provider"] == "gemini_vertex"
        assert billing_calls[1]["provider"] == "elevenlabs"
        assert billing_calls[1]["request_units"] == len("EN.")

    def test_step_tts_truncates_audio_beyond_video_plus_two(self, tmp_path, monkeypatch):
        from appcore import task_state
        from appcore.events import EventBus
        from appcore.runtime import PipelineRunner

        task_id = "step-tts-truncate"
        task_state.create(
            task_id,
            str(tmp_path / "v.mp4"),
            str(tmp_path),
            original_filename="v.mp4",
            user_id=1,
        )
        task_state.update(
            task_id,
            script_segments=[
                {"index": 0, "text": "a", "start_time": 0.0, "end_time": 1.0},
                {"index": 1, "text": "b", "start_time": 1.0, "end_time": 2.0},
                {"index": 2, "text": "c", "start_time": 2.0, "end_time": 3.0},
            ],
            source_full_text_zh="source",
            source_language="zh",
            localized_translation={
                "full_text": "A B C",
                "sentences": [
                    {"index": 0, "text": "A", "source_segment_indices": [0]},
                    {"index": 1, "text": "B", "source_segment_indices": [1]},
                    {"index": 2, "text": "C", "source_segment_indices": [2]},
                ],
            },
            variants={"normal": {"localized_translation": {
                "full_text": "A B C",
                "sentences": [
                    {"index": 0, "text": "A", "source_segment_indices": [0]},
                    {"index": 1, "text": "B", "source_segment_indices": [1]},
                    {"index": 2, "text": "C", "source_segment_indices": [2]},
                ],
            }}},
            voice_id=99,
        )

        round_audio = tmp_path / "tts_full.round_5.mp3"
        round_audio.write_bytes(b"original audio")
        ffmpeg_calls = []

        def fake_ffmpeg(cmd, *args, **kwargs):
            ffmpeg_calls.append(cmd)
            out_path = cmd[-1]
            with open(out_path, "wb") as f:
                f.write(b"truncated audio")
            completed = MagicMock()
            completed.returncode = 0
            completed.stderr = ""
            return completed

        monkeypatch.setattr("subprocess.run", fake_ffmpeg)
        monkeypatch.setattr("appcore.api_keys.resolve_key", lambda *args, **kwargs: "fake-key")
        monkeypatch.setattr("appcore.api_keys.get_key", lambda *args, **kwargs: None)
        monkeypatch.setattr("appcore.api_keys.resolve_extra", lambda *args, **kwargs: {})
        monkeypatch.setattr("pipeline.extract.get_video_duration", lambda path: 30.0)
        monkeypatch.setattr("pipeline.tts._get_audio_duration", lambda path: 36.0)
        billing_calls = []
        monkeypatch.setattr(
            "appcore.runtime.ai_billing.log_request",
            lambda **kw: billing_calls.append(kw),
        )

        runner = PipelineRunner(bus=EventBus(), user_id=1)
        monkeypatch.setattr(
            runner,
            "_resolve_voice",
            lambda task, loc_mod: {"id": 99, "elevenlabs_voice_id": "voice-99", "name": "Voice"},
        )
        monkeypatch.setattr(
            runner,
            "_run_tts_duration_loop",
            lambda **kwargs: {
                "localized_translation": kwargs["initial_localized_translation"],
                "tts_script": {
                    "full_text": "A B C",
                    "blocks": [
                        {"index": 0, "text": "A", "sentence_indices": [0], "source_segment_indices": [0]},
                        {"index": 1, "text": "B", "sentence_indices": [1], "source_segment_indices": [1]},
                        {"index": 2, "text": "C", "sentence_indices": [2], "source_segment_indices": [2]},
                    ],
                    "subtitle_chunks": [
                        {"index": 0, "text": "A", "block_indices": [0], "sentence_indices": [0], "source_segment_indices": [0]},
                        {"index": 1, "text": "B", "block_indices": [1], "sentence_indices": [1], "source_segment_indices": [1]},
                        {"index": 2, "text": "C", "block_indices": [2], "sentence_indices": [2], "source_segment_indices": [2]},
                    ],
                },
                "tts_audio_path": str(round_audio),
                "tts_segments": [
                    {"index": 0, "text": "A", "translated": "A", "tts_path": str(round_audio), "tts_duration": 12.0},
                    {"index": 1, "text": "B", "translated": "B", "tts_path": str(round_audio), "tts_duration": 12.0},
                    {"index": 2, "text": "C", "translated": "C", "tts_path": str(round_audio), "tts_duration": 12.0},
                ],
                "rounds": [{
                    "round": 5,
                    "audio_duration": 36.0,
                    "translate_tokens_in": 11,
                    "translate_tokens_out": 7,
                    "tts_script_tokens_in": 5,
                    "tts_script_tokens_out": 4,
                    "tts_char_count": 5,
                }],
                "final_round": 5,
            },
        )

        runner._step_tts(task_id, str(tmp_path))

        task = task_state.get(task_id)
        variant_state = task["variants"]["normal"]
        assert ffmpeg_calls, "expected direct audio truncation"
        assert variant_state["tts_audio_path"].endswith("tts_full.normal.mp3")
        assert (tmp_path / "tts_full.normal.mp3").read_bytes() == b"truncated audio"
        assert len(variant_state["segments"]) == 3
        assert sum(seg["tts_duration"] for seg in variant_state["segments"]) == pytest.approx(32.0)
        assert variant_state["timeline_manifest"]["total_tts_duration"] == pytest.approx(32.0)
        assert [call["use_case_code"] for call in billing_calls] == [
            "video_translate.rewrite",
            "video_translate.tts_script",
            "video_translate.tts",
        ]
        assert billing_calls[0]["provider"] == "gemini_vertex"
        assert billing_calls[0]["input_tokens"] == 11
        assert billing_calls[0]["output_tokens"] == 7
        assert billing_calls[2]["provider"] == "elevenlabs"
        assert billing_calls[2]["request_units"] == 5

    def test_step_tts_keeps_audio_within_video_plus_two_without_truncation(self, tmp_path, monkeypatch):
        from appcore import task_state
        from appcore.events import EventBus
        from appcore.runtime import PipelineRunner

        task_id = "step-tts-short"
        task_state.create(
            task_id,
            str(tmp_path / "v.mp4"),
            str(tmp_path),
            original_filename="v.mp4",
            user_id=1,
        )
        task_state.update(
            task_id,
            script_segments=[{"index": 0, "text": "a", "start_time": 0.0, "end_time": 1.0}],
            source_full_text_zh="source",
            source_language="zh",
            localized_translation={
                "full_text": "A",
                "sentences": [{"index": 0, "text": "A", "source_segment_indices": [0]}],
            },
            variants={"normal": {"localized_translation": {
                "full_text": "A",
                "sentences": [{"index": 0, "text": "A", "source_segment_indices": [0]}],
            }}},
            voice_id=99,
        )

        round_audio = tmp_path / "tts_full.round_2.mp3"
        round_audio.write_bytes(b"in-range audio")

        def fail_if_ffmpeg(*args, **kwargs):
            raise AssertionError("ffmpeg truncation should not run for audio within final range")

        monkeypatch.setattr("subprocess.run", fail_if_ffmpeg)
        monkeypatch.setattr("appcore.api_keys.resolve_key", lambda *args, **kwargs: "fake-key")
        monkeypatch.setattr("appcore.api_keys.get_key", lambda *args, **kwargs: None)
        monkeypatch.setattr("appcore.api_keys.resolve_extra", lambda *args, **kwargs: {})
        monkeypatch.setattr("pipeline.extract.get_video_duration", lambda path: 30.0)
        monkeypatch.setattr("pipeline.tts._get_audio_duration", lambda path: 31.5)

        runner = PipelineRunner(bus=EventBus(), user_id=1)
        monkeypatch.setattr(
            runner,
            "_resolve_voice",
            lambda task, loc_mod: {"id": 99, "elevenlabs_voice_id": "voice-99", "name": "Voice"},
        )
        monkeypatch.setattr(
            runner,
            "_run_tts_duration_loop",
            lambda **kwargs: {
                "localized_translation": kwargs["initial_localized_translation"],
                "tts_script": {
                    "full_text": "A",
                    "blocks": [{"index": 0, "text": "A", "sentence_indices": [0], "source_segment_indices": [0]}],
                    "subtitle_chunks": [
                        {"index": 0, "text": "A", "block_indices": [0], "sentence_indices": [0], "source_segment_indices": [0]},
                    ],
                },
                "tts_audio_path": str(round_audio),
                "tts_segments": [
                    {"index": 0, "text": "A", "translated": "A", "tts_path": str(round_audio), "tts_duration": 31.5},
                ],
                "rounds": [{"round": 2, "audio_duration": 31.5}],
                "final_round": 2,
            },
        )

        runner._step_tts(task_id, str(tmp_path))

        task = task_state.get(task_id)
        variant_state = task["variants"]["normal"]
        assert (tmp_path / "tts_full.normal.mp3").read_bytes() == b"in-range audio"
        assert sum(seg["tts_duration"] for seg in variant_state["segments"]) == pytest.approx(31.5)
        assert variant_state["timeline_manifest"]["total_tts_duration"] == pytest.approx(31.5)


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
