"""豆包 SeedASR v3 adapter。

API：https://www.volcengine.com/docs/6561/1354868
- POST submit → 返回 X-Api-Status-Code 头
- POST query → 轮询直到 20000000 (成功) / 20000003 (静音空段)
- 输入只接受音频 URL（不接受 base64），因此本 adapter 在 transcribe(local_path)
  入口里会先把文件上传到 TOS 再走 URL 接口，结束后清理。

强制语言：豆包接口当前不支持 decoder force language，supports_force_language=False，
language 参数被忽略；多语言污染要靠 fallback adapter 重转兜底。
"""
from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from typing import Dict, List

import requests

from appcore.llm_provider_configs import (
    ProviderConfigError,
    require_provider_config,
)
from config import TOS_ASR_BUCKET, VOLC_ASR_QUERY_URL, VOLC_ASR_SUBMIT_URL

from .base import ASRCapabilities, BaseASRAdapter, Utterance

log = logging.getLogger(__name__)

_DEFAULT_VOLC_RESOURCE_ID = "volc.seedasr.auc"
_DEFAULT_MODEL_ID = "bigmodel"
_POLL_INTERVAL_SEC = 3
_POLL_MAX_RETRIES = 100  # ~5 分钟


def _resolve_doubao_asr_key() -> str:
    try:
        return require_provider_config("doubao_asr").require_api_key()
    except ProviderConfigError as exc:
        raise RuntimeError(str(exc)) from exc


def _resolve_doubao_asr_resource_id() -> str:
    try:
        cfg = require_provider_config("doubao_asr")
    except ProviderConfigError:
        return _DEFAULT_VOLC_RESOURCE_ID
    extra = cfg.extra_config or {}
    return (extra.get("resource_id") or "").strip() or _DEFAULT_VOLC_RESOURCE_ID


class DoubaoAdapter(BaseASRAdapter):
    provider_code = "doubao_asr"
    display_name = "火山豆包 SeedASR"
    default_model_id = _DEFAULT_MODEL_ID
    capabilities = ASRCapabilities(
        supports_force_language=False,
        supported_languages=frozenset({"zh", "en"}),
        accepts_local_file=False,
    )

    def transcribe(
        self,
        local_audio_path: Path,
        language: str | None = None,
    ) -> List[Utterance]:
        del language  # 豆包不支持 decoder force language
        from pipeline.storage import delete_file, upload_file

        local_path = str(local_audio_path)
        object_key = f"asr_doubao_{uuid.uuid4().hex[:12]}.mp3"
        audio_url = upload_file(local_path, object_key, bucket=TOS_ASR_BUCKET)
        try:
            return self.transcribe_url(audio_url)
        finally:
            try:
                delete_file(object_key, bucket=TOS_ASR_BUCKET)
            except Exception:
                log.warning("[Doubao] 清理临时音频失败: %s", object_key, exc_info=True)

    def transcribe_url(
        self,
        audio_url: str,
        api_key: str | None = None,
    ) -> List[Utterance]:
        request_id = str(uuid.uuid4())
        resolved_key = api_key or _resolve_doubao_asr_key()
        log.info(
            "[Doubao] 开始识别 request_id=%s url=%s",
            request_id,
            audio_url[:200],
        )
        self._submit(audio_url, request_id, resolved_key)
        log.info("[Doubao] 任务已提交，开始轮询")
        result = self._poll(request_id, resolved_key)
        segments = self._parse(result)
        log.info("[Doubao] 识别完成，%d 段", len(segments))
        return segments

    def _build_headers(self, request_id: str, api_key: str) -> dict:
        return {
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "X-Api-Resource-Id": _resolve_doubao_asr_resource_id(),
            "X-Api-Request-Id": request_id,
            "X-Api-Sequence": "-1",
        }

    def _submit(self, audio_url: str, request_id: str, api_key: str) -> None:
        payload = {
            "user": {"uid": "auto_video_srt"},
            "audio": {
                "url": audio_url,
                "format": "wav",
                "rate": 16000,
                "bits": 16,
                "channel": 1,
            },
            "request": {
                "model_name": self.model_id or _DEFAULT_MODEL_ID,
                "show_utterances": True,
                "enable_itn": True,
                "enable_punc": True,
            },
        }
        headers = self._build_headers(request_id, api_key)
        resp = requests.post(
            VOLC_ASR_SUBMIT_URL, json=payload, headers=headers, timeout=30
        )
        resp.raise_for_status()
        status = resp.headers.get("X-Api-Status-Code", "")
        message = resp.headers.get("X-Api-Message", "")
        log.debug("[Doubao] 提交响应: status=%s, message=%s", status, message)
        if status != "20000000":
            raise RuntimeError(
                f"豆包 ASR 提交失败: status={status}, message={message}"
            )

    def _poll(self, request_id: str, api_key: str) -> dict:
        headers = self._build_headers(request_id, api_key)
        for attempt in range(_POLL_MAX_RETRIES):
            resp = requests.post(
                VOLC_ASR_QUERY_URL, json={}, headers=headers, timeout=30
            )
            resp.raise_for_status()
            status = resp.headers.get("X-Api-Status-Code", "")
            message = resp.headers.get("X-Api-Message", "")
            if status == "20000000":
                return resp.json()
            if status in ("20000001", "20000002"):
                log.debug(
                    "[Doubao] 轮询 #%d: status=%s 处理中, 等 %ds",
                    attempt + 1,
                    status,
                    _POLL_INTERVAL_SEC,
                )
                time.sleep(_POLL_INTERVAL_SEC)
                continue
            if status == "20000003":
                return {"resp": {"text": "", "utterances": []}}
            raise RuntimeError(
                f"豆包 ASR 识别失败: status={status}, message={message}"
            )
        raise TimeoutError(
            f"豆包 ASR 轮询超时（{_POLL_MAX_RETRIES * _POLL_INTERVAL_SEC}s）"
        )

    def _parse(self, data: dict) -> List[Utterance]:
        segments: List[Utterance] = []
        result = data.get("result", {}) or {}
        utterances = result.get("utterances", []) or []

        if not utterances:
            full_text = (result.get("text") or "").strip()
            duration_ms = (data.get("audio_info", {}) or {}).get("duration", 0)
            if full_text:
                segments.append(
                    {
                        "text": full_text,
                        "start_time": 0.0,
                        "end_time": float(duration_ms) / 1000.0,
                        "words": [],
                    }
                )
            return segments

        for utt in utterances:
            text = (utt.get("text") or "").strip()
            if not text:
                continue
            start_ms = utt.get("start_time", 0)
            end_ms = utt.get("end_time", 0)
            words: list[Dict] = []
            for word in utt.get("words", []) or []:
                words.append(
                    {
                        "text": word.get("text", ""),
                        "start_time": float(word.get("start_time", 0)) / 1000.0,
                        "end_time": float(word.get("end_time", 0)) / 1000.0,
                        "confidence": float(word.get("confidence", 0)),
                    }
                )
            segments.append(
                {
                    "text": text,
                    "start_time": float(start_ms) / 1000.0,
                    "end_time": float(end_ms) / 1000.0,
                    "words": words,
                }
            )
        return segments
