"""Unit tests for pipeline.lang_labels."""
from __future__ import annotations

import pytest

from pipeline.lang_labels import LANG_LABELS_EN, LANG_LABELS_ZH, lang_label


def test_zh_en_es_have_english_labels():
    assert lang_label("zh") == "Chinese"
    assert lang_label("en") == "English"
    assert lang_label("es") == "Spanish"


def test_zh_en_es_have_chinese_labels():
    assert lang_label("zh", in_chinese=True) == "中文"
    assert lang_label("en", in_chinese=True) == "英文"
    assert lang_label("es", in_chinese=True) == "西班牙语"


def test_target_languages_present():
    for code in ("de", "fr", "ja", "pt", "it", "nl", "sv", "fi"):
        assert lang_label(code) != code, f"missing English label for {code}"
        assert lang_label(code, in_chinese=True) != code, f"missing Chinese label for {code}"


def test_unknown_code_falls_back_to_code():
    assert lang_label("xx") == "xx"
    assert lang_label("xx", in_chinese=True) == "xx"


def test_dicts_align():
    assert set(LANG_LABELS_EN.keys()) == set(LANG_LABELS_ZH.keys())
