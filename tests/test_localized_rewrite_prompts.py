"""Tests for language-specific rewrite prompts / messages builders."""
import pytest


class TestEnglishRewritePrompt:
    def test_prompt_contains_rewrite_instructions(self):
        from pipeline.localization import LOCALIZED_REWRITE_SYSTEM_PROMPT
        assert "REWRITING" in LOCALIZED_REWRITE_SYSTEM_PROMPT.upper()
        assert "target character count" in LOCALIZED_REWRITE_SYSTEM_PROMPT.lower()
        assert "shrink" in LOCALIZED_REWRITE_SYSTEM_PROMPT.lower()
        assert "expand" in LOCALIZED_REWRITE_SYSTEM_PROMPT.lower()

    def test_prompt_inherits_original_style_rules(self):
        """Rewrite prompt must preserve hook / CTA / structure rules from original."""
        from pipeline.localization import (
            LOCALIZED_REWRITE_SYSTEM_PROMPT,
            LOCALIZED_TRANSLATION_SYSTEM_PROMPT,
        )
        # Key rules from original should be restated:
        assert "source_segment_indices" in LOCALIZED_REWRITE_SYSTEM_PROMPT
        assert "JSON" in LOCALIZED_REWRITE_SYSTEM_PROMPT

    def test_builder_injects_target_chars_and_direction(self):
        from pipeline.localization import build_localized_rewrite_messages
        msgs = build_localized_rewrite_messages(
            source_full_text="Hello world. This is source.",
            prev_localized_translation={
                "full_text": "Bonjour monde.",
                "sentences": [{"index": 0, "text": "Bonjour monde.", "source_segment_indices": [0]}],
            },
            target_chars=200,
            direction="shrink",
            source_language="zh",
        )
        assert len(msgs) == 2
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"
        user_content = msgs[1]["content"]
        assert "200" in user_content
        assert "shrink" in user_content.lower()
        assert "Bonjour monde." in user_content

    def test_builder_respects_source_language_label(self):
        from pipeline.localization import build_localized_rewrite_messages
        msgs_zh = build_localized_rewrite_messages(
            source_full_text="中文原文",
            prev_localized_translation={"full_text": "x", "sentences": [{"index": 0, "text": "x", "source_segment_indices": [0]}]},
            target_chars=100, direction="shrink", source_language="zh",
        )
        msgs_en = build_localized_rewrite_messages(
            source_full_text="English source",
            prev_localized_translation={"full_text": "x", "sentences": [{"index": 0, "text": "x", "source_segment_indices": [0]}]},
            target_chars=100, direction="shrink", source_language="en",
        )
        assert "Chinese" in msgs_zh[1]["content"]
        assert "English" in msgs_en[1]["content"]
