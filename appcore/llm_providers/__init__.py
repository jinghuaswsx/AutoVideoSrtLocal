"""Provider Adapters 注册表。

LLM 统一调用入口 appcore.llm_client 按 provider_code 取 Adapter，
Adapter 负责具体的 HTTP 调用和 usage 规范化。

provider_code 枚举：
    openrouter      - OpenAI-compatible (OpenRouter)
    doubao          - OpenAI-compatible (火山引擎 ARK)
    gemini_aistudio - Google AI Studio (genai.Client(api_key=...))
    gemini_vertex   - Google Cloud Express Mode (genai.Client(vertexai=True, ...))
"""
from appcore.llm_providers.base import LLMAdapter
from appcore.llm_providers.gemini_aistudio_adapter import GeminiAIStudioAdapter
from appcore.llm_providers.gemini_vertex_adapter import GeminiVertexAdapter
from appcore.llm_providers.openrouter_adapter import DoubaoAdapter, OpenRouterAdapter

PROVIDER_ADAPTERS: dict[str, LLMAdapter] = {
    "openrouter": OpenRouterAdapter(),
    "doubao": DoubaoAdapter(),
    "gemini_aistudio": GeminiAIStudioAdapter(),
    "gemini_vertex": GeminiVertexAdapter(),
}


def get_adapter(provider_code: str) -> LLMAdapter:
    if provider_code not in PROVIDER_ADAPTERS:
        raise KeyError(f"unknown provider: {provider_code}")
    return PROVIDER_ADAPTERS[provider_code]


__all__ = ["LLMAdapter", "PROVIDER_ADAPTERS", "get_adapter"]
