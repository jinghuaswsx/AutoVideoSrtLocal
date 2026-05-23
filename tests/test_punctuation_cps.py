import pytest
from unittest.mock import patch
from pipeline import duration_reconcile_v2

@patch("pipeline.speech_rate_model.get_effective_rate")
def test_compute_target_chars_range_v2_punctuation_deduction(mock_get_rate):
    """验证标点停顿感知的有效时长精确扣减"""
    # 假设 CPS 为 10 字符/秒
    mock_get_rate.return_value = 10.0

    # 场景一：无标点符号，10秒时长，预期lo/hi为 (10 * 10 * 0.92) = 92 到 (10 * 10 * 1.08) = 108
    lo1, hi1 = duration_reconcile_v2.compute_target_chars_range_v2(
        target_duration=10.0,
        voice_id="voice1",
        target_language="en",
        source_text="No punctuation at all in this text"
    )
    assert lo1 == 92
    assert hi1 == 108

    # 场景二：含2个逗号，2个句号。扣减 = 2 * 0.15 + 2 * 0.30 = 0.90 秒。
    # 有效时长 = 9.10 秒。预期范围lo/hi为 (10 * 9.1 * 0.92) = 83 到 (10 * 9.1 * 1.08 + 0.5) = 98
    lo2, hi2 = duration_reconcile_v2.compute_target_chars_range_v2(
        target_duration=10.0,
        voice_id="voice1",
        target_language="en",
        source_text="Hello, this is a test, which has punctuation. Yes."
    )
    assert lo2 == 83
    assert hi2 == 98


@patch("pipeline.speech_rate_model.get_effective_rate")
def test_predict_tts_duration_punctuation_aware(mock_get_rate):
    """验证本地声学时长预测把标点符号计入发音停顿时间"""
    # 假设 CPS 为 10
    mock_get_rate.return_value = 10.0

    # 场景一：纯文字，20个字，预期 20 / 10 = 2.0 秒
    t1 = duration_reconcile_v2.predict_tts_duration(
        text="Twenty letters exactly",
        voice_id="voice1",
        target_language="en",
    )
    assert abs(t1 - 2.2) < 0.01  # length = 22, so 2.2s

    # 场景二：含标点
    # 文字部分 "Hello, world!" 长度 13，纯发音时间 = 1.3 秒。
    # 标点包含 1个逗号(0.15s), 1个叹号(0.3s)。
    # 预测时长 = 1.3 + 0.15 + 0.3 = 1.75s
    t2 = duration_reconcile_v2.predict_tts_duration(
        text="Hello, world!",
        voice_id="voice1",
        target_language="en",
    )
    assert abs(t2 - (13 / 10.0 + 0.15 + 0.30)) < 0.01

    # 场景三：配音变速。2倍速时文字发音减半：1.3 / 2.0 = 0.65 秒。加上停顿 0.45 秒 = 1.1 秒。
    t3 = duration_reconcile_v2.predict_tts_duration(
        text="Hello, world!",
        voice_id="voice1",
        target_language="en",
        speed=2.0
    )
    assert abs(t3 - (13 / 20.0 + 0.15 + 0.30)) < 0.01
