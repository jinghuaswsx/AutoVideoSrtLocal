"""pipeline/copywriting.py
文案生成：调用 LLM 生成 / 重写短视频卖货文案。
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
from typing import Any

log = logging.getLogger(__name__)

# ── 默认系统提示词 ──────────────────────────────────

DEFAULT_SYSTEM_PROMPT_EN = """\
You are an expert TikTok short-video copywriter specializing in US e-commerce ads.

**Your task:** Based on the video keyframes, product information, and product images provided, write a compelling short-video sales script for the US market. The script must match the video's visual content and the product being sold.

**Video understanding:** Carefully analyze each keyframe to understand the video's scenes, actions, mood, and pacing. Your script must align with what's happening on screen — each segment should correspond to the visual flow.

**Script structure (follow TikTok best practices):**
1. **Hook (0-3s):** An attention-grabbing opening that stops the scroll. Use curiosity, shock, relatability, or a bold claim. Must connect to what's shown in the first frames.
2. **Problem/Scene (3-8s):** Identify a pain point or set a relatable scene that the target audience experiences. Match the video's visual context.
3. **Product Reveal (8-15s):** Introduce the product naturally as the solution. Highlight key selling points that are visible in the video. Be specific — mention features shown on screen.
4. **Social Proof / Demo (15-22s):** Reinforce credibility — results, transformations, or demonstrations visible in the video. Use sensory language.
5. **CTA (last 3-5s):** Clear call-to-action. Create urgency. Direct viewers to take action.

**Style guidelines:**
- Conversational, authentic tone — sounds like a real person, not an ad
- Short punchy sentences, easy to speak aloud
- Use power words: "obsessed", "game-changer", "finally", "you need this"
- Match the energy/mood of the video (upbeat, calm, dramatic, etc.)
- Aim for 15-45 seconds total speaking time depending on video length

**Output format:** Return ONLY a JSON object with this exact structure:
{
  "segments": [
    {"label": "Hook", "text": "...", "duration_hint": 3.0},
    {"label": "Problem", "text": "...", "duration_hint": 5.0},
    {"label": "Product", "text": "...", "duration_hint": 7.0},
    {"label": "Demo", "text": "...", "duration_hint": 5.0},
    {"label": "CTA", "text": "...", "duration_hint": 3.0}
  ],
  "full_text": "Complete script as one paragraph",
  "tone": "Description of the tone used",
  "target_duration": 23
}"""

DEFAULT_SYSTEM_PROMPT_ZH = """\
你是一位专业的短视频带货文案专家，擅长为美国 TikTok 市场创作电商广告脚本。

**你的任务：** 根据提供的视频关键帧、商品信息和商品图片，撰写一段面向美国市场的短视频带货口播文案。文案必须与视频画面内容和所售商品高度匹配。

**视频理解：** 仔细分析每一帧关键画面，理解视频的场景、动作、氛围和节奏。你的文案必须与画面同步——每一段都要对应视频的视觉流程。

**文案结构（遵循 TikTok 最佳实践）：**
1. **Hook 开头（0-3秒）：** 抓眼球的开场，让用户停止滑动。用好奇心、冲击感、共鸣或大胆主张。必须关联开头几帧画面。
2. **痛点/场景（3-8秒）：** 点出目标用户的痛点或建立一个有共鸣的场景，匹配视频画面。
3. **产品展示（8-15秒）：** 自然引入产品作为解决方案。突出视频中可见的核心卖点，要具体——提及画面中展示的功能特点。
4. **信任背书/演示（15-22秒）：** 强化可信度——视频中可见的效果、变化或演示。使用感官化语言。
5. **CTA 行动号召（最后3-5秒）：** 清晰的行动指令，制造紧迫感，引导用户下单。

**风格要求：**
- 口语化、真实自然的语气——听起来像真人分享，不像广告
- 短句为主，朗朗上口，适合口播
- 善用有感染力的词汇
- 匹配视频的情绪和节奏（活力、舒缓、震撼等）
- 根据视频时长，口播总时长控制在 15-45 秒

