from unittest.mock import patch

from pipeline.tts_v2 import TOLERANCE, generate_and_verify_shot


def test_tolerance_is_1_10():
    assert TOLERANCE == 1.10


def test_generate_passes_on_first_try_within_tolerance(tmp_path):
    with (
        patch("pipeline.tts_v2._tts_generate", return_value=str(tmp_path / "shot_1.mp3")),
        patch("pipeline.tts_v2._get_duration", return_value=4.8),
        patch("pipeline.tts_v2._refine_text") as refine,
        patch("pipeline.tts_v2.update_rate") as update_rate,
    ):
        result = generate_and_verify_shot(
            shot={"index": 1, "duration": 5.0},
            translated_text="Some translation.",
            voice_id="v1",
            api_key="k",
            language="en",
            user_id=1,
            out_dir=str(tmp_path),
        )
    assert result["final_duration"] == 4.8
    assert result["retry_count"] == 0
    assert result["over_tolerance"] is False
    assert result["final_text"] == "Some translation."
    refine.assert_not_called()
    update_rate.assert_called_once()


def test_generate_refines_when_over_tolerance(tmp_path):
    durations = iter([6.2, 4.9])

    def fake_generate(text, voice_id, output_path, api_key):
        return output_path

    def fake_duration(path):
        return next(durations)

    def fake_refine(prev_text, over_ratio, target_chars, user_id):
        return "Short version."

    with (
        patch("pipeline.tts_v2._tts_generate", side_effect=fake_generate),
        patch("pipeline.tts_v2._get_duration", side_effect=fake_duration),
        patch("pipeline.tts_v2._refine_text", side_effect=fake_refine),
        patch("pipeline.tts_v2.get_rate", return_value=15.0),
        patch("pipeline.tts_v2.update_rate"),
    ):
        result = generate_and_verify_shot(
            shot={"index": 1, "duration": 5.0},
            translated_text="Initial long version.",
            voice_id="v1",
            api_key="k",
            language="en",
            user_id=1,
            out_dir=str(tmp_path),
        )
    assert result["retry_count"] == 1
    assert result["final_text"] == "Short version."
    assert result["over_tolerance"] is False
    assert result["final_duration"] == 4.9


def test_generate_gives_up_after_max_retries(tmp_path):
    with (
        patch("pipeline.tts_v2._tts_generate", return_value=str(tmp_path / "shot_1.mp3")),
        patch("pipeline.tts_v2._get_duration", return_value=10.0),
        patch("pipeline.tts_v2._refine_text", return_value="still long"),
        patch("pipeline.tts_v2.get_rate", return_value=15.0),
        patch("pipeline.tts_v2.update_rate"),
    ):
        result = generate_and_verify_shot(
            shot={"index": 1, "duration": 5.0},
            translated_text="original too long",
            voice_id="v1",
            api_key="k",
            language="en",
            user_id=1,
            out_dir=str(tmp_path),
            max_retries=3,
        )
    assert result["retry_count"] == 3
    assert result["over_tolerance"] is True


def test_generate_updates_speech_rate_model_each_iteration(tmp_path):
    durations = iter([6.2, 4.9])
    update_calls = []

    def fake_generate(text, voice_id, output_path, api_key):
        return output_path

    def fake_update(voice_id, language, *, chars, duration_seconds):
        update_calls.append(
            {
                "voice_id": voice_id,
                "language": language,
                "chars": chars,
                "duration": duration_seconds,
            }
        )

    with (
        patch("pipeline.tts_v2._tts_generate", side_effect=fake_generate),
        patch("pipeline.tts_v2._get_duration", side_effect=lambda path: next(durations)),
        patch("pipeline.tts_v2._refine_text", return_value="Short."),
        patch("pipeline.tts_v2.get_rate", return_value=15.0),
        patch("pipeline.tts_v2.update_rate", side_effect=fake_update),
    ):
        generate_and_verify_shot(
            shot={"index": 1, "duration": 5.0},
            translated_text="Initial long version.",
            voice_id="v1",
            api_key="k",
            language="en",
            user_id=1,
            out_dir=str(tmp_path),
        )
    assert len(update_calls) == 2
    assert update_calls[0]["chars"] == len("Initial long version.")
    assert update_calls[1]["chars"] == len("Short.")


def test_refine_text_uses_translate_lab_use_case():
    from pipeline import tts_v2 as mod

    captured = {}

    def fake_generate(prompt, **kwargs):
        captured["prompt"] = prompt
        captured["kwargs"] = kwargs
        return {"translated_text": "Short."}

    with patch("pipeline.tts_v2.gemini_generate", side_effect=fake_generate):
        out = mod._refine_text("Long text", 0.36, 18, 9)

    assert out == "Short."
    assert "Long text" in captured["prompt"]
    assert captured["kwargs"]["service"] == "translate_lab.tts_refine"
    assert captured["kwargs"]["user_id"] == 9
