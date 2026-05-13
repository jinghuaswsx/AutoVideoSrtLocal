"""OmniTranslateRunner 关键不变量测试。"""
from __future__ import annotations

import inspect


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
