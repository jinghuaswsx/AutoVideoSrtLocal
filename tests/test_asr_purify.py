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


def test_purify_majority_non_main_keeps_all():
    """多数派保护：当多数段（≥60%）被判非主语言时，认为是 source_language
    与实际音频不一致（不是局部污染），原样保留 ASR 输出而不是清空。

    场景：用户填 source_language=es，但音频实际是中文，豆包识别出全中文段。
    旧逻辑会把所有段都删掉 → 下游报"未检测到语音"。新逻辑保留原段落，
    让下游尽量利用现有 ASR 结果。
    """
    utts: List[Utterance] = [
        _u("你好这是中文污染段落啊啊啊啊", 0.0, 2.0),
        _u("再来一段中文污染应该也被删除掉啊啊", 2.0, 4.0),
    ]
    out = purify_language(utts, source_language="es")
    assert len(out) == 2
    assert out[0]["text"] == "你好这是中文污染段落啊啊啊啊"
    assert out[1]["text"] == "再来一段中文污染应该也被删除掉啊啊"


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


# -------------------- 多数派保护：source_language 与音频不一致 --------------------

def test_purify_keeps_all_when_majority_is_non_main(monkeypatch):
    """source_language=zh 但音频实际是西语 → 豆包硬识别成乱码英文 →
    fast-langdetect 全标 en → 旧逻辑会把所有段都删掉，新逻辑应原样保留。
    """
    # 6 段都被 fast-langdetect 判成 en，但 source_language=zh
    monkeypatch.setattr(
        "appcore.asr_purify.detect_language",
        lambda text: ("en", 0.95),
    )
    utts = [
        _u(f"long enough sentence number {i}.", float(i) * 2.0, float(i + 1) * 2.0)
        for i in range(6)
    ]
    out = purify_language(utts, source_language="zh")
    # 多数派保护触发 → 原样返回，不一刀切
    assert len(out) == 6
    for original, kept in zip(utts, out):
        assert kept["text"] == original["text"]


def test_purify_still_cleans_minority_pollution(monkeypatch):
    """正常的局部污染场景：5 段中文 + 1 段英文乱码，应正确清掉那 1 段。"""

    def _fake_detect(text: str):
        if "你好" in text:
            return ("zh", 0.99)
        return ("en", 0.95)

    monkeypatch.setattr("appcore.asr_purify.detect_language", _fake_detect)
    utts = [
        _u(f"你好世界这是中文 {i} 段足够长的句子。", float(i) * 2.0, float(i + 1) * 2.0)
        for i in range(5)
    ]
    utts.append(_u("background noise garbage en pollution.", 10.0, 12.0))
    out = purify_language(utts, source_language="zh")
    # 5/6 段中文，污染段在阈值之下 → 正常清理
    assert len(out) == 5
    for kept in out:
        assert "你好" in kept["text"]
