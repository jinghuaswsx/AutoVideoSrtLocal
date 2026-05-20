from __future__ import annotations

import pytest


@pytest.mark.parametrize(
    ("provider", "expected"),
    [
        ("doubao", "doubao"),
        ("vertex_adc_gemini_35_flash", "gemini_aistudio"),
        ("vertex_gemini_35_flash", "gemini_vertex"),
        ("gpt_5_mini", "openrouter"),
        ("openrouter", "openrouter"),
        ("claude_sonnet", "openrouter"),
    ],
)
def test_resolve_translate_billing_provider_maps_retranslate_providers(provider, expected):
    from web.services.task_llm import resolve_translate_billing_provider

    assert resolve_translate_billing_provider(provider) == expected
