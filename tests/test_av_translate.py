from __future__ import annotations

import pytest

from pipeline import av_translate


SCRIPT_SEGMENTS = [
    {"index": 0, "start_time": 0.0, "end_time": 1.0, "text": "第一句"},
    {"index": 1, "start_time": 1.0, "end_time": 2.5, "text": "第二句"},
]

SHOT_NOTES = {
    "global": {
        "product_name": "Ocean Bottle",
        "brand": "BlueWave",
        "category": "drinkware",
        "overall_theme": "保温杯短视频带货",
        "hook_range": [0, 0],
        "demo_range": [1, 1],
        "proof_range": None,
        "cta_range": [1, 1],
        "observed_selling_points": ["保温", "防漏"],
        "price_mentioned": "$19.99",
        "on_screen_persistent_text": ["50% OFF"],
        "pacing_note": "快节奏",
    },
    "sentences": [
        {
            "asr_index": 0,
            "start_time": 0.0,
            "end_time": 1.0,
            "scene": "桌面特写",
            "action": "拿起杯子",
            "on_screen_text": ["50% OFF"],
            "product_visible": True,
            "shot_type": "close_up",
            "emotion_hint": "兴奋",
        },
        {
            "asr_index": 1,
            "start_time": 1.0,
            "end_time": 2.5,
            "scene": "倒水展示",
            "action": "展示防漏",
            "on_screen_text": [],
            "product_visible": True,
            "shot_type": "medium",
            "emotion_hint": "自信",
        },
    ],
}

AV_INPUTS = {
    "target_language": "en",
    "target_language_name": "English",
    "target_market": "US",
    "product_overrides": {
        "product_name": None,
        "brand": None,
        "selling_points": None,
        "price": None,
        "target_audience": None,
        "extra_info": None,
    },
}


def test_build_translate_messages_uses_sentence_localization_contract(monkeypatch):
    monkeypatch.setattr(av_translate.speech_rate_model, "get_rate", lambda voice_id, language: 10.0)

    messages, _sentence_inputs, _global_context = av_translate._build_translate_messages(
        SCRIPT_SEGMENTS,
        SHOT_NOTES,
        AV_INPUTS,
        "voice-1",
    )

    system_prompt = messages[0]["content"]
    assert "one target-language sentence for every source sentence" in system_prompt
    assert "Do not merge, split, reorder, or skip sentences" in system_prompt
    assert "native short-video spoken line" in system_prompt
    assert "target_chars_range" in system_prompt
    assert "Do not invent facts" in system_prompt
    assert "ElevenLabs" in system_prompt


def test_av_translate_response_schema_requires_sentence_metadata():
    sentence_schema = av_translate.AV_TRANSLATE_RESPONSE_FORMAT["json_schema"]["schema"]["properties"]["sentences"][
        "items"
    ]
    properties = sentence_schema["properties"]
    required = sentence_schema["required"]

    assert "localization_note" in properties
    assert "duration_risk" in properties
    assert "source_intent" in properties
    assert "localization_note" in required
    assert "duration_risk" in required
    assert "source_intent" in required


def test_compute_target_chars_range_uses_speech_rate_model(monkeypatch):
    monkeypatch.setattr(av_translate.speech_rate_model, "get_rate", lambda voice_id, language: 10.0)
    assert av_translate.compute_target_chars_range(2.0, "voice-1", "en") == (18, 22)


def test_compute_target_chars_range_falls_back_when_cps_missing(monkeypatch):
    monkeypatch.setattr(av_translate.speech_rate_model, "get_rate", lambda voice_id, language: None)
    assert av_translate.compute_target_chars_range(1.0, "voice-1", "ja") == (6, 8)


def test_merge_global_context_overrides_priority():
    av_inputs = {
        **AV_INPUTS,
        "product_overrides": {
            "product_name": "Manual Name",
            "brand": "Manual Brand",
            "selling_points": ["更轻", "更快"],
            "price": "$29.99",
            "target_audience": "commuters",
            "extra_info": "BPA free",
        },
    }
    merged = av_translate._merge_global_context(SHOT_NOTES, av_inputs)

    assert merged["product_name"] == "Manual Name"
    assert merged["brand"] == "Manual Brand"
    assert merged["selling_points"] == ["更轻", "更快"]
    assert merged["price"] == "$29.99"
    assert merged["target_audience"] == "commuters"
    assert merged["extra_info"] == "BPA free"


def test_merge_global_context_shotnotes_fallback():
    merged = av_translate._merge_global_context(SHOT_NOTES, AV_INPUTS)

    assert merged["product_name"] == "Ocean Bottle"
    assert merged["brand"] == "BlueWave"
    assert merged["selling_points"] == ["保温", "防漏"]
    assert merged["price"] == "$19.99"
    assert merged["category"] == "drinkware"
    assert merged["overall_theme"] == "保温杯短视频带货"
    assert merged["pacing_note"] == "快节奏"
    assert merged["structure_ranges"] == [
        {"role": "hook", "range": [0, 0]},
        {"role": "demo", "range": [1, 1]},
        {"role": "cta", "range": [1, 1]},
    ]


