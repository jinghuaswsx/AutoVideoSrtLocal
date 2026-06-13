"""Block1: build_localized_translation_messages 源语言动态标签测试。
Spec: docs/superpowers/specs/2026-06-12-omni-quality-block1-prompt-correctness-design.md
"""
from pipeline.localization import build_localized_translation_messages

SEGS = [{"index": 0, "text": "hola"}]


def test_source_language_label_dynamic():
    msgs = build_localized_translation_messages("hola", SEGS, source_language="es")
    user = msgs[1]["content"]
    assert "Source Spanish full text" in user
    assert "Chinese" not in user


def test_source_language_default_keeps_chinese():
    msgs = build_localized_translation_messages("你好", SEGS)
    assert "Source Chinese full text" in msgs[1]["content"]
