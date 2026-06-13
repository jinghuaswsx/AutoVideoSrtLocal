"""Block 2: tts_script wording 重试 + 确定性回退 — 单元测试（TDD 红阶段）。

Task 2: _generate_tts_script_single 捕获 TtsScriptWordingMismatchError:
  - 首次 mismatch → 带反馈消息重试一次
  - 二次 mismatch → 确定性回退（_wording_fallback=True，blocks=sentences）
"""
from unittest.mock import patch
from pipeline.translate import _generate_tts_script_single

LOC = {
    "full_text": "Alpha beta gamma. Delta epsilon zeta.",
    "sentences": [
        {"index": 0, "text": "Alpha beta gamma.", "source_segment_indices": [0]},
        {"index": 1, "text": "Delta epsilon zeta.", "source_segment_indices": [1]},
    ],
}
GOOD = {
    "full_text": "Alpha beta gamma. Delta epsilon zeta.",
    "blocks": [
        {"index": 0, "text": "Alpha beta gamma.", "sentence_indices": [0], "source_segment_indices": [0]},
        {"index": 1, "text": "Delta epsilon zeta.", "sentence_indices": [1], "source_segment_indices": [1]},
    ],
    "subtitle_chunks": [],
}
BAD = {**GOOD, "blocks": [dict(GOOD["blocks"][0], text="Alpha CHANGED gamma."), GOOD["blocks"][1]],
       "full_text": "Alpha CHANGED gamma. Delta epsilon zeta."}


def test_retry_recovers_wording():
    with patch("pipeline.translate._invoke_chat_for_use_case",
               side_effect=[(BAD, None), (GOOD, None)]) as call:
        result = _generate_tts_script_single(LOC, use_case="video_translate.tts_script")
    assert call.call_count == 2
    retry_messages = call.call_args_list[1].args[1]
    assert "EXACT wording" in retry_messages[-1]["content"]
    assert not result.get("_wording_fallback")


def test_double_failure_falls_back_deterministic():
    with patch("pipeline.translate._invoke_chat_for_use_case",
               side_effect=[(BAD, None), (BAD, None)]):
        result = _generate_tts_script_single(LOC, use_case="video_translate.tts_script")
    assert result["_wording_fallback"] is True
    assert [b["text"] for b in result["blocks"]] == [s["text"] for s in LOC["sentences"]]
    assert result["subtitle_chunks"]  # 重建成功
