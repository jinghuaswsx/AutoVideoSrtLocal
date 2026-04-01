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
import uuid
import time
import requests
from typing import List, Dict
from config import VOLC_API_KEY, VOLC_RESOURCE_ID, VOLC_ASR_SUBMIT_URL, VOLC_ASR_QUERY_URL

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
    api_key = volc_api_key or VOLC_API_KEY

    # Step 1: 提交任务
    _submit(audio_url, request_id, api_key)

    # Step 2: 轮询结果
    result = _poll(request_id, api_key)

    # Step 3: 解析
    return _parse(result)


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
            pass


def _build_headers(request_id: str, api_key: str | None = None) -> dict:
    return {
        "Content-Type": "application/json",
        "x-api-key": api_key or VOLC_API_KEY,
        "X-Api-Resource-Id": VOLC_RESOURCE_ID,
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
            time.sleep(POLL_INTERVAL_SEC)
            continue

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
