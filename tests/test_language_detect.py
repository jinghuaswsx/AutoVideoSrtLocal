"""Unit tests for pipeline.language_detect (zh/en/es trichotomy)."""
from __future__ import annotations

import pytest

from pipeline.language_detect import detect_language


class TestChinese:
    def test_pure_chinese(self):
        assert detect_language("这是一个中文带货视频，介绍我们的新产品") == "zh"

    def test_chinese_with_some_english(self):
        assert detect_language("我们的iPhone case 非常好用，质感超棒") == "zh"

    def test_short_chinese(self):
        assert detect_language("你好") == "zh"


class TestEnglish:
    def test_pure_english(self):
        assert detect_language(
            "This is an awesome product. The quality is amazing and you will love it."
        ) == "en"

    def test_english_short(self):
        assert detect_language("Hello, welcome to our shop!") == "en"

    def test_english_with_numbers(self):
        assert (
            detect_language("Get 50% off today, limited time only, shop now")
            == "en"
        )


class TestSpanish:
    def test_pure_spanish_with_accents(self):
        assert detect_language(
            "Hola amigos, hoy les traigo un producto increíble que les va a encantar."
        ) == "es"

    def test_spanish_with_inverted_punctuation(self):
        assert detect_language("¿Buscas algo nuevo? ¡Mira esto!") == "es"

    def test_spanish_stopwords_only_no_accents(self):
        # No accented chars but heavy use of Spanish stopwords
        assert detect_language(
            "El producto que tenemos es muy bueno y todos los clientes estan contentos con la compra"
        ) == "es"

    def test_spanish_ecommerce_typical(self):
        assert detect_language(
            "Mira este organizador de cocina, es perfecto para mantener todo en orden. "
            "Tiene un diseño moderno y es muy práctico."
        ) == "es"


class TestEdgeCases:
    def test_empty_string(self):
        assert detect_language("") == "zh"

    def test_whitespace_only(self):
        assert detect_language("   \n\t  ") == "zh"

    def test_pure_numbers(self):
        # No CJK, no Latin — falls back to zh default
        assert detect_language("123 456 789") == "zh"

    def test_short_ambiguous_english(self):
        # Short text without Spanish markers should default to en
        assert detect_language("Buy now") == "en"

    def test_english_with_one_spanish_char_in_brand(self):
        # Single ñ in a brand name shouldn't flip to es when context is English
        result = detect_language(
            "I love the new España jersey, the quality is excellent, "
            "I bought it yesterday and it fits perfectly"
        )
        assert result == "en"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("产品质量很好，性价比超高", "zh"),
        ("Free shipping on orders over $50, limited offer this week", "en"),
        ("Esta mochila es perfecta para viajar, la recomiendo a todos", "es"),
        ("¿Sabías que tenemos descuentos especiales hoy?", "es"),
    ],
)
def test_realistic_short_form_video_lines(text, expected):
    assert detect_language(text) == expected
