"""ASR adapter base class + 公共类型。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, TypedDict


class WordTimestamp(TypedDict):
    text: str
    start_time: float
    end_time: float
    confidence: float


class Utterance(TypedDict):
    text: str
    start_time: float
    end_time: float
    words: List[WordTimestamp]


@dataclass(frozen=True)
class ASRCapabilities:
    """Adapter 能力声明，用于 router/purify 决策。"""

    supports_force_language: bool
    supported_languages: frozenset[str]  # ISO-639-1；包含 "*" 表示全部
    accepts_local_file: bool             # True=直传本地，False=要先上传 URL

    def supports_language(self, language: str | None) -> bool:
        if language is None:
            return True
        if "*" in self.supported_languages:
            return True
        return language in self.supported_languages


class BaseASRAdapter:
    """统一 ASR adapter 接口。

    子类需声明 provider_code / display_name / capabilities / default_model_id，
    并实现 transcribe(local_audio_path, language=None) -> List[Utterance]。
    """

    provider_code: str = ""
    display_name: str = ""
    capabilities: ASRCapabilities
    default_model_id: str = ""

    def __init__(self, model_id: str | None = None) -> None:
        self.model_id = (model_id or self.default_model_id or "").strip()

    def transcribe(
        self,
        local_audio_path: Path,
        language: str | None = None,
    ) -> List[Utterance]:
        raise NotImplementedError

    def __repr__(self) -> str:  # pragma: no cover - debug only
        return f"<{type(self).__name__} provider={self.provider_code} model={self.model_id}>"
