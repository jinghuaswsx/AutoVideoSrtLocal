"""Provider Adapter 抽象基类。

每个 Adapter 实现 chat() 和 generate() 两个方法：
  - chat: OpenAI messages 风格，返回 {"text", "raw", "usage"}
  - generate: prompt + 可选 media/schema，返回 {"text" or "json", "raw", "usage"}

Adapter 不做 UseCase → provider 路由，只负责 provider 级 HTTP 调用和 usage 规范化。
上层 appcore.llm_client.invoke_* 负责 binding 解析 + adapter 选路。
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class LLMAdapter(ABC):
    provider_code: str = ""

    @abstractmethod
    def resolve_credentials(self, user_id: int | None) -> dict:
        """返回 {'api_key': str, 'base_url': str|None, 'extra': dict}。"""
        ...

    def chat(
        self,
        *,
        model: str,
        messages: list[dict],
        user_id: int | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict | None = None,
        extra_body: dict | None = None,
    ) -> dict:
        raise NotImplementedError(f"{self.provider_code} does not support chat()")

    def generate(
        self,
        *,
        model: str,
        prompt: str,
        user_id: int | None = None,
        system: str | None = None,
        media=None,
        response_schema: dict | None = None,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
        google_search: bool | None = None,
    ) -> dict:
        raise NotImplementedError(f"{self.provider_code} does not support generate()")