**输出格式：** 仅返回如下 JSON 对象：
{
  "segments": [
    {"label": "Hook", "text": "...", "duration_hint": 3.0},
    {"label": "Problem", "text": "...", "duration_hint": 5.0},
    {"label": "Product", "text": "...", "duration_hint": 7.0},
    {"label": "Demo", "text": "...", "duration_hint": 5.0},
    {"label": "CTA", "text": "...", "duration_hint": 3.0}
  ],
  "full_text": "完整文案拼接为一段话",
  "tone": "语气描述",
  "target_duration": 23
}"""

REWRITE_SEGMENT_PROMPT_EN = """\
You are rewriting ONE segment of a TikTok sales script. Keep the same style and flow as the rest of the script.

Full script context:
{full_text}

Segment to rewrite (label: {label}):
"{original_text}"

{user_instruction}

Return ONLY a JSON object:
{{"label": "{label}", "text": "rewritten text here", "duration_hint": {duration_hint}}}"""

REWRITE_SEGMENT_PROMPT_ZH = """\
你正在重写一段 TikTok 带货文案中的某一段。请保持与其余文案一致的风格和节奏。

完整文案上下文：
{full_text}

需要重写的段落（标签：{label}）：
"{original_text}"

{user_instruction}

仅返回如下 JSON 对象：
{{"label": "{label}", "text": "重写后的文案", "duration_hint": {duration_hint}}}"""

# ── JSON Schema ──────────────────────────────────────

COPYWRITING_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "copywriting_result",
        "strict": True,
        "schema": {
            "type": "object",
            "required": ["segments", "full_text", "tone", "target_duration"],
            "additionalProperties": False,
            "properties": {
                "segments": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["label", "text", "duration_hint"],
                        "additionalProperties": False,
                        "properties": {
                            "label": {"type": "string"},
                            "text": {"type": "string"},
                            "duration_hint": {"type": "number"},
                        },
                    },
                },
                "full_text": {"type": "string"},
                "tone": {"type": "string"},
                "target_duration": {"type": "number"},
            },
        },
    },
}


# ── 辅助函数 ──────────────────────────────────────────

def _image_to_base64_url(image_path: str) -> str:
    """将本地图片转为 base64 data URL。"""
    with open(image_path, "rb") as f:
        data = base64.b64encode(f.read()).decode()
    ext = os.path.splitext(image_path)[1].lower()
    mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
            ".webp": "image/webp", ".gif": "image/gif"}.get(ext, "image/jpeg")
    return f"data:{mime};base64,{data}"


def _parse_json_content(raw: str) -> dict:
    """解析 LLM 返回的 JSON（兼容 markdown code block 包裹）。"""
    text = raw.strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if m:
        text = m.group(1).strip()
    return json.loads(text)


def _build_product_text(inputs: dict) -> str:
    """将结构化商品信息拼成文本块。"""
    parts: list[str] = []
    if inputs.get("product_title"):
        parts.append(f"Product: {inputs['product_title']}")
    if inputs.get("price"):
        parts.append(f"Price: {inputs['price']}")
    if inputs.get("selling_points"):
        sp = inputs["selling_points"]
        if isinstance(sp, str):
            try:
                sp = json.loads(sp)
            except json.JSONDecodeError:
                sp = [sp]
        parts.append("Key selling points:\n" + "\n".join(f"- {p}" for p in sp))
    if inputs.get("target_audience"):
        parts.append(f"Target audience: {inputs['target_audience']}")
    if inputs.get("extra_info"):
        parts.append(f"Additional info: {inputs['extra_info']}")
    return "\n".join(parts)


def _supports_vision(provider: str) -> bool:
    """判断 provider 是否支持 vision（图片输入）。"""
    return provider != "doubao"


# ── 主函数 ─────────────────────────────────────────────

def generate_copy(
    keyframe_paths: list[str],
    product_inputs: dict,
    provider: str = "openrouter",
    user_id: int | None = None,
    custom_system_prompt: str | None = None,
    language: str = "en",
) -> dict:
    """生成短视频文案。

    Args:
        keyframe_paths: 关键帧图片路径列表
        product_inputs: 商品信息 dict（product_title, price, selling_points 等）
        provider: LLM provider（"openrouter" 或 "doubao"）
        user_id: 用户 ID（用于解析 API key）
        custom_system_prompt: 自定义系统提示词，为 None 则用默认
        language: 输出语言 "en" 或 "zh"

    Returns:
        dict: {segments, full_text, tone, target_duration}
    """
    from pipeline.translate import _resolve_provider_config

    client, model = _resolve_provider_config(provider, user_id=user_id)

    # 系统提示词
    if custom_system_prompt:
        system_prompt = custom_system_prompt
    elif language == "zh":
        system_prompt = DEFAULT_SYSTEM_PROMPT_ZH
    else:
        system_prompt = DEFAULT_SYSTEM_PROMPT_EN

    # 构建用户消息内容
    content: list[dict[str, Any]] = []

    # 图片（仅 vision 支持的模型）
    use_vision = _supports_vision(provider) and keyframe_paths
    if use_vision:
        content.append({"type": "text", "text": "Video keyframes (in chronological order):"})
        for path in keyframe_paths:
            content.append({
                "type": "image_url",
                "image_url": {"url": _image_to_base64_url(path)},
            })

    # 商品主图
    product_image = product_inputs.get("product_image_url") or product_inputs.get("product_image_path")
    if use_vision and product_image and os.path.isfile(product_image):
        content.append({"type": "text", "text": "Product image:"})
        content.append({
            "type": "image_url",
            "image_url": {"url": _image_to_base64_url(product_image)},
        })

    # 商品文本信息
    product_text = _build_product_text(product_inputs)
    if not use_vision:
        product_text = (
            "[Note: Current model does not support image input. "
            "Generating copy based on text information only.]\n\n" + product_text
        )
    if product_text.strip():
        content.append({"type": "text", "text": product_text})

    # 语言指令
    if language == "zh":
        content.append({"type": "text", "text": "请用中文撰写文案。"})
    else:
        content.append({"type": "text", "text": "Write the script in English for the US market."})

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": content},
    ]

    extra_kwargs: dict[str, Any] = {"temperature": 0.7, "max_tokens": 4096}
    if provider == "openrouter":
        extra_kwargs["extra_body"] = {"plugins": [{"id": "response-healing"}]}
    extra_kwargs["response_format"] = COPYWRITING_RESPONSE_FORMAT

    log.info("调用 LLM 生成文案: provider=%s, model=%s, images=%d",
             provider, model, len(keyframe_paths) if use_vision else 0)

    response = client.chat.completions.create(
        model=model,
        messages=messages,
        **extra_kwargs,
    )

    raw = response.choices[0].message.content
    result = _parse_json_content(raw)

    # 补充 index
    for i, seg in enumerate(result.get("segments", [])):
        seg["index"] = i

    log.info("文案生成完成: %d 段, 预计时长 %ds",
             len(result.get("segments", [])), result.get("target_duration", 0))
    return result


def rewrite_segment(
    full_text: str,
    segment: dict,
    user_instruction: str = "",
    provider: str = "openrouter",
    user_id: int | None = None,
    language: str = "en",
) -> dict:
    """重写文案的某一段。

    Args:
        full_text: 完整文案文本（上下文）
        segment: 要重写的段落 dict（label, text, duration_hint）
        user_instruction: 用户的修改要求
        provider: LLM provider
        user_id: 用户 ID
        language: 语言

    Returns:
        dict: {label, text, duration_hint}
    """
    from pipeline.translate import _resolve_provider_config

    client, model = _resolve_provider_config(provider, user_id=user_id)

    template = REWRITE_SEGMENT_PROMPT_ZH if language == "zh" else REWRITE_SEGMENT_PROMPT_EN
    if not user_instruction:
        user_instruction = "请重写这一段，使其更有吸引力。" if language == "zh" else "Rewrite to be more engaging."

    prompt = template.format(
        full_text=full_text,
        label=segment["label"],
        original_text=segment["text"],
        duration_hint=segment.get("duration_hint", 3.0),
        user_instruction=f"User request: {user_instruction}",
    )

    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
        max_tokens=1024,
    )

    raw = response.choices[0].message.content
    return _parse_json_content(raw)
