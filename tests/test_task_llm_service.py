from __future__ import annotations

import pytest


@pytest.mark.parametrize(
    ("provider", "expected"),
    [
        ("doubao", "doubao"),
        ("vertex_adc_gemini_31_pro", "gemini_vertex_adc"),
        ("vertex_gemini_31_pro", "gemini_vertex"),
        ("gpt_5_mini", "openrouter"),
        ("openrouter", "openrouter"),
        ("claude_sonnet", "openrouter"),
    ],
)
def test_resolve_translate_billing_provider_maps_retranslate_providers(provider, expected):
    from web.services.task_llm import resolve_translate_billing_provider

    assert resolve_translate_billing_provider(provider) == expected
