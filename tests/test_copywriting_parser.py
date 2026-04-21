import pytest

from appcore.pushes import (
    CopywritingParseError,
    parse_copywriting_body,
)


def test_parse_body_normal_english_colon():
    body = (
        "标题: Ready. Aim. LAUNCH! 🌪️\n"
        "文案: Experience the thrill! 🤩 Instant launch.\n"
        "描述: Fly High Today ✈️"
    )
    assert parse_copywriting_body(body) == {
        "title": "Ready. Aim. LAUNCH! 🌪️",
        "message": "Experience the thrill! 🤩 Instant launch.",
        "description": "Fly High Today ✈️",
    }


def test_parse_body_chinese_colon_and_whitespace():
    body = (
        "标题 ：  Hello World\n"
        "文案：  Line one\n"
        "描述 ： End"
    )
    assert parse_copywriting_body(body) == {
        "title": "Hello World",
        "message": "Line one",
        "description": "End",
    }


def test_parse_body_multiline_value():
    body = (
        "标题: Line1\n"
        "文案: Line A\n"
        "Line B\n"
        "Line C\n"
        "描述: Tail"
    )
    assert parse_copywriting_body(body) == {
        "title": "Line1",
        "message": "Line A\nLine B\nLine C",
        "description": "Tail",
    }


def test_parse_body_order_swapped():
    body = "描述: D\n标题: T\n文案: M"
    assert parse_copywriting_body(body) == {
        "title": "T",
        "message": "M",
        "description": "D",
    }


def test_parse_body_missing_title_raises():
    body = "文案: M\n描述: D"
    with pytest.raises(CopywritingParseError, match="title"):
        parse_copywriting_body(body)


def test_parse_body_missing_description_raises():
    body = "标题: T\n文案: M"
    with pytest.raises(CopywritingParseError, match="description"):
        parse_copywriting_body(body)


def test_parse_body_no_labels_raises():
    body = "just a paragraph without labels"
    with pytest.raises(CopywritingParseError, match="未找到"):
        parse_copywriting_body(body)


def test_parse_body_empty_field_raises():
    body = "标题:\n文案: M\n描述: D"
    with pytest.raises(CopywritingParseError, match="为空"):
        parse_copywriting_body(body)


def test_parse_body_empty_string_raises():
    with pytest.raises(CopywritingParseError):
        parse_copywriting_body("")


def test_parse_body_preserves_emoji_and_punctuation():
    body = (
        "标题: Ready. Aim. LAUNCH! 🌪️\n"
        "文案: Durable & crash-proof.\n"
        "描述: Fly ✈️"
    )
    parsed = parse_copywriting_body(body)
    assert parsed["title"] == "Ready. Aim. LAUNCH! 🌪️"
    assert parsed["message"] == "Durable & crash-proof."
    assert parsed["description"] == "Fly ✈️"


def test_parse_body_supports_mixed_colons_and_newlines():
    body = (
        "标题：\n"
        "Ready. Aim. LAUNCH! 🌪️\n"
        "文案:\n"
        "Experience the thrill! 🤩 Instant mechanical launch.\n"
        "Durable & crash-proof. The coolest gift for ages 3+.\n"
        "描述： Fly High Today ✈️"
    )
    assert parse_copywriting_body(body) == {
        "title": "Ready. Aim. LAUNCH! 🌪️",
        "message": (
            "Experience the thrill! 🤩 Instant mechanical launch.\n"
            "Durable & crash-proof. The coolest gift for ages 3+."
        ),
        "description": "Fly High Today ✈️",
    }