def test_role_in_structure_priority():
    structure_ranges = [
        {"role": "proof", "range": [0, 2]},
        {"role": "demo", "range": [1, 3]},
        {"role": "cta", "range": [2, 4]},
        {"role": "hook", "range": [2, 2]},
    ]
    assert av_translate._role_in_structure(2, structure_ranges) == "hook"
    assert av_translate._role_in_structure(3, structure_ranges) == "cta"
    assert av_translate._role_in_structure(1, structure_ranges) == "demo"
    assert av_translate._role_in_structure(0, structure_ranges) == "proof"
    assert av_translate._role_in_structure(8, structure_ranges) == "unknown"


def test_generate_av_localized_translation_happy(monkeypatch):
    monkeypatch.setattr(av_translate.speech_rate_model, "get_rate", lambda voice_id, language: 10.0)
    captured = {}

    def fake_invoke_chat(use_case_code, **kwargs):
        captured["use_case_code"] = use_case_code
        captured["kwargs"] = kwargs
        return {
            "json": {
                "sentences": [
                    {
                        "asr_index": 0,
                        "text": "Meet your leakproof bottle.",
                        "est_chars": 27,
                        "source_intent": "hook the shopper with the product",
                        "localization_note": "native US commerce phrasing",
                        "duration_risk": "ok",
                    },
                    {
                        "asr_index": 1,
                        "text": "It stays hot and never spills.",
                        "est_chars": 30,
                        "source_intent": "demonstrate the leakproof benefit",
                        "localization_note": "keeps the claim concise",
                        "duration_risk": "may_be_long",
                    },
                ]
            }
        }

    monkeypatch.setattr(av_translate.llm_client, "invoke_chat", fake_invoke_chat)

    result = av_translate.generate_av_localized_translation(
        script_segments=SCRIPT_SEGMENTS,
        shot_notes=SHOT_NOTES,
        av_inputs=AV_INPUTS,
        voice_id="voice-1",
        user_id=7,
        project_id="task-1",
    )

    assert captured["use_case_code"] == "video_translate.av_localize"
    assert captured["kwargs"]["response_format"]["type"] == "json_schema"
    assert captured["kwargs"]["messages"][0]["role"] == "system"
    assert captured["kwargs"]["messages"][1]["role"] == "user"
    assert len(result["sentences"]) == 2
    assert result["sentences"][0]["target_duration"] == 1.0
    assert result["sentences"][0]["target_chars_range"] == (9, 11)
    assert result["sentences"][0]["role_in_structure"] == "hook"
    assert result["sentences"][1]["target_duration"] == 1.5
    assert result["sentences"][1]["role_in_structure"] == "cta"


