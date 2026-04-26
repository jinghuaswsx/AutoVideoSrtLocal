"""ASR 语言污染清理单元测试。"""
from __future__ import annotations

from typing import List

import pytest

from appcore.asr_providers import Utterance
from appcore.asr_purify import (
    _merge_adjacent,
    _normalize_lang_code,
    _too_short_to_judge,
    detect_language,
    purify_language,
)


def _u(text: str, start: float, end: float) -> Utterance:
    return {"text": text, "start_time": start, "end_time": end, "words": []}


# -------------------- _normalize_lang_code --------------------

def test_normalize_lang_code():
    assert _normalize_lang_code("zh") == "zh"
    assert _normalize_lang_code("zh-Hans") == "zh"
    assert _normalize_lang_code("zh_TW") == "zh"
    assert _normalize_lang_code("en-US") == "en"
    assert _normalize_lang_code("ES") == "es"
    assert _normalize_lang_code("") == ""


# -------------------- _too_short_to_judge --------------------

def test_too_short_text_is_kept():
    # < 8 字符
    assert _too_short_to_judge(_u("OK", 0.0, 5.0)) is True
    assert _too_short_to_judge(_u("Sí", 0.0, 5.0)) is True


def test_too_short_duration_is_kept():
    # < 1.5 秒
    assert _too_short_to_judge(_u("Hello world!!!", 0.0, 0.5)) is True


def test_long_enough_text_and_duration_is_judged():
    assert _too_short_to_judge(_u("Hola mundo, esta es una prueba", 0.0, 3.0)) is False


# -------------------- detect_language --------------------

def test_detect_language_spanish_long_text():
    out = detect_language("Hola amigo, esto es una prueba en español")
    assert out is not None
    lang, score = out
    assert lang == "es"
    assert score > 0.5


def test_detect_language_chinese():
    out = detect_language("你好世界，这是一段中文测试文本")
    assert out is not None
    lang, _ = out
    assert lang == "zh"


def test_detect_language_too_short_returns_none():
    assert detect_language("OK") is None
    assert detect_language("") is None
    assert detect_language("   ") is None


def test_detect_language_normalizes_code(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "fast_langdetect.detect",
        lambda text, low_memory=True: {"lang": "zh-Hans", "score": 0.99},
    )
    out = detect_language("一段足够长的文本以触发检测")
    assert out is not None
    assert out[0] == "zh"


def test_detect_language_handles_exception(monkeypatch: pytest.MonkeyPatch, caplog):
    def _boom(text, low_memory=True):
        raise RuntimeError("boom")

    monkeypatch.setattr("fast_langdetect.detect", _boom)
    assert detect_language("足够长的测试文本以触发检测路径啊") is None


# -------------------- purify_language --------------------

def test_purify_skips_when_source_language_empty():
    utts: List[Utterance] = [_u("hello", 0.0, 1.0), _u("world", 1.0, 2.0)]
    out = purify_language(utts, source_language=None)
    assert out == utts
    out2 = purify_language(utts, source_language="auto")
    assert out2 == utts


def test_purify_keeps_all_main_language_segments():
    utts: List[Utterance] = [
        _u("Hola amigo, esto es una prueba en español", 0.0, 3.0),
        _u("Adiós a todos, hasta luego", 3.0, 5.0),
    ]
    out = purify_language(utts, source_language="es")
    assert len(out) == 2
    assert out[0]["text"].startswith("Hola")


def test_purify_drops_chinese_pollution_in_spanish_video():
    utts: List[Utterance] = [
        _u("Hola amigo, esto es una prueba en español", 0.0, 3.0),
        _u("你好这是中文污染段落，应当被删除", 3.0, 5.0),
        _u("Adiós a todos, hasta luego en español", 5.0, 7.0),
    ]
    out = purify_language(utts, source_language="es")
    assert len(out) == 2
    assert all("你好" not in u["text"] for u in out)
    # 时间合并：第二段被删，时间并入前一段
    assert out[0]["end_time"] == pytest.approx(5.0)
    assert out[1]["start_time"] == pytest.approx(5.0)
    assert out[1]["end_time"] == pytest.approx(7.0)


