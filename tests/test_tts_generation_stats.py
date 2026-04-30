"""tts_generation_stats utils 测试。

聚焦 compute_summary 的口径正确性、format_log_line 的 ANSI 输出、
upsert 的 UPSERT 行为，以及 finalize 在异常路径下的容错。
"""
from __future__ import annotations

import pytest


def _round(rewrite_attempts: int = 0, audio_segments: int = 0) -> dict:
    """构造一个最简的 round_record。round 1 没有 rewrite_attempts。"""
    rec: dict = {"audio_segments_total": audio_segments}
    if rewrite_attempts > 0:
        rec["rewrite_attempts"] = [{"attempt": i + 1} for i in range(rewrite_attempts)]
    return rec


def test_compute_summary_round1_only_initial_translate():
    from appcore.tts_generation_stats import compute_summary
    rounds = [_round(rewrite_attempts=0, audio_segments=9)]
    summary = compute_summary(rounds)
    assert summary["translate_calls"] == 1
    assert summary["audio_calls"] == 9


def test_compute_summary_multi_round_aggregates_rewrite_and_segments():
    from appcore.tts_generation_stats import compute_summary
    rounds = [
        _round(rewrite_attempts=0, audio_segments=9),
        _round(rewrite_attempts=2, audio_segments=9),
        _round(rewrite_attempts=5, audio_segments=10),
    ]
    summary = compute_summary(rounds)
    assert summary["translate_calls"] == 8
    assert summary["audio_calls"] == 28


def test_compute_summary_handles_missing_audio_segments_total():
    from appcore.tts_generation_stats import compute_summary
    rounds = [{"rewrite_attempts": []}]
    summary = compute_summary(rounds)
    assert summary["translate_calls"] == 1
    assert summary["audio_calls"] == 0


def test_compute_summary_empty_rounds():
    from appcore.tts_generation_stats import compute_summary
    summary = compute_summary([])
    assert summary["translate_calls"] == 0
    assert summary["audio_calls"] == 0


def test_format_log_line_contains_counts_and_ansi():
    from appcore.tts_generation_stats import format_log_line
    line = format_log_line({"translate_calls": 4, "audio_calls": 27})
    assert "\033[1;34m" in line
    assert "\033[0m" in line
    assert "4 次翻译" in line
    assert "27 次语音生成" in line


def test_format_log_line_zero_counts():
    from appcore.tts_generation_stats import format_log_line
    line = format_log_line({"translate_calls": 0, "audio_calls": 0})
    assert "0 次翻译" in line
    assert "0 次语音生成" in line
