"""向后兼容 wrapper。

新代码请使用 appcore.asr_providers.scribe.ScribeAdapter（或 appcore.asr_router）。
本模块仅保留旧 module-level 入口，避免破坏现有 import / 测试：
    - transcribe_local_audio(local_audio_path, language_code=None, *, api_key=None, model_id="scribe_v2")
    - _parse_scribe_response(payload)  ← 旧测试入口
"""
from __future__ import annotations

import logging
from typing import Dict, List

from appcore.asr_providers.scribe import (
    ScribeAdapter,
    parse_scribe_response as _parse_scribe_response,
)

log = logging.getLogger(__name__)

__all__ = ["transcribe_local_audio", "_parse_scribe_response"]


def transcribe_local_audio(
    local_audio_path: str,
    language_code: str | None = None,
    *,
    api_key: str | None = None,
    model_id: str = "scribe_v2",
) -> List[Dict]:
    """旧接口：直传本地文件给 Scribe，返回 utterances。"""
    adapter = ScribeAdapter(model_id=model_id)
    return adapter._transcribe(
        local_audio_path,
        language=language_code,
        api_key=api_key,
    )
