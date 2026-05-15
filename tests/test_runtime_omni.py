"""OmniTranslateRunner 关键不变量测试。"""
from __future__ import annotations

import inspect

from appcore.events import EventBus


def test_step_asr_never_calls_lid_to_override_manual_source_language():
    """源语言由人工选择，_step_asr 不能再调用 LID 改写 source_language。"""
    from appcore.runtime_omni import OmniTranslateRunner

    src = inspect.getsource(OmniTranslateRunner._step_asr)
    assert "detect_language_llm" not in src
    assert "omni-lid-override" not in src


def test_shot_char_limit_translation_units_follow_asr_with_shot_context():
    from appcore.runtime_omni_steps import build_asr_primary_translation_units

    script_segments = [
        {
            "index": 0,
            "start_time": 0.179,
            "end_time": 4.159,
            "text": "Opening hook keeps speaking",
        },
        {
            "index": 1,
            "start_time": 4.319,
            "end_time": 8.679,
            "text": "Second ASR sentence continues",
        },
    ]
    shots = [
        {"index": 1, "start": 0.0, "end": 3.0, "description": "hook visual"},
        {"index": 2, "start": 3.0, "end": 6.0, "description": "demo visual"},
        {"index": 3, "start": 6.0, "end": 10.33, "description": "storage visual"},
    ]

    units = build_asr_primary_translation_units(script_segments, [], shots)

    assert len(units) == 2
    assert units[0]["index"] == 0
    assert units[0]["source_text"] == "Opening hook keeps speaking"
    assert units[0]["start"] == 0.179
    assert units[0]["end"] == 4.159
    assert units[0]["duration"] == 3.98
    assert [item["index"] for item in units[0]["shot_context"]] == [1, 2]
    assert units[0]["description"] == "hook visual / demo visual"
    assert units[1]["source_text"] == "Second ASR sentence continues"
    assert [item["index"] for item in units[1]["shot_context"]] == [2, 3]


def test_omni_ja_localization_adapter_keeps_character_budget_hooks(monkeypatch):
    from appcore.runtime_omni import OmniTranslateRunner

    ja_text = "\u30dc\u30c8\u30eb\u3092\u6e05\u6f54\u306b\u4fdd\u3061\u307e\u3059\u3002"
    monkeypatch.setattr(
        "appcore.runtime_omni._resolve_prompt_anchor",
        lambda slot, lang: {"content": "Rewrite Japanese to {target_words} {direction}."},
    )
    runner = OmniTranslateRunner(bus=EventBus(), user_id=1)

    adapter = runner._get_localization_module({
        "target_lang": "ja",
        "source_language": "pt",
        "utterances": [{"text": "Mantenha esta garrafa limpa."}],
    })

    assert adapter.count_tts_units(f"{ja_text} \n") == len(ja_text)
    assert adapter.rewrite_unit_label == "\u5b57"
    assert adapter.DEFAULT_TTS_UNITS_PER_SECOND == 7.0
    assert callable(adapter.generate_duration_rewrite)

    tts_script = adapter.build_tts_script_from_localized({
        "full_text": ja_text,
        "sentences": [
            {
                "index": 0,
                "text": ja_text,
                "source_segment_indices": [0],
                "asr_index": 0,
            }
        ],
    })
    assert tts_script["full_text"] == ja_text
    assert tts_script["blocks"][0]["text"] == ja_text

    rewrite_messages = adapter.build_localized_rewrite_messages(
        "Mantenha esta garrafa limpa.",
        {"full_text": ja_text, "sentences": []},
        12,
        "shrink",
        source_language="pt",
    )
    assert "ORIGINAL VIDEO TRANSCRIPT (Portuguese" in rewrite_messages[1]["content"]
