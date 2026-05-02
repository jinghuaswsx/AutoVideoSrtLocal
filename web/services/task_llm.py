"""Task route LLM helpers."""

from __future__ import annotations


def resolve_translate_billing_provider(provider: str) -> str:
    if provider == "doubao":
        return "doubao"
    if provider.startswith("vertex_adc_"):
        return "gemini_vertex_adc"
    if provider.startswith("vertex_"):
        return "gemini_vertex"
    return "openrouter"