def test_generate_av_retries_on_failure(monkeypatch):
    monkeypatch.setattr(av_translate.speech_rate_model, "get_rate", lambda voice_id, language: 10.0)
    calls = {"count": 0}

    def fake_invoke_chat(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("temporary failure")
        return {
            "json": {
                "sentences": [
                    {
                        "asr_index": 0,
                        "text": "A",
                        "est_chars": 1,
                        "source_intent": "hook",
                        "localization_note": "",
                        "duration_risk": "may_be_short",
                    },
                    {
                        "asr_index": 1,
                        "text": "B",
                        "est_chars": 1,
                        "source_intent": "demo",
                        "localization_note": "",
                        "duration_risk": "may_be_short",
                    },
                ]
            }
        }

    monkeypatch.setattr(av_translate.llm_client, "invoke_chat", fake_invoke_chat)

    result = av_translate.generate_av_localized_translation(
        script_segments=SCRIPT_SEGMENTS,
        shot_notes=SHOT_NOTES,
        av_inputs=AV_INPUTS,
        voice_id="voice-1",
    )

    assert calls["count"] == 2
    assert len(result["sentences"]) == 2


def test_rewrite_one_includes_overshoot_in_prompt(monkeypatch):
    captured = {}
    monkeypatch.setattr(av_translate.speech_rate_model, "get_rate", lambda voice_id, language: 10.0)

    def fake_invoke_chat(use_case_code, **kwargs):
        captured["use_case_code"] = use_case_code
        captured["kwargs"] = kwargs
        return {
            "json": {
                "sentences": [
                    {
                        "asr_index": 1,
                        "text": "Shorter copy",
                        "est_chars": 12,
                        "source_intent": "demo",
                        "localization_note": "shortened for timing",
                        "duration_risk": "ok",
                    }
                ]
            }
        }

    monkeypatch.setattr(av_translate.llm_client, "invoke_chat", fake_invoke_chat)

    text = av_translate.rewrite_one(
        asr_index=1,
        prev_text="This one is definitely too long for the slot",
        overshoot_sec=0.8,
        direction="shorten",
        new_target_chars_range=(12, 16),
        script_segments=SCRIPT_SEGMENTS,
        shot_notes=SHOT_NOTES,
        av_inputs=AV_INPUTS,
        voice_id="voice-1",
        user_id=9,
        project_id="task-2",
    )

    assert text == "Shorter copy"
    assert captured["use_case_code"] == "video_translate.av_rewrite"
    system_prompt = captured["kwargs"]["messages"][0]["content"]
    assert "rewrite only the focus_sentence" in system_prompt
    assert "return exactly one sentence object" in system_prompt
    assert "one target-language sentence for every source sentence" not in system_prompt
    user_prompt = captured["kwargs"]["messages"][1]["content"]
    assert "0.8" in user_prompt
    assert "12-16" in user_prompt
    assert "This one is definitely too long for the slot" in user_prompt


def test_rewrite_one_expand_prompt_avoids_shorten_language(monkeypatch):
    captured = {}
    monkeypatch.setattr(av_translate.speech_rate_model, "get_rate", lambda voice_id, language: 10.0)

    def fake_invoke_chat(use_case_code, **kwargs):
        captured["kwargs"] = kwargs
        return {
            "json": {
                "sentences": [
                    {
                        "asr_index": 1,
                        "text": "Expanded natural copy",
                        "est_chars": 21,
                        "source_intent": "demo",
                        "localization_note": "expanded naturally for timing",
                        "duration_risk": "ok",
                    }
                ]
            }
        }

    monkeypatch.setattr(av_translate.llm_client, "invoke_chat", fake_invoke_chat)

    text = av_translate.rewrite_one(
        asr_index=1,
        prev_text="Too brief",
        overshoot_sec=0.0,
        direction="expand",
        new_target_chars_range=(20, 24),
        script_segments=SCRIPT_SEGMENTS,
        shot_notes=SHOT_NOTES,
        av_inputs=AV_INPUTS,
        voice_id="voice-1",
    )

    assert text == "Expanded natural copy"
    user_prompt = captured["kwargs"]["messages"][1]["content"].lower()
    assert "expand" in user_prompt
    assert "shorter than target" in user_prompt
    assert "cannot add new facts" in user_prompt
    assert "shorten" not in user_prompt
    assert "trim" not in user_prompt


def test_rewrite_one_uses_attempt_temperature_and_failure_feedback(monkeypatch):
    captured = {}
    monkeypatch.setattr(av_translate.speech_rate_model, "get_rate", lambda voice_id, language: 10.0)

    def fake_invoke_chat(use_case_code, **kwargs):
        captured["use_case_code"] = use_case_code
        captured["kwargs"] = kwargs
        return {
            "json": {
                "sentences": [
                    {
                        "asr_index": 1,
                        "text": "A structurally different rewrite",
                        "est_chars": 32,
                        "source_intent": "demo",
                        "localization_note": "changed rhythm and sentence frame",
                        "duration_risk": "ok",
                    }
                ]
            }
        }

    monkeypatch.setattr(av_translate.llm_client, "invoke_chat", fake_invoke_chat)

    text = av_translate.rewrite_one(
        asr_index=1,
        prev_text="This line keeps coming back too long",
        overshoot_sec=0.7,
        direction="shorten",
        new_target_chars_range=(24, 30),
        script_segments=SCRIPT_SEGMENTS,
        shot_notes=SHOT_NOTES,
        av_inputs=AV_INPUTS,
        voice_id="voice-1",
        attempt_number=3,
        previous_attempts=[
            {
                "round": 1,
                "after_text": "This line keeps coming back too long",
                "tts_duration": 6.1,
                "duration_ratio": 1.22,
                "status": "needs_rewrite",
            },
            {
                "round": 2,
                "after_text": "This line still comes back too long",
                "tts_duration": 5.9,
                "duration_ratio": 1.18,
                "status": "needs_rewrite",
            },
        ],
    )

    assert text == "A structurally different rewrite"
    assert captured["use_case_code"] == "video_translate.av_rewrite"
    assert captured["kwargs"]["temperature"] >= 1.0
    system_prompt = captured["kwargs"]["messages"][0]["content"]
    assert "Do not reuse the same wording" in system_prompt
    assert "change the sentence structure" in system_prompt
    user_prompt = captured["kwargs"]["messages"][1]["content"]
    assert '"attempt_number": 3' in user_prompt
    assert "previous_failed_attempts" in user_prompt
    assert "This line still comes back too long" in user_prompt
