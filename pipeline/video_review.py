"""pipeline/video_review.py
AI 视频评估：通过 Gemini（via OpenRouter）分析视频质量并给出评分报告。
"""
from __future__ import annotations

import base64
import json
import logging
import os

from openai import OpenAI

log = logging.getLogger(__name__)

# 可选模型
GEMINI_MODELS = [
    ("google/gemini-2.5-flash", "Gemini 2.5 Flash"),
    ("google/gemini-2.5-flash-lite", "Gemini 2.5 Flash Lite"),
    ("google/gemini-2.5-pro", "Gemini 2.5 Pro"),
]
DEFAULT_MODEL = "google/gemini-2.5-flash"

SYSTEM_PROMPT = """你是一位资深的美国短视频电商运营专家和视频质量评审员。
你将收到一段用于美国市场投放的短视频。请根据以下框架进行全面评估。

请用中文回复，返回有效的 JSON，结构如下：

{
  "overview": {
    "content_summary": "视频内容概述（50-100字）",
    "target_audience": "目标受众分析",
    "platform_fit": "平台适用性（TikTok/Instagram Reels/YouTube Shorts）"
  },
  "quality_assessment": {
    "visual_quality": {
      "score": 0-10,
      "details": "画面质量、构图、色彩、光线等分析"
    },
    "audio_quality": {
      "score": 0-10,
      "details": "音频清晰度、背景音乐、节奏感等分析"
    },
    "editing_quality": {
      "score": 0-10,
      "details": "剪辑节奏、转场效果、字幕排版等分析"
    },
    "content_quality": {
      "score": 0-10,
      "details": "信息传达、故事性、吸引力等分析"
    },
    "hook_effectiveness": {
      "score": 0-10,
      "details": "前3秒吸引力分析，是否能有效抓住观众注意力"
    },
    "cta_effectiveness": {
      "score": 0-10,
      "details": "行动号召分析，是否能有效引导用户行动"
    }
  },
  "issues": [
    {
      "severity": "high/medium/low",
      "category": "问题类别",
      "description": "问题描述",
      "suggestion": "调整建议"
    }
  ],
  "scoring": {
    "total_score": 0-100,
    "grade": "A/B/C/D/F",
    "verdict": "可直接使用/需要小幅调整/需要较大修改/建议重做",
    "summary": "一句话总结评估结论"
  }
}

评分标准：
- A (90-100): 可直接投放，质量优秀
- B (75-89): 可投放但建议优化，质量良好
- C (60-74): 需要调整后再投放
- D (40-59): 问题较多，需要较大修改
- F (0-39): 质量不达标，建议重做

评估时请结合美国短视频电商的最佳实践：
1. 前3秒必须有强有力的 Hook
2. 视频节奏要快，避免拖沓
3. 字幕/文字要清晰可读
4. 音频质量要过关
5. CTA（行动号召）要明确
6. 内容要符合目标平台的调性
"""


def _get_client(user_id: int | None = None, api_key: str | None = None) -> OpenAI:
    """获取 OpenRouter 客户端。"""
    from appcore.api_keys import resolve_extra, resolve_key
    from config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL

    key = api_key or (
        resolve_key(user_id, "openrouter", "OPENROUTER_API_KEY") if user_id else OPENROUTER_API_KEY
    )
    extra = resolve_extra(user_id, "openrouter") if user_id else {}
    base_url = extra.get("base_url") or OPENROUTER_BASE_URL
    return OpenAI(api_key=key, base_url=base_url)


def _encode_video(video_path: str) -> str:
    """将视频文件编码为 base64 data URI。"""
    with open(video_path, "rb") as f:
        data = base64.b64encode(f.read()).decode("ascii")
    ext = os.path.splitext(video_path)[1].lower().lstrip(".")
    mime = {"mp4": "video/mp4", "webm": "video/webm", "mov": "video/quicktime"}.get(ext, "video/mp4")
    return f"data:{mime};base64,{data}"


def review_video(
    video_path: str,
    *,
    user_id: int | None = None,
    model: str = DEFAULT_MODEL,
    custom_prompt: str | None = None,
) -> dict:
    """分析视频并返回评估结果 JSON。

    Args:
        video_path: 本地视频文件路径
        user_id: 用户 ID（用于获取 API Key）
        model: Gemini 模型 ID
        custom_prompt: 可选的自定义提示词（追加到系统提示后面）

    Returns:
        评估结果 dict
    """
    client = _get_client(user_id=user_id)
    video_data_uri = _encode_video(video_path)

    system = SYSTEM_PROMPT
    if custom_prompt:
        system += f"\n\n用户额外要求：\n{custom_prompt}"

    file_size_mb = os.path.getsize(video_path) / (1024 * 1024)
    log.info("[VideoReview] 开始评估: model=%s, video=%s (%.1fMB)", model, video_path, file_size_mb)

    messages = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "请评估这段视频，返回 JSON 格式的评估报告。"},
                {
                    "type": "image_url",
                    "image_url": {"url": video_data_uri},
                },
            ],
        },
    ]

    response = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.3,
        max_tokens=4096,
    )

    raw = response.choices[0].message.content or ""
    log.info("[VideoReview] 原始响应长度: %d", len(raw))

    # 解析 JSON
    result = _parse_json_response(raw)
    result["_raw"] = raw
    result["_model"] = model
    return result


def _parse_json_response(text: str) -> dict:
    """从 LLM 响应中提取 JSON。"""
    text = text.strip()
    # 去掉 markdown code block
    if text.startswith("```"):
        lines = text.split("\n")
        # 去掉首尾的 ``` 行
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 尝试找到第一个 { 和最后一个 }
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass

    log.warning("[VideoReview] 无法解析 JSON 响应: %s...", text[:200])
    return {"_parse_error": True, "_raw_text": text}
