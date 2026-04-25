"""
ASR 模块：使用豆包大模型录音文件识别 v3 接口
文档：https://www.volcengine.com/docs/6561/1354868

流程：
  1. 提交任务（POST submit）→ 获取 task_id（从请求头 X-Api-Request-Id 回传）
  2. 轮询查询（POST query）→ 等待状态码 20000000（成功）
  3. 解析 utterances，返回带时间戳的句子列表

注意：豆包 v3 接口只接受音频 URL，不接受 base64。
调用前需确保 audio_url 可被火山引擎服务端访问到。
"""
import logging
import uuid
import time
import requests
from typing import List, Dict
from appcore.llm_provider_configs import (
    ProviderConfigError,
    require_provider_config,
)
from config import VOLC_ASR_SUBMIT_URL, VOLC_ASR_QUERY_URL

_DEFAULT_VOLC_RESOURCE_ID = "volc.seedasr.auc"


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

log = logging.getLogger(__name__)

# 轮询配置
POLL_INTERVAL_SEC = 3
POLL_MAX_RETRIES = 100  # 最多等 ~5 分钟


def transcribe(audio_url: str, volc_api_key: str | None = None) -> List[Dict]:
    """
    调用豆包 v3 ASR 识别音频，返回带时间戳的句子列表

    Args:
        audio_url: 可被火山引擎服务端访问的音频文件 URL（mp3/wav）
        volc_api_key: 可选的用户自定义 API Key，None 时使用系统配置

    Returns:
        List[Dict]: [{"text": str, "start_time": float, "end_time": float, "words": [...]}, ...]
        时间单位为秒
    """
    request_id = str(uuid.uuid4())
    api_key = volc_api_key or _resolve_doubao_asr_key()

    log.info("[ASR] 开始识别，request_id=%s, audio_url=%s", request_id, audio_url[:200])

    # Step 1: 提交任务
    _submit(audio_url, request_id, api_key)
    log.info("[ASR] 任务已提交，开始轮询")

    # Step 2: 轮询结果
    result = _poll(request_id, api_key)

    # Step 3: 解析
    segments = _parse(result)
    log.info("[ASR] 识别完成，共 %d 个片段", len(segments))
    return segments


def transcribe_local_audio(local_audio_path: str, prefix: str, volc_api_key: str | None = None) -> List[Dict]:
    from pipeline.storage import delete_file, upload_file

    object_key = f"{prefix}_{uuid.uuid4().hex[:8]}.mp3"
    audio_url = upload_file(local_audio_path, object_key)
    try:
        return transcribe(audio_url, volc_api_key=volc_api_key)
    finally:
        try:
            delete_file(object_key)
        except Exception:
            log.warning("[ASR] 清理临时音频文件失败: %s", object_key, exc_info=True)


# Source languages that Doubao's volc.seedasr.auc endpoint officially supports.
# Anything outside this set is routed to ElevenLabs Scribe (99 langs).
_DOUBAO_NATIVE_LANGUAGES = frozenset({"zh", "en"})


def transcribe_local_audio_for_source(
    local_audio_path: str,
    source_language: str | None,
    *,
    prefix: str = "asr_input",
    volc_api_key: str | None = None,
    elevenlabs_api_key: str | None = None,
) -> List[Dict]:
    """Source-language-aware ASR dispatcher.

    Routes to Doubao SeedASR for zh/en (strong on those + cheap), and to
    ElevenLabs Scribe for any other source language (es/pt/de/fr/...).
    Doubao's volc.seedasr.auc endpoint does not officially support
    non-zh-non-en sources; previously Spanish input was misidentified as
    English, producing garbled transcripts that broke downstream rewrites.

    Args:
        local_audio_path: 本地音频/视频路径。
        source_language: ISO-639-1 (zh/en/es/...) 或 None（None 视作 zh 默认）。
        prefix: 豆包路径下用于 TOS object key 的前缀。
        volc_api_key: 豆包 ASR 自定义 key。None 时走系统配置。
        elevenlabs_api_key: Scribe 自定义 key。None 时走系统配置。

    Returns:
        [{"text", "start_time", "end_time", "words": [...]}] —— 两个 backend
        的输出已对齐到同一结构。
    """
    if source_language in (None, "") or source_language in _DOUBAO_NATIVE_LANGUAGES:
        log.info(
            "[ASR-router] source_language=%s → Doubao SeedASR",
            source_language or "(unset)",
        )
        return transcribe_local_audio(
            local_audio_path, prefix=prefix, volc_api_key=volc_api_key,
        )

    from pipeline import asr_scribe
    log.info(
        "[ASR-router] source_language=%s → ElevenLabs Scribe "
        "(Doubao only supports zh/en officially)",
        source_language,
    )
    return asr_scribe.transcribe_local_audio(
        local_audio_path,
        language_code=source_language,
        api_key=elevenlabs_api_key,
    )


