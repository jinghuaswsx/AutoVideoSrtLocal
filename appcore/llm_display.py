"""Display helpers for LLM provider/model labels."""

from __future__ import annotations


def resolve_use_case_provider_model(use_case_code: str) -> tuple[str, str]:
    """Return the provider/model currently bound to a use case for UI display."""
    try:
        from appcore import llm_bindings

        binding = llm_bindings.resolve(use_case_code)
        provider = str(binding.get("provider") or "").strip()
        model = str(binding.get("model") or "").strip()
        if provider and model:
            return provider, model
    except Exception:
        pass

    try:
        from appcore.llm_use_cases import get_use_case

        use_case = get_use_case(use_case_code)
        provider = str(use_case.get("default_provider") or "").strip()
        model = str(use_case.get("default_model") or "").strip()
        if provider and model:
            return provider, model
    except Exception:
        pass

    return use_case_code, use_case_code


def provider_model_tag(provider: str | None, model: str | None) -> str:
    provider_text = str(provider or "").strip()
    model_text = str(model or "").strip()
    if provider_text and model_text:
        return f"{provider_text} · {model_text}"
    return provider_text or model_text
