"""向后兼容 wrapper。

新代码请使用 appcore.asr_providers.doubao.DoubaoAdapter（或 appcore.asr_router）。
本模块仅保留两个旧函数签名，避免破坏现有 import：
    - transcribe(audio_url, volc_api_key=None) -> List[Utterance]
    - transcribe_local_audio(local_audio_path, prefix, volc_api_key=None)

文档：https://www.volcengine.com/docs/6561/1354868
"""
from __future__ import annotations

import logging
import uuid
from typing import Dict, List

from appcore.asr_providers.doubao import DoubaoAdapter

log = logging.getLogger(__name__)


def transcribe(audio_url: str, volc_api_key: str | None = None) -> List[Dict]:
    """旧接口：直接给 URL 调豆包。"""
    return DoubaoAdapter().transcribe_url(audio_url, api_key=volc_api_key)


def transcribe_local_audio(
    local_audio_path: str,
    prefix: str,
    volc_api_key: str | None = None,
) -> List[Dict]:
    """旧接口：本地文件先上传 TOS，再调豆包。

    `prefix` 仅用于命名上传 object key（保持与旧版兼容）。
    """
    from pipeline.storage import delete_file, upload_file

    object_key = f"{prefix}_{uuid.uuid4().hex[:8]}.mp3"
    audio_url = upload_file(local_audio_path, object_key)
    try:
        return DoubaoAdapter().transcribe_url(audio_url, api_key=volc_api_key)
    finally:
        try:
            delete_file(object_key)
        except Exception:
            log.warning(
                "[ASR] 清理临时音频文件失败: %s", object_key, exc_info=True
            )
