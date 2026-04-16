from unittest.mock import patch
from pipeline.translate_v2 import translate_shot, compute_char_limit


def test_compute_char_limit_uses_tolerance_and_rate():
    # 分镜 10s，语速 15 char/s，容忍度 0.9 → 10*0.9*15 = 135
    assert compute_char_limit(shot_duration=10.0,
                               chars_per_second=15.0,
                               tolerance=0.9) == 135


def test_compute_char_limit_default_tolerance_is_0_9():
    # 默认 tolerance = 0.9
    assert compute_char_limit(shot_duration=10.0,
                               chars_per_second=15.0) == 135


def test_compute_char_limit_returns_int():
    assert isinstance(
        compute_char_limit(shot_duration=3.33, chars_per_second=17.7),
        int,
    )


def test_translate_shot_returns_text_within_limit():
    with patch("pipeline.translate_v2._call_llm",
               return_value="She stepped in."):
        result = translate_shot(
            shot={"index": 1, "source_text": "她推开门",
                  "description": "走进咖啡厅", "duration": 3.0},
            target_language="en",
            char_limit=30,
            prev_translation=None,
            next_source=None,
            user_id=1,
        )
    assert result["translated_text"] == "She stepped in."
    assert result["char_count"] == len("She stepped in.")
    assert result["over_limit"] is False
    assert result["shot_index"] == 1


def test_translate_shot_retries_when_over_limit():
    calls = {"n": 0}
    def fake_llm(prompt, user_id):
        calls["n"] += 1
        if calls["n"] == 1:
            return "This translation is way too long for the limit here."
        return "Short."
    with patch("pipeline.translate_v2._call_llm", side_effect=fake_llm):
        result = translate_shot(
            shot={"index": 1, "source_text": "原文", "description": "d",
                  "duration": 2.0},
            target_language="en",
            char_limit=20,
            prev_translation=None,
            next_source=None,
            user_id=1,
        )
    assert calls["n"] == 2
    assert result["translated_text"] == "Short."
    assert result["over_limit"] is False


def test_translate_shot_marks_over_limit_after_max_retries():
    def fake_llm(prompt, user_id):
        return "Always way too long for this limit."
    with patch("pipeline.translate_v2._call_llm", side_effect=fake_llm):
        result = translate_shot(
            shot={"index": 1, "source_text": "原文",
                  "description": "d", "duration": 1.0},
            target_language="en",
            char_limit=10,
            prev_translation=None,
            next_source=None,
            user_id=1,
            max_retries=2,
        )
    assert result["over_limit"] is True


def test_translate_shot_honors_context_in_prompt():
    captured = {}
    def fake_llm(prompt, user_id):
        captured["prompt"] = prompt
        return "Result."
    with patch("pipeline.translate_v2._call_llm", side_effect=fake_llm):
        translate_shot(
            shot={"index": 2, "source_text": "原文", "description": "画面",
                  "duration": 3.0},
            target_language="en",
            char_limit=30,
            prev_translation="Previous line.",
            next_source="下一句原文",
            user_id=1,
        )
    prompt = captured["prompt"]
    assert "原文" in prompt
    assert "画面" in prompt
    assert "Previous line." in prompt
    assert "下一句原文" in prompt
    assert "30" in prompt or "≤ 30" in prompt
