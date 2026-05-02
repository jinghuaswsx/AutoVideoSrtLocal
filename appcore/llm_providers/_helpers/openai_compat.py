"""OpenAI 兼容客户端薄封装（OpenRouter / 豆包 LLM 共用）。

`pipeline.translate._call_openai_compat` 历史上自己 import openai SDK 创建
client；C-3 把客户端创建迁到这里，pipeline.translate 不再 `from openai import
OpenAI`。helper 不做 use-case 路由 / ai_billing；上层（`pipeline.translate`
老兼容路径 / 评测脚本）自管。

所有传入 (api_key, base_url) 由调用方决定（来自 `llm_provider_configs` 或
`api_keys` user-level 覆盖），helper 不读 DB。
"""
from __future__ import annotations

from openai import OpenAI


def make_openai_compat_client(*, api_key: str, base_url: str) -> OpenAI:
    """创建 OpenAI 兼容（OpenRouter / 豆包 LLM）客户端。"""
    return OpenAI(api_key=api_key, base_url=base_url)


__all__ = ["make_openai_compat_client"]
