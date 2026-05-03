"""pipeline/video_review.py
AI 视频评估：通过 Gemini 官方 API 分析视频质量并给出评分报告。
"""
from __future__ import annotations

import json
import logging
import os

from appcore.llm_models import VIDEO_CAPABLE_MODELS

log = logging.getLogger(__name__)

# 可选模型：复用全局 VIDEO_CAPABLE_MODELS（Gemini 3 系列）
GEMINI_MODELS = VIDEO_CAPABLE_MODELS
DEFAULT_MODEL = "gemini-3.1-pro-preview"

DEFAULT_PROMPT_EN = """You are a senior US short-video e-commerce operations expert and video quality reviewer.
You will receive a short video intended for the US market. Please evaluate it comprehensively using the framework below.

Reply in Chinese. Return valid JSON only, with this exact structure:

{
  "overview": {
    "content_summary": "Video content overview (50-100 words)",
    "target_audience": "Target audience analysis",
    "platform_fit": "Platform suitability (TikTok/Instagram Reels/YouTube Shorts)"
  },
  "quality_assessment": {
    "visual_quality": {
      "score": 0-10,
      "details": "Analysis of image quality, composition, color, lighting, etc."
    },
    "audio_quality": {
      "score": 0-10,
      "details": "Analysis of audio clarity, background music, rhythm, etc."
    },
    "editing_quality": {
      "score": 0-10,
      "details": "Analysis of editing pace, transitions, subtitle layout, etc."
    },
    "content_quality": {
      "score": 0-10,
      "details": "Analysis of information delivery, storytelling, appeal, etc."
    },
    "hook_effectiveness": {
      "score": 0-10,
      "details": "First-3-seconds hook analysis: does it effectively capture viewer attention?"
    },
    "cta_effectiveness": {
      "score": 0-10,
      "details": "Call-to-action analysis: does it effectively guide user action?"
    }
  },
  "issues": [
    {
      "severity": "high/medium/low",
      "category": "Issue category",
      "description": "Issue description",
      "suggestion": "Adjustment suggestion"
    }
  ],
  "scoring": {
    "total_score": 0-100,
    "grade": "A/B/C/D/F",
    "verdict": "Ready to use / Needs minor adjustments / Needs major revision / Recommend redo",
    "summary": "One-sentence evaluation conclusion"
  }
}

Grading criteria:
- A (90-100): Ready to publish, excellent quality
- B (75-89): Publishable but optimization recommended, good quality
- C (60-74): Needs adjustments before publishing
- D (40-59): Multiple issues, needs major revision
- F (0-39): Below standard, recommend redo

Evaluate based on US short-video e-commerce best practices:
1. First 3 seconds must have a strong Hook
2. Video pace should be fast, avoid dragging
3. Subtitles/text must be clear and readable
4. Audio quality must be acceptable
5. CTA (Call to Action) must be clear
6. Content must match the target platform's tone
"""

DEFAULT_PROMPT_ZH = """你是一位资深的美国短视频电商运营专家和视频质量评审员。
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


def get_review_prompts() -> dict:
    """获取全局视频评分提示词（中英）。优先读数据库，没有则返回默认值。"""
    from appcore.db import query_one
    row = query_one(
        "SELECT key_value, extra_config FROM api_keys WHERE user_id = 0 AND service = 'video_review_prompt'"
    )
    if row:
        en = row.get("key_value") or DEFAULT_PROMPT_EN
        extra = row.get("extra_config")
        if isinstance(extra, str):
            try:
                extra = json.loads(extra)
            except Exception:
                extra = {}
        zh = (extra or {}).get("prompt_zh") or DEFAULT_PROMPT_ZH
        return {"en": en, "zh": zh}
    return {"en": DEFAULT_PROMPT_EN, "zh": DEFAULT_PROMPT_ZH}


def save_review_prompts(prompt_en: str, prompt_zh: str) -> None:
    """保存全局视频评分提示词（仅管理员调用）。"""
    from appcore.db import execute, query_one
    extra = json.dumps({"prompt_zh": prompt_zh}, ensure_ascii=False)
    existing = query_one(
        "SELECT id FROM api_keys WHERE user_id = 0 AND service = 'video_review_prompt'"
    )
    if existing:
        execute(
            "UPDATE api_keys SET key_value = %s, extra_config = %s WHERE user_id = 0 AND service = 'video_review_prompt'",
            (prompt_en, extra),
        )
    else:
        execute(
            "INSERT INTO api_keys (user_id, service, key_value, extra_config) VALUES (0, 'video_review_prompt', %s, %s)",
            (prompt_en, extra),
        )


def review_video(
    video_path: str,
    *,
    user_id: int | None = None,
    model: str = DEFAULT_MODEL,
    custom_prompt: str | None = None,
    prompt_lang: str = "en",
) -> dict:
    """分析视频并返回评估结果 JSON。

    通过 Gemini 官方 API（appcore.gemini）执行，复用 gemini_video_analysis
    服务的 key/model 配置；model 参数用作默认值（用户未在配置页覆盖时生效）。
    """
    prompts = get_review_prompts()
    system = prompts.get(prompt_lang) or prompts.get("en") or DEFAULT_PROMPT_EN
    if custom_prompt:
        system += f"\n\nAdditional requirements:\n{custom_prompt}"

    file_size_mb = os.path.getsize(video_path) / (1024 * 1024)
    from appcore import llm_bindings
    resolved_model = llm_bindings.resolve("video_review.analyze").get("model") or model
    log.info("[VideoReview] 开始评估: model=%s, video=%s (%.1fMB)",
             resolved_model, video_path, file_size_mb)

    from appcore.llm_client import invoke_generate
    invoked = invoke_generate(
        "video_review.analyze",
        prompt="请评估这段视频，返回 JSON 格式的评估报告。",
        system=system,
        media=[video_path],
        user_id=user_id,
        model_override=model,
        temperature=0.3,
        max_output_tokens=4096,
    )
    raw = invoked.get("text") or ""
    if not raw and isinstance(invoked.get("json"), dict):
        raw = json.dumps(invoked["json"], ensure_ascii=False)
    log.info("[VideoReview] 原始响应长度: %d", len(raw))

    result = _parse_json_response(raw)
    result["_raw"] = raw
    result["_model"] = resolved_model
    result["_usage"] = {}
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