def test_purify_keeps_short_segments_even_if_other_language():
    utts: List[Utterance] = [
        _u("Hola mundo, esto es una prueba muy larga", 0.0, 3.0),
        _u("OK", 3.0, 4.0),  # 太短
        _u("Continúa la grabación en español aquí", 4.0, 6.0),
    ]
    out = purify_language(utts, source_language="es")
    assert len(out) == 3  # 短段保留


def test_purify_first_segment_dropped_merges_to_next():
    utts: List[Utterance] = [
        _u("你好这是中文污染段落，应当被删除", 0.0, 2.0),
        _u("Hola amigo, esto es una prueba en español", 2.0, 5.0),
    ]
    out = purify_language(utts, source_language="es")
    assert len(out) == 1
    # 首段被删 → 时间并到下一段：start_time 提到 0.0
    assert out[0]["start_time"] == pytest.approx(0.0)
    assert out[0]["end_time"] == pytest.approx(5.0)


def test_purify_consecutive_drops_accumulate():
    utts: List[Utterance] = [
        _u("Hola amigo, esto es una prueba en español", 0.0, 3.0),
        # 连续两段中文（每段 ≥ 1.5s + ≥ 8 字符，才会被语言判定）
        _u("你好这是中文污染段落啊啊啊啊啊", 3.0, 5.0),
        _u("再来一段中文污染应该也被删除掉啊啊", 5.0, 7.0),
        _u("Adiós a todos, hasta luego en español", 7.0, 9.0),
    ]
    out = purify_language(utts, source_language="es")
    assert len(out) == 2
    # 前一段 end_time 累积合并到 7.0
    assert out[0]["end_time"] == pytest.approx(7.0)
    assert out[1]["start_time"] == pytest.approx(7.0)


def test_purify_all_segments_dropped_returns_empty():
    utts: List[Utterance] = [
        _u("你好这是中文污染段落啊啊啊啊", 0.0, 2.0),
        _u("再来一段中文污染应该也被删除掉啊啊", 2.0, 4.0),
    ]
    out = purify_language(utts, source_language="es")
    assert out == []


def test_purify_low_confidence_keeps_segment(monkeypatch: pytest.MonkeyPatch):
    """fast-langdetect 返回低置信度时不删，避免误杀。"""
    monkeypatch.setattr(
        "appcore.asr_purify.detect_language",
        lambda text: ("zh", 0.3),  # 低于 MIN_CONFIDENCE=0.5
    )
    utts: List[Utterance] = [
        _u("Hola amigo, esto es una prueba en español", 0.0, 3.0),
    ]
    out = purify_language(utts, source_language="es")
    assert len(out) == 1


# -------------------- _merge_adjacent --------------------

def test_merge_adjacent_no_drops_returns_input():
    utts = [_u("a", 0.0, 1.0), _u("b", 1.0, 2.0)]
    out = _merge_adjacent(utts, [False, False])
    assert len(out) == 2


def test_merge_adjacent_middle_drop():
    utts = [_u("a", 0.0, 1.0), _u("b", 1.0, 2.0), _u("c", 2.0, 3.0)]
    out = _merge_adjacent(utts, [False, True, False])
    assert len(out) == 2
    assert out[0]["end_time"] == 2.0  # b 的 end_time 并入 a
    assert out[1]["start_time"] == 2.0


def test_merge_adjacent_first_drop():
    utts = [_u("a", 0.0, 1.0), _u("b", 1.0, 2.0)]
    out = _merge_adjacent(utts, [True, False])
    assert len(out) == 1
    assert out[0]["start_time"] == 0.0  # a 的 start_time 提前到 b
    assert out[0]["end_time"] == 2.0


def test_merge_adjacent_all_drops():
    utts = [_u("a", 0.0, 1.0), _u("b", 1.0, 2.0)]
    out = _merge_adjacent(utts, [True, True])
    assert out == []
