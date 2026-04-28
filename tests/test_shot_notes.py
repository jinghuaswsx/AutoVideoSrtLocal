from __future__ import annotations

import pytest

from pipeline import shot_notes


SCRIPT_SEGMENTS = [
    {"index": 0, "start_time": 0.0, "end_time": 1.2, "text": "第一句"},
    {"index": 1, "start_time": 1.2, "end_time": 2.4, "text": "第二句"},
    {"index": 2, "start_time": 2.4, "end_time": 3.6, "text": "第三句"},
]


def _shot_notes_payload(*, sentences=None):
    return {
        "global": {
            "product_name": "Ocean Bottle",
            "category": "drinkware",
            "overall_theme": "便携保温杯展示",
            "hook_range": [0, 0],
            "demo_range": [1, 1],
            "proof_range": [2, 2],
            "cta_range": None,
            "observed_selling_points": ["保温", "防漏"],
            "price_mentioned": "$19.99",
            "on_screen_persistent_text": ["50% OFF"],
            "pacing_note": "快节奏切镜",
        },
        "sentences": sentences
        if sentences is not None
        else [
            {
                "asr_index": 0,
                "start_time": 0.0,
                "end_time": 1.2,
                "scene": "桌面特写",
                "action": "手拿起杯子",
                "on_screen_text": ["50% OFF"],
                "product_visible": True,
                "shot_type": "close_up",
                "emotion_hint": "兴奋",
            },
            {
                "asr_index": 1,
                "start_time": 1.2,
                "end_time": 2.4,
                "scene": "杯子倒水",
                "action": "展示防漏",
                "on_screen_text": [],
                "product_visible": True,
                "shot_type": "medium",
                "emotion_hint": "自信",
            },
            {
                "asr_index": 2,
                "start_time": 2.4,
                "end_time": 3.6,
                "scene": "用户出门携带",
                "action": "背包侧袋展示",
                "on_screen_text": [],
                "product_visible": True,
                "shot_type": "wide",
                "emotion_hint": "轻松",
            },
        ],
    }


def test_shot_notes_happy_path(monkeypatch):
    captured = {}

    def fake_invoke_generate(use_case_code, **kwargs):
        captured["use_case_code"] = use_case_code
        captured["kwargs"] = kwargs
        return _shot_notes_payload()

    monkeypatch.setattr(shot_notes.llm_client, "invoke_generate", fake_invoke_generate)

    result = shot_notes.generate_shot_notes(
        video_path="demo.mp4",
        script_segments=SCRIPT_SEGMENTS,
        target_language="en",
        target_market="US",
        user_id=42,
        project_id="task-1",
    )

    assert captured["use_case_code"] == "video_translate.shot_notes"
    assert captured["kwargs"]["media"] == ["demo.mp4"]
    assert captured["kwargs"]["response_schema"]["type"] == "object"
    assert result["global"]["product_name"] == "Ocean Bottle"
    assert len(result["sentences"]) == 3
    assert result["sentences"][0]["scene"] == "桌面特写"
    assert result["generated_at"]
    assert result["model"]["provider"] == "openrouter"


def test_shot_notes_fills_missing_sentences(monkeypatch):
    monkeypatch.setattr(
        shot_notes.llm_client,
        "invoke_generate",
        lambda *args, **kwargs: _shot_notes_payload(
            sentences=[
                {
                    "asr_index": 0,
                    "start_time": 0.0,
                    "end_time": 1.2,
                    "scene": "桌面特写",
                    "action": "手拿起杯子",
                    "on_screen_text": ["50% OFF"],
                    "product_visible": True,
                    "shot_type": "close_up",
                    "emotion_hint": "兴奋",
                },
                {
                    "asr_index": 2,
                    "start_time": 2.4,
                    "end_time": 3.6,
                    "scene": "用户出门携带",
                    "action": "背包侧袋展示",
                    "on_screen_text": [],
                    "product_visible": True,
                    "shot_type": "wide",
                    "emotion_hint": "轻松",
                },
            ]
        ),
    )

    result = shot_notes.generate_shot_notes(
        video_path="demo.mp4",
        script_segments=SCRIPT_SEGMENTS,
        target_language="en",
        target_market="US",
    )

    assert [row["asr_index"] for row in result["sentences"]] == [0, 1, 2]
    assert result["sentences"][1]["scene"] is None
    assert result["sentences"][1]["action"] is None
    assert result["sentences"][1]["on_screen_text"] == []
    assert result["sentences"][1]["product_visible"] is False
    assert result["sentences"][1]["shot_type"] is None
    assert result["sentences"][1]["emotion_hint"] is None


def test_shot_notes_retries_on_failure(monkeypatch):
    calls = {"count": 0}

    def fake_invoke_generate(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] < 3:
            raise RuntimeError("temporary failure")
        return _shot_notes_payload()

    monkeypatch.setattr(shot_notes.llm_client, "invoke_generate", fake_invoke_generate)

    result = shot_notes.generate_shot_notes(
        video_path="demo.mp4",
        script_segments=SCRIPT_SEGMENTS,
        target_language="en",
        target_market="US",
        max_retries=2,
    )

    assert calls["count"] == 3
    assert result["global"]["overall_theme"] == "便携保温杯展示"


def test_shot_notes_fails_after_retries(monkeypatch):
    def fake_invoke_generate(*args, **kwargs):
        raise RuntimeError("still failing")

    monkeypatch.setattr(shot_notes.llm_client, "invoke_generate", fake_invoke_generate)

    with pytest.raises(RuntimeError):
        shot_notes.generate_shot_notes(
            video_path="demo.mp4",
            script_segments=SCRIPT_SEGMENTS,
            target_language="en",
            target_market="US",
            max_retries=2,
        )
