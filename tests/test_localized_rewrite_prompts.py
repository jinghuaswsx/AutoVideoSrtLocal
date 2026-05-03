"""Tests for language-specific rewrite prompts / messages builders."""
import pytest


class TestEnglishRewritePrompt:
    def test_prompt_contains_rewrite_instructions(self):
        from pipeline.localization import LOCALIZED_REWRITE_SYSTEM_PROMPT
        assert "REWRITING" in LOCALIZED_REWRITE_SYSTEM_PROMPT.upper()
        assert "target word count" in LOCALIZED_REWRITE_SYSTEM_PROMPT.lower()
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
            target_words=200,
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
            target_words=100, direction="shrink", source_language="zh",
        )
        msgs_en = build_localized_rewrite_messages(
            source_full_text="English source",
            prev_localized_translation={"full_text": "x", "sentences": [{"index": 0, "text": "x", "source_segment_indices": [0]}]},
            target_words=100, direction="shrink", source_language="en",
        )
        assert "Chinese" in msgs_zh[1]["content"]
        assert "English" in msgs_en[1]["content"]


class TestGermanRewritePrompt:
    def test_prompt_inherits_german_localization_rules(self):
        from pipeline.localization_de import LOCALIZED_REWRITE_SYSTEM_PROMPT
        # German-specific rules must persist
        assert "German" in LOCALIZED_REWRITE_SYSTEM_PROMPT or "Deutsch" in LOCALIZED_REWRITE_SYSTEM_PROMPT
        assert "DACH" in LOCALIZED_REWRITE_SYSTEM_PROMPT or "Germans" in LOCALIZED_REWRITE_SYSTEM_PROMPT
        # rewrite constraints
        assert "target" in LOCALIZED_REWRITE_SYSTEM_PROMPT.lower()
        assert "shrink" in LOCALIZED_REWRITE_SYSTEM_PROMPT.lower()
        assert "expand" in LOCALIZED_REWRITE_SYSTEM_PROMPT.lower()

    def test_builder_for_german(self):
        from pipeline.localization_de import build_localized_rewrite_messages
        msgs = build_localized_rewrite_messages(
            source_full_text="Source text",
            prev_localized_translation={
                "full_text": "Hallo Welt.",
                "sentences": [{"index": 0, "text": "Hallo Welt.", "source_segment_indices": [0]}],
            },
            target_words=300, direction="expand", source_language="en",
        )
        assert "300" in msgs[1]["content"]
        assert "expand" in msgs[1]["content"].lower()
        assert "Hallo Welt" in msgs[1]["content"]
        assert "English" in msgs[1]["content"]


class TestFrenchRewritePrompt:
    def test_prompt_inherits_french_elision_rules(self):
        from pipeline.localization_fr import LOCALIZED_REWRITE_SYSTEM_PROMPT
        # French-specific rules must persist
        text = LOCALIZED_REWRITE_SYSTEM_PROMPT.lower()
        assert "french" in text or "français" in text
        assert "élision" in LOCALIZED_REWRITE_SYSTEM_PROMPT or "elision" in text
        # rewrite constraints
        assert "target" in text
        assert "shrink" in text
        assert "expand" in text

    def test_builder_for_french(self):
        from pipeline.localization_fr import build_localized_rewrite_messages
        msgs = build_localized_rewrite_messages(
            source_full_text="Source",
            prev_localized_translation={
                "full_text": "C'est super.",
                "sentences": [{"index": 0, "text": "C'est super.", "source_segment_indices": [0]}],
            },
            target_words=250, direction="shrink", source_language="zh",
        )
        assert "250" in msgs[1]["content"]
        assert "shrink" in msgs[1]["content"].lower()
        assert "Chinese" in msgs[1]["content"]


class TestGenerateLocalizedRewrite:
    def test_rewrite_calls_llm_with_custom_messages_builder(self, monkeypatch):
        """generate_localized_rewrite 必须走语言专属 messages_builder 路径。

        D-4 之后 generate_localized_rewrite 走 invoke_chat（use_case 必传），
        本测试 patch llm_client.invoke_chat 替代老 resolve_provider_config。
        """
        from pipeline import translate

        captured = {}

        def fake_invoke_chat(use_case_code, **kwargs):
            captured["use_case_code"] = use_case_code
            captured["messages"] = kwargs.get("messages")
            return {
                "json": {
                    "full_text": "Short.",
                    "sentences": [
                        {"index": 0, "text": "Short.", "source_segment_indices": [0]}
                    ],
                },
                "text": None,
                "raw": None,
                "usage": {"input_tokens": 10, "output_tokens": 5},
            }

        monkeypatch.setattr("appcore.llm_client.invoke_chat", fake_invoke_chat)

        from pipeline.localization_de import build_localized_rewrite_messages
        result = translate.generate_localized_rewrite(
            source_full_text="Source",
            prev_localized_translation={
                "full_text": "Hallo.",
                "sentences": [{"index": 0, "text": "Hallo.", "source_segment_indices": [0]}],
            },
            target_words=50,
            direction="shrink",
            source_language="en",
            messages_builder=build_localized_rewrite_messages,
            use_case="video_translate.rewrite",
        )
        assert result["full_text"] == "Short."
        assert len(result["sentences"]) == 1
        assert captured["use_case_code"] == "video_translate.rewrite"
        # Confirm messages_builder was called with rewrite-specific args
        assert "50" in captured["messages"][1]["content"]
        assert "shrink" in captured["messages"][1]["content"].lower()
        assert "Hallo." in captured["messages"][1]["content"]
        # usage was attached
        assert result["_usage"]["input_tokens"] == 10
        assert result["_usage"]["output_tokens"] == 5
