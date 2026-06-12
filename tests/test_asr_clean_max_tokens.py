"""Block 2: asr_clean max_tokens 动态估算 — 单元测试（TDD 红阶段）。

Task 4: _estimate_max_tokens 函数边界测试
"""
from pipeline.asr_clean import _estimate_max_tokens


def test_small_input_floor():
    assert _estimate_max_tokens([{"text": "hi"}]) == 4000


def test_large_input_scales_and_caps():
    utts = [{"text": "word " * 200} for _ in range(120)]
    assert _estimate_max_tokens(utts) == 16000


def test_medium_input_between_bounds():
    utts = [{"text": "a" * 100} for _ in range(40)]
    v = _estimate_max_tokens(utts)
    assert 4000 < v < 16000
