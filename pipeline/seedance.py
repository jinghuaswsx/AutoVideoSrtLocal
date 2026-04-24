"""pipeline/seedance.py
Seedance 1.5 Pro 视频生成 API 封装。

异步任务模式：提交生成请求 → 轮询任务状态 → 获取视频 URL。
"""
from __future__ import annotations

import logging
import time

import requests

log = logging.getLogger(__name__)

API_BASE = "https://ark.cn-beijing.volces.com/api/v3"
DEFAULT_MODEL = "doubao-seedance-1-5-pro-251215"

# 轮询配置
POLL_INTERVAL = 10     # 秒
POLL_TIMEOUT = 1800    # 最长等待 30 分钟


def create_video_task(
    api_key: str,
    prompt: str,
    image_url: str | None = None,
    duration: int = 5,
    model: str = DEFAULT_MODEL,
) -> str:
    """提交视频生成任务，返回 task_id。

    Args:
        api_key: 火山方舟 API Key
        prompt: 文本提示词，可包含 --duration/--camerafixed 等参数
        image_url: 参考图片 URL（图生视频），为 None 则为纯文生视频
        duration: 视频时长（秒），会追加到 prompt 尾部
        model: 模型 ID

    Returns:
        task_id 字符串
    """
    # 构建 prompt（追加参数）
    full_prompt = prompt.strip()
    if f"--duration" not in full_prompt:
        full_prompt += f"  --duration {duration}"

    content = [{"type": "text", "text": full_prompt}]
    if image_url:
        content.append({
            "type": "image_url",
            "image_url": {"url": image_url},
        })

    payload = {"model": model, "content": content}

    log.info("[Seedance] 提交任务: model=%s, prompt=%s, has_image=%s",
             model, full_prompt[:100], bool(image_url))

    resp = requests.post(
        f"{API_BASE}/contents/generations/tasks",
        json=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    task_id = data.get("id") or data.get("task_id")
    if not task_id:
        raise RuntimeError(f"Seedance 未返回 task_id: {data}")

    log.info("[Seedance] 任务已提交: task_id=%s", task_id)
    return task_id


def poll_video_task(
    api_key: str,
    task_id: str,
    interval: int = POLL_INTERVAL,
    timeout: int = POLL_TIMEOUT,
    on_progress: callable = None,
) -> dict:
    """轮询视频生成任务直到完成。

    Args:
        api_key: 火山方舟 API Key
        task_id: 任务 ID
        interval: 轮询间隔（秒）
        timeout: 最长等待（秒）
        on_progress: 进度回调 fn(status, message)

    Returns:
        dict: {"status": "succeeded", "video_url": "...", "raw": {...}}

    Raises:
        RuntimeError: 任务失败或超时
    """
    start = time.time()

    while True:
        elapsed = time.time() - start
        if elapsed > timeout:
            raise RuntimeError(f"Seedance 任务超时（{timeout}s）: {task_id}")

        resp = requests.get(
            f"{API_BASE}/contents/generations/tasks/{task_id}",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        status = data.get("status", "").lower()
        log.info("[Seedance] 轮询 task_id=%s, status=%s, elapsed=%.0fs", task_id, status, elapsed)

        if on_progress:
            on_progress(status, f"已等待 {int(elapsed)}s")

        if status in ("succeeded", "success", "complete", "completed"):
            # 打印完整响应用于调试
            log.info("[Seedance] 任务完成，完整响应: %s", data)
            # 提取视频 URL
            video_url = _extract_video_url(data)
            return {"status": "succeeded", "video_url": video_url, "raw": data}

        if status in ("failed", "error", "cancelled"):
            error_msg = data.get("error", {}).get("message", "") or data.get("message", "")
            raise RuntimeError(f"Seedance 任务失败: {error_msg or status}")

        time.sleep(interval)


def _extract_video_url(data: dict) -> str:
    """从任务结果中提取视频下载 URL。"""
    # 格式1: data.content 为 dict，直接含 video_url
    content = data.get("content")
    if isinstance(content, dict) and "video_url" in content:
        return content["video_url"]

    # 格式2: data.content[] — list of dict
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict):
                if "video_url" in item:
                    url = item["video_url"]
                    if isinstance(url, dict):
                        return url.get("url", "")
                    return url
                if item.get("type") == "video_url":
                    vu = item.get("video_url", {})
                    if isinstance(vu, dict):
                        return vu.get("url", "")
                    return vu

    # 格式2: data.output.video_url
    output = data.get("output")
    if isinstance(output, dict) and "video_url" in output:
        return output["video_url"]

    # 格式3: data.result.video_url
    result = data.get("result")
    if isinstance(result, dict) and "video_url" in result:
        return result["video_url"]

    # 格式4: data.choices[].message.content (类 chat 格式)
    choices = data.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            msg = choice.get("message", {})
            msg_content = msg.get("content") if isinstance(msg, dict) else None
            if isinstance(msg_content, list):
                for c in msg_content:
                    if isinstance(c, dict) and c.get("type") == "video_url":
                        vu = c.get("video_url", {})
                        return vu.get("url", "") if isinstance(vu, dict) else (vu or "")

    log.warning("[Seedance] 无法从响应中提取视频 URL: %s", data)
    raise RuntimeError("Seedance 返回结果中未找到视频 URL")


def generate_video(
    api_key: str,
    prompt: str,
    image_url: str | None = None,
    duration: int = 5,
    model: str = DEFAULT_MODEL,
    on_progress: callable = None,
) -> dict:
    """一站式调用：提交任务 + 轮询等待 + 返回结果。

    Returns:
        dict: {"task_id": "...", "video_url": "...", "raw": {...}}
    """
    task_id = create_video_task(api_key, prompt, image_url, duration, model)
    result = poll_video_task(api_key, task_id, on_progress=on_progress)
    result["task_id"] = task_id
    return result


# ── Seedance 2.0 ──

DEFAULT_MODEL_V2 = "doubao-seedance-2-0-260128"


def create_video_task_v2(
    api_key: str,
    prompt: str,
    video_url: str | None = None,
    image_urls: list[str] | None = None,
    audio_url: str | None = None,
    ratio: str = "9:16",
    duration: int = 5,
    generate_audio: bool = True,
    watermark: bool = False,
    model: str = DEFAULT_MODEL_V2,
) -> str:
    """提交 Seedance 2.0 视频生成任务，返回 task_id。

    Args:
        api_key: 火山方舟 API Key
        prompt: 文案（最多 2000 字）
        video_url: 参考视频公网 URL（最多 1 个）
        image_urls: 参考图片公网 URL 列表（最多 9 个）
        audio_url: 参考音频公网 URL（最多 1 个）
        ratio: 视频比例，如 "9:16" / "16:9" / "1:1"
        duration: 视频时长（秒）
        generate_audio: 是否生成音频
        watermark: 是否加水印
        model: 模型 ID

    Returns:
        task_id 字符串

    Raises:
        ValueError: prompt 超 2000 字或 image_urls 超 9 个
    """
    if len(prompt) > 2000:
        raise ValueError(f"文案不能超过 2000 字（当前 {len(prompt)} 字）")
    if image_urls and len(image_urls) > 9:
        raise ValueError(f"图片最多 9 张（当前 {len(image_urls)} 张）")

    content: list[dict] = [{"type": "text", "text": prompt.strip()}]

    if video_url:
        content.append({
            "type": "video_url",
            "video_url": {"url": video_url},
            "role": "reference_video",
        })

    for url in (image_urls or []):
        content.append({
            "type": "image_url",
            "image_url": {"url": url},
            "role": "reference_image",
        })

    if audio_url:
        content.append({
            "type": "audio_url",
            "audio_url": {"url": audio_url},
            "role": "reference_audio",
        })

    payload = {
        "model": model,
        "content": content,
        "generate_audio": generate_audio,
        "ratio": ratio,
        "duration": duration,
        "watermark": watermark,
    }

    log.info(
        "[Seedance2] 提交任务: model=%s, prompt=%s, images=%d, has_video=%s, has_audio=%s",
        model, prompt[:80], len(image_urls or []), bool(video_url), bool(audio_url),
    )

    resp = requests.post(
        f"{API_BASE}/contents/generations/tasks",
        json=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    task_id = data.get("id") or data.get("task_id")
    if not task_id:
        raise RuntimeError(f"Seedance 2.0 未返回 task_id: {data}")

    log.info("[Seedance2] 任务已提交: task_id=%s", task_id)
    return task_id


def generate_video_v2(
    api_key: str,
    prompt: str,
    video_url: str | None = None,
    image_urls: list[str] | None = None,
    audio_url: str | None = None,
    ratio: str = "9:16",
    duration: int = 5,
    generate_audio: bool = True,
    watermark: bool = False,
    model: str = DEFAULT_MODEL_V2,
    on_progress: callable = None,
) -> dict:
    """Seedance 2.0 一站式调用：提交任务 + 轮询等待 + 返回结果。

    Returns:
        dict: {"task_id": "...", "video_url": "...", "raw": {...}}
    """
    task_id = create_video_task_v2(
        api_key=api_key,
        prompt=prompt,
        video_url=video_url,
        image_urls=image_urls,
        audio_url=audio_url,
        ratio=ratio,
        duration=duration,
        generate_audio=generate_audio,
        watermark=watermark,
        model=model,
    )
    result = poll_video_task(api_key, task_id, on_progress=on_progress)
    result["task_id"] = task_id
    return result
