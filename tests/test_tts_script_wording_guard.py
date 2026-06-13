"""Block 2: tts_script 词级一致性守卫 — 单元测试（TDD 红阶段）。

Task 1: validate_tts_script + ensure_tts_script_wording 词签名校验
Task 3: es/it 路径接入公共校验（追加到本文件）
"""
import pytest
from pipeline.localization import (
    TtsScriptWordingMismatchError,
    ensure_tts_script_wording,
    validate_tts_script,
)

SENTENCES = [
    {"index": 0, "text": "This melts slower than regular ice.", "source_segment_indices": [0]},
    {"index": 1, "text": "Everyone wants to take one home.", "source_segment_indices": [1]},
]


def _payload(block_texts):
    return {
        "full_text": " ".join(block_texts),
        "blocks": [
            {"index": i, "text": t, "sentence_indices": [i], "source_segment_indices": [i]}
            for i, t in enumerate(block_texts)
        ],
        "subtitle_chunks": [],
    }


def test_same_wording_passes():
    payload = _payload([s["text"] for s in SENTENCES])
    result = validate_tts_script(payload, sentences=SENTENCES)
    assert result["blocks"]


def test_changed_word_raises():
    payload = _payload(["This melts slower than normal ice.",  # regular→normal
                        "Everyone wants to take one home."])
    with pytest.raises(TtsScriptWordingMismatchError):
        validate_tts_script(payload, sentences=SENTENCES)


def test_dropped_sentence_raises():
    payload = _payload(["This melts slower than regular ice."])
    with pytest.raises(TtsScriptWordingMismatchError):
        validate_tts_script(payload, sentences=SENTENCES)


def test_punct_and_case_changes_are_ok():
    payload = _payload(["this melts slower, than regular ice",
                        "everyone wants to take one home!"])
    result = validate_tts_script(payload, sentences=SENTENCES)
    assert result["blocks"]


def test_ensure_helper_reports_context():
    with pytest.raises(TtsScriptWordingMismatchError) as ei:
        ensure_tts_script_wording(
            [{"text": "totally different words"}], SENTENCES,
        )
    assert "different" in str(ei.value)


# -----------------------------------------------------------------------
# Task 3: es/it 路径（追加在 Task 3 实施后）
# -----------------------------------------------------------------------

def test_es_validate_tts_script_wording_guard():
    """Spanish validate_tts_script 也应触发词级一致性校验。"""
    from pipeline.localization_es import validate_tts_script as es_validate
    es_sentences = [
        {"index": 0, "text": "Este producto es increíble.", "source_segment_indices": [0]},
    ]
    # 改词应触发 mismatch
    bad_payload = {
        "full_text": "Este artículo es increíble.",
        "blocks": [
            {"index": 0, "text": "Este artículo es increíble.",
             "sentence_indices": [0], "source_segment_indices": [0]},
        ],
        "subtitle_chunks": [],
    }
    with pytest.raises(TtsScriptWordingMismatchError):
        es_validate(bad_payload, sentences=es_sentences)


def test_it_validate_tts_script_wording_guard():
    """Italian validate_tts_script 也应触发词级一致性校验。"""
    from pipeline.localization_it import validate_tts_script as it_validate
    it_sentences = [
        {"index": 0, "text": "Questo prodotto è fantastico.", "source_segment_indices": [0]},
    ]
    bad_payload = {
        "full_text": "Questo oggetto è fantastico.",
        "blocks": [
            {"index": 0, "text": "Questo oggetto è fantastico.",
             "sentence_indices": [0], "source_segment_indices": [0]},
        ],
        "subtitle_chunks": [],
    }
    with pytest.raises(TtsScriptWordingMismatchError):
        it_validate(bad_payload, sentences=it_sentences)