def _build_headers(request_id: str, api_key: str | None = None) -> dict:
    resolved_key = api_key or _resolve_doubao_asr_key()
    return {
        "Content-Type": "application/json",
        "x-api-key": resolved_key,
        "X-Api-Resource-Id": _resolve_doubao_asr_resource_id(),
        "X-Api-Request-Id": request_id,
        "X-Api-Sequence": "-1",
    }


def _submit(audio_url: str, request_id: str, api_key: str | None = None):
    """提交识别任务"""
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
            "model_name": "bigmodel",
            "show_utterances": True,
            "enable_itn": True,
            "enable_punc": True,
        }
    }

    headers = _build_headers(request_id, api_key)
    resp = requests.post(VOLC_ASR_SUBMIT_URL, json=payload, headers=headers, timeout=30)
    resp.raise_for_status()

    status_code = resp.headers.get("X-Api-Status-Code", "")
    message = resp.headers.get("X-Api-Message", "")
    log.debug("[ASR] 提交响应: status=%s, message=%s", status_code, message)

    if status_code != "20000000":
        raise RuntimeError(
            f"豆包 ASR 提交失败: status={status_code}, message={message}"
        )


def _poll(request_id: str, api_key: str | None = None) -> dict:
    """轮询查询结果，直到成功或超时"""
    headers = _build_headers(request_id, api_key)

    for attempt in range(POLL_MAX_RETRIES):
        resp = requests.post(
            VOLC_ASR_QUERY_URL,
            json={},
            headers=headers,
            timeout=30
        )
        resp.raise_for_status()

        status_code = resp.headers.get("X-Api-Status-Code", "")
        message = resp.headers.get("X-Api-Message", "")

        if status_code == "20000000":
            # 识别成功
            return resp.json()

        if status_code in ("20000001", "20000002"):
            # 处理中 / 队列中，继续等待
            log.debug("[ASR] 轮询 #%d: status=%s (处理中), 等待 %ds", attempt + 1, status_code, POLL_INTERVAL_SEC)
            time.sleep(POLL_INTERVAL_SEC)
            continue

        if status_code == "20000003":
            # 静音/无有效语音，返回空结果而非报错
            return {"resp": {"text": "", "utterances": []}}

        # 其他状态码均为错误
        raise RuntimeError(
            f"豆包 ASR 识别失败: status={status_code}, message={message}"
        )

    raise TimeoutError(
        f"豆包 ASR 轮询超时（已等待 {POLL_MAX_RETRIES * POLL_INTERVAL_SEC}s）"
    )


def _parse(data: dict) -> List[Dict]:
    """解析响应 JSON，提取句子和时间戳"""
    segments = []
    result = data.get("result", {})
    utterances = result.get("utterances", [])

    if not utterances:
        # 没有分句信息，用整体文本和 audio_info 时长兜底
        full_text = result.get("text", "").strip()
        duration_ms = data.get("audio_info", {}).get("duration", 0)
        if full_text:
            segments.append({
                "text": full_text,
                "start_time": 0.0,
                "end_time": duration_ms / 1000.0,
                "words": [],
            })
        return segments

    for utt in utterances:
        text = utt.get("text", "").strip()
        start_ms = utt.get("start_time", 0)
        end_ms = utt.get("end_time", 0)

        if text:
            words = []
            for word in utt.get("words", []) or []:
                words.append({
                    "text": word.get("text", ""),
                    "start_time": word.get("start_time", 0) / 1000.0,
                    "end_time": word.get("end_time", 0) / 1000.0,
                    "confidence": word.get("confidence", 0),
                })
            segments.append({
                "text": text,
                "start_time": start_ms / 1000.0,
                "end_time": end_ms / 1000.0,
                "words": words,
            })

    return segments
