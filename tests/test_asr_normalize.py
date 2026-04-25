"""pipeline.asr_normalize 单元测试。"""
from __future__ import annotations

import pytest


def test_module_exports_required_symbols():
    from pipeline import asr_normalize
    assert hasattr(asr_normalize, "DETECT_SUPPORTED_LANGS")
    assert hasattr(asr_normalize, "LOW_CONFIDENCE_THRESHOLD")
    assert hasattr(asr_normalize, "LANG_LABELS")
    assert hasattr(asr_normalize, "DetectLanguageFailedError")
    assert hasattr(asr_normalize, "UnsupportedSourceLanguageError")
    assert hasattr(asr_normalize, "TranslateOutputInvalidError")
    assert hasattr(asr_normalize, "detect_language")
    assert hasattr(asr_normalize, "translate_to_en")
    assert hasattr(asr_normalize, "run_asr_normalize")


def test_detect_supported_langs_excludes_other():
    from pipeline.asr_normalize import DETECT_SUPPORTED_LANGS
    assert DETECT_SUPPORTED_LANGS == ("en", "zh", "es", "pt", "fr", "it", "ja", "nl", "sv", "fi")
    assert "other" not in DETECT_SUPPORTED_LANGS


def test_low_confidence_threshold_is_06():
    from pipeline.asr_normalize import LOW_CONFIDENCE_THRESHOLD
    assert LOW_CONFIDENCE_THRESHOLD == 0.6


def test_lang_labels_covers_all_supported():
    from pipeline.asr_normalize import LANG_LABELS, DETECT_SUPPORTED_LANGS
    for code in DETECT_SUPPORTED_LANGS:
        assert code in LANG_LABELS, f"LANG_LABELS missing {code!r}"
    assert LANG_LABELS["zh"] == "中文"
    assert LANG_LABELS["es"] == "西班牙语"
    assert LANG_LABELS["en"] == "英语"
