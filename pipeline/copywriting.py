"""pipeline/copywriting.py
文案生成：调用 LLM 生成 / 重写短视频卖货文案。
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
from decimal import Decimal
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

_MAX_BASE64_FILE_SIZE = 50 * 1024 * 1024  # 50MB


def _image_to_base64_url(image_path: str) -> str:
    """将本地图片转为 base64 data URL。"""
    file_size = os.path.getsize(image_path)
    if file_size > _MAX_BASE64_FILE_SIZE:
        raise ValueError(f"文件过大（{file_size / 1024 / 1024:.1f}MB），base64 编码上限为 50MB")
    with open(image_path, "rb") as f:
        data = base64.b64encode(f.read()).decode()
    ext = os.path.splitext(image_path)[1].lower()
    mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
            ".webp": "image/webp", ".gif": "image/gif"}.get(ext, "image/jpeg")
    return f"data:{mime};base64,{data}"


def _video_to_base64_url(video_path: str) -> str:
    """将本地视频转为 base64 data URL。"""
    file_size = os.path.getsize(video_path)
    if file_size > _MAX_BASE64_FILE_SIZE:
        raise ValueError(f"文件过大（{file_size / 1024 / 1024:.1f}MB），base64 编码上限为 50MB")
    with open(video_path, "rb") as f:
        data = base64.b64encode(f.read()).decode()
    ext = os.path.splitext(video_path)[1].lower()
    mime = {".mp4": "video/mp4", ".mov": "video/mov", ".mpeg": "video/mpeg",
            ".webm": "video/webm", ".avi": "video/mp4"}.get(ext, "video/mp4")
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
    return True  # 所有 provider 都支持


def _supports_video(provider: str, model: str) -> bool:
    """判断是否支持直接视频输入。"""
    if "gemini" in model.lower():
        return True
    if provider == "doubao":
        return True
    return False


def _resolve_model_only(provider: str, user_id: int | None = None) -> str:
    """仅获取模型 ID，不创建 OpenAI client（避免 doubao 缺 key 时报错）。"""
    from appcore.api_keys import resolve_extra
    from config import CLAUDE_MODEL, DOUBAO_LLM_MODEL

    if provider == "doubao":
        extra = resolve_extra(user_id, "doubao_llm") if user_id else {}
        return extra.get("model_id") or DOUBAO_LLM_MODEL
    else:
        extra = resolve_extra(user_id, "openrouter") if user_id else {}
        return extra.get("model_id") or CLAUDE_MODEL


def _upload_to_tos(local_path: str, prefix: str = "copywriting_media/") -> str:
    """上传文件到 TOS，返回签名下载 URL。"""
    from appcore.tos_clients import upload_file, generate_signed_download_url
    object_key = f"{prefix}{os.path.basename(local_path)}"
    upload_file(local_path, object_key)
    url = generate_signed_download_url(object_key, expires=3600)
    log.info("已上传到 TOS: %s -> %s", os.path.basename(local_path), object_key)
    return url


def _call_doubao_multimodal(
    model: str,
    system_prompt: str,
    content_items: list[dict],
    api_key: str,
    base_url: str = "https://ark.cn-beijing.volces.com/api/v3",
) -> tuple[str, dict | None]:
    """用 volcengine Ark SDK 调用豆包多模态模型。返回 (text, usage_dict)。"""
    try:
        from volcenginesdkarkruntime import Ark
    except ImportError:
        raise ImportError(
            "volcenginesdkarkruntime 未安装，请运行: pip install volcenginesdkarkruntime"
        )

    client = Ark(base_url=base_url, api_key=api_key)

    # 转换 content 为 Ark 格式
    ark_content = []
    for item in content_items:
        if item["type"] == "text":
            ark_content.append({"type": "input_text", "text": item["text"]})
        elif item["type"] == "image_url":
            # TOS URL
            ark_content.append({
                "type": "input_image",
                "image_url": item["tos_url"],
            })
        elif item["type"] == "video_url":
            ark_content.append({
                "type": "input_video",
                "video_url": item["tos_url"],
            })

    response = client.responses.create(
        model=model,
        input=[
            {"role": "system", "content": [{"type": "input_text", "text": system_prompt}]},
            {"role": "user", "content": ark_content},
        ],
    )

    # 打印完整响应结构用于调试
    log.info("Ark 响应 type=%s, output type=%s", type(response).__name__, type(response.output).__name__)
    log.info("Ark 响应 output=%s", repr(response.output)[:2000])

    # 提取 token 用量（Ark SDK: response.usage 或 response.ResponseMeta.Usage）
    ark_usage = None
    try:
        usage_obj = getattr(response, "usage", None)
        if usage_obj:
            ark_usage = {
                "input_tokens": getattr(usage_obj, "prompt_tokens", None) or getattr(usage_obj, "PromptTokens", None) or getattr(usage_obj, "input_tokens", None),
                "output_tokens": getattr(usage_obj, "completion_tokens", None) or getattr(usage_obj, "CompletionTokens", None) or getattr(usage_obj, "output_tokens", None),
            }
        if not ark_usage or not ark_usage.get("input_tokens"):
            meta = getattr(response, "ResponseMeta", None) or getattr(response, "response_meta", None)
            if meta:
                meta_usage = getattr(meta, "Usage", None) or getattr(meta, "usage", None)
                if meta_usage:
                    ark_usage = {
                        "input_tokens": getattr(meta_usage, "PromptTokens", None) or getattr(meta_usage, "prompt_tokens", None),
                        "output_tokens": getattr(meta_usage, "CompletionTokens", None) or getattr(meta_usage, "completion_tokens", None),
                    }
        if ark_usage:
            log.info("Ark token usage: input=%s, output=%s", ark_usage["input_tokens"], ark_usage["output_tokens"])
    except Exception as e:
        log.debug("提取 Ark usage 失败: %s", e)

    output = response.output
    if isinstance(output, list):
        for item in output:
            # Pydantic model 对象
            if hasattr(item, 'content'):
                for c in item.content:
                    text = getattr(c, 'text', None)
                    if text:
                        return text, ark_usage
            # dict 对象
            elif isinstance(item, dict):
                for c in item.get('content', []):
                    if isinstance(c, dict) and c.get('text'):
                        return c['text'], ark_usage
        log.warning("Ark 响应 list 中未找到 text，尝试其他属性...")
        # 尝试直接取 text 属性
        for item in output:
            text = getattr(item, 'text', None)
            if text:
                return text, ark_usage
        log.error("Ark 响应格式不符预期: %s", repr(output)[:2000])
        raise ValueError(f"无法从 Ark 响应中提取文本: {repr(output)[:500]}")
    else:
        return output.content[0].text, ark_usage


# ── 主函数 ─────────────────────────────────────────────

def preview_request(
    keyframe_paths: list[str],
    product_inputs: dict,
    provider: str = "openrouter",
    user_id: int | None = None,
    custom_system_prompt: str | None = None,
    language: str = "en",
    video_path: str | None = None,
    model_override: str | None = None,
) -> dict:
    """预览将要发送给 LLM 的完整请求结构（不实际调用）。"""
    model = _resolve_model_only(provider, user_id=user_id)
    if model_override:
        model = model_override

    if custom_system_prompt:
        system_prompt = custom_system_prompt
    elif language == "zh":
        system_prompt = DEFAULT_SYSTEM_PROMPT_ZH
    else:
        system_prompt = DEFAULT_SYSTEM_PROMPT_EN

    use_video = _supports_video(provider, model) and video_path and os.path.isfile(video_path)
    use_vision = _supports_vision(provider) and keyframe_paths and not use_video
    user_content: list[dict] = []

    if use_video:
        video_size = os.path.getsize(video_path) / (1024 * 1024)
        user_content.append({"type": "text", "text": "Source video (full):"})
        user_content.append({"type": "video", "file": os.path.basename(video_path),
                             "size_mb": round(video_size, 1)})
    elif use_vision:
        user_content.append({"type": "text", "text": "Video keyframes (in chronological order):"})
        for path in keyframe_paths:
            user_content.append({"type": "image", "file": os.path.basename(path)})

    product_image = product_inputs.get("product_image_url") or product_inputs.get("product_image_path")
    if (use_vision or use_video) and product_image and os.path.isfile(product_image):
        user_content.append({"type": "text", "text": "Product image:"})
        user_content.append({"type": "image", "file": os.path.basename(product_image)})

    product_text = _build_product_text(product_inputs)
    if not use_vision and not use_video:
        product_text = (
            "[Note: Current model does not support image/video input. "
            "Generating copy based on text information only.]\n\n" + product_text
        )
    if product_text.strip():
        user_content.append({"type": "text", "text": product_text})

    if language == "zh":
        user_content.append({"type": "text", "text": "请用中文撰写文案。"})
    else:
        user_content.append({"type": "text", "text": "Write the script in English for the US market."})

    return {
        "provider": provider,
        "model": model,
        "system_prompt": system_prompt,
        "user_content": user_content,
        "resources": {
            "video": os.path.basename(video_path) if use_video else None,
            "video_size_mb": round(os.path.getsize(video_path) / (1024 * 1024), 1) if use_video else None,
            "video_input": use_video,
            "keyframes": [os.path.basename(p) for p in keyframe_paths] if not use_video else [],
            "keyframe_count": len(keyframe_paths) if not use_video else 0,
            "vision_enabled": use_vision or use_video,
            "product_image": os.path.basename(product_image) if (product_image and os.path.isfile(product_image)) else None,
            "product_inputs": {k: v for k, v in product_inputs.items() if v and k != "product_image_url"},
        },
        "parameters": {
            "temperature": 0.7,
            "max_tokens": 4096,
            "response_format": "json_schema",
        },
    }


def generate_copy(
    keyframe_paths: list[str],
    product_inputs: dict,
    provider: str = "openrouter",
    user_id: int | None = None,
    custom_system_prompt: str | None = None,
    language: str = "en",
    video_path: str | None = None,
    model_override: str | None = None,
) -> dict:
    """生成短视频文案。

    Args:
        keyframe_paths: 关键帧图片路径列表
        product_inputs: 商品信息 dict（product_title, price, selling_points 等）
        provider: LLM provider（"openrouter" 或 "doubao"）
        user_id: 用户 ID（用于解析 API key）
        custom_system_prompt: 自定义系统提示词，为 None 则用默认
        language: 输出语言 "en" 或 "zh"
        video_path: 原始视频路径（支持视频输入的模型直接传视频）

    Returns:
        dict: {segments, full_text, tone, target_duration}
    """
    from pipeline.translate import resolve_provider_config

    # doubao 多模态走 Ark SDK，不需要 OpenAI client
    is_doubao = provider == "doubao"
    if is_doubao:
        model = _resolve_model_only(provider, user_id=user_id)
        client = None
    else:
        client, model = resolve_provider_config(provider, user_id=user_id)
    if model_override:
        model = model_override

    # 系统提示词
    if custom_system_prompt:
        system_prompt = custom_system_prompt
    elif language == "zh":
        system_prompt = DEFAULT_SYSTEM_PROMPT_ZH
    else:
        system_prompt = DEFAULT_SYSTEM_PROMPT_EN

    # 构建用户消息内容
    content: list[dict[str, Any]] = []

    # 判断是否支持直接视频输入
    use_video = _supports_video(provider, model) and video_path and os.path.isfile(video_path)
    use_vision = _supports_vision(provider) and keyframe_paths and not use_video

    # 豆包多模态：先上传媒体到 TOS，拿 URL
    tos_urls: dict[str, str] = {}  # local_path -> tos_url

    if is_doubao and (use_video or use_vision):
        log.info("豆包多模态：上传媒体文件到 TOS...")
        if use_video:
            tos_urls[video_path] = _upload_to_tos(video_path)
        elif use_vision:
            for path in keyframe_paths:
                tos_urls[path] = _upload_to_tos(path)
        product_image = product_inputs.get("product_image_url") or product_inputs.get("product_image_path")
        if product_image and os.path.isfile(product_image):
            tos_urls[product_image] = _upload_to_tos(product_image)

    # 构建用户消息内容
    content: list[dict[str, Any]] = []

    if use_video:
        log.info("模型支持视频输入，直接传视频: %s", os.path.basename(video_path))
        content.append({"type": "text", "text": "Source video:"})
        if is_doubao:
            content.append({
                "type": "video_url",
                "video_url": {"url": tos_urls[video_path]},
                "tos_url": tos_urls[video_path],
            })
        else:
            content.append({
                "type": "video_url",
                "video_url": {"url": _video_to_base64_url(video_path)},
            })
    elif use_vision:
        content.append({"type": "text", "text": "Video keyframes (in chronological order):"})
        for path in keyframe_paths:
            if is_doubao:
                content.append({
                    "type": "image_url",
                    "image_url": {"url": tos_urls[path]},
                    "tos_url": tos_urls[path],
                })
            else:
                content.append({
                    "type": "image_url",
                    "image_url": {"url": _image_to_base64_url(path)},
                })

    # 商品主图
    product_image = product_inputs.get("product_image_url") or product_inputs.get("product_image_path")
    if (use_vision or use_video) and product_image and os.path.isfile(product_image):
        content.append({"type": "text", "text": "Product image:"})
        if is_doubao:
            content.append({
                "type": "image_url",
                "image_url": {"url": tos_urls.get(product_image, "")},
                "tos_url": tos_urls.get(product_image, ""),
            })
        else:
            content.append({
                "type": "image_url",
                "image_url": {"url": _image_to_base64_url(product_image)},
            })

    # 商品文本信息
    product_text = _build_product_text(product_inputs)
    if not use_vision and not use_video:
        product_text = (
            "[Note: Current model does not support image/video input. "
            "Generating copy based on text information only.]\n\n" + product_text
        )
    if product_text.strip():
        content.append({"type": "text", "text": product_text})

    # 语言指令
    if language == "zh":
        content.append({"type": "text", "text": "请用中文撰写文案。"})
    else:
        content.append({"type": "text", "text": "Write the script in English for the US market."})

    log.info("调用 LLM 生成文案: provider=%s, model=%s, video=%s, images=%d",
             provider, model, bool(use_video), len(keyframe_paths) if use_vision else 0)

    # ── 构建调试信息 ──
    def _truncate_data(obj):
        """递归截断 base64/长 URL 数据。"""
        if isinstance(obj, dict):
            out = {}
            for k, v in obj.items():
                if k in ("url",) and isinstance(v, str) and (v.startswith("data:") or len(v) > 500):
                    out[k] = v[:120] + f"...[TRUNCATED, total {len(v)} chars]"
                elif k == "tos_url":
                    continue  # 不放入日志
                else:
                    out[k] = _truncate_data(v)
            return out
        elif isinstance(obj, list):
            return [_truncate_data(i) for i in obj]
        return obj

    debug_content = []
    for item in content:
        if item["type"] == "text":
            debug_content.append({"type": "text", "text": item["text"]})
        elif item["type"] == "image_url":
            info = "(TOS URL image)" if is_doubao else "(base64 image)"
            debug_content.append({"type": "image", "info": info})
        elif item["type"] == "video_url":
            info = f"(TOS URL video: {os.path.basename(video_path)})" if is_doubao else f"(base64 video: {os.path.basename(video_path)})"
            debug_content.append({"type": "video", "info": info})

    # ── 实际调用 ──
    if is_doubao and (use_video or use_vision):
        # 豆包多模态：用 Ark SDK
        from appcore.api_keys import resolve_key
        api_key = resolve_key(user_id, "doubao_llm", "DOUBAO_LLM_API_KEY")
        from appcore.api_keys import resolve_extra
        extra = resolve_extra(user_id, "doubao_llm") if user_id else {}
        doubao_base_url = extra.get("base_url") or "https://ark.cn-beijing.volces.com/api/v3"

        full_request_log = {
            "endpoint": f"{doubao_base_url}/responses",
            "method": "POST",
            "sdk": "volcenginesdkarkruntime.Ark",
            "headers": {"Authorization": "Bearer ***REDACTED***"},
            "body": {
                "model": model,
                "input": _truncate_data([
                    {"role": "system", "content": [{"type": "input_text", "text": system_prompt}]},
                    {"role": "user", "content": content},
                ]),
            },
        }
        log.info("完整请求报文:\n%s", json.dumps(full_request_log, ensure_ascii=False, indent=2))

        raw, token_usage = _call_doubao_multimodal(
            model=model,
            system_prompt=system_prompt,
            content_items=content,
            api_key=api_key,
            base_url=doubao_base_url,
        )
    else:
        # OpenRouter / 豆包纯文本：用 OpenAI SDK
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content},
        ]

        extra_kwargs: dict[str, Any] = {"temperature": 0.7, "max_tokens": 4096}
        if provider == "openrouter":
            extra_kwargs["extra_body"] = {
                "plugins": [{"id": "response-healing"}],
                "usage": {"include": True},
            }
        extra_kwargs["response_format"] = COPYWRITING_RESPONSE_FORMAT

        base_url = client.base_url if hasattr(client, 'base_url') else "unknown"
        full_request_log = {
            "endpoint": f"{base_url}chat/completions",
            "method": "POST",
            "headers": {"Authorization": "Bearer ***REDACTED***", "Content-Type": "application/json"},
            "body": {
                "model": model,
                "messages": _truncate_data(messages),
                "temperature": 0.7,
                "max_tokens": 4096,
                "response_format": extra_kwargs.get("response_format"),
            },
        }
        log.info("完整请求报文:\n%s", json.dumps(full_request_log, ensure_ascii=False, indent=2))

        response = client.chat.completions.create(
            model=model,
            messages=messages,
            **extra_kwargs,
        )
        raw = response.choices[0].message.content
        # 提取 OpenAI SDK token 用量
        token_usage = None
        usage = getattr(response, "usage", None)
        if usage:
            token_usage = {
                "input_tokens": getattr(usage, "prompt_tokens", None),
                "output_tokens": getattr(usage, "completion_tokens", None),
            }
            if provider == "openrouter":
                from config import USD_TO_CNY

                cost_usd = getattr(usage, "cost", None)
                if cost_usd not in (None, ""):
                    try:
                        token_usage["cost_cny"] = (
                            Decimal(str(cost_usd)) * Decimal(str(USD_TO_CNY))
                        ).quantize(Decimal("0.000001"))
                    except Exception:
                        pass
            log.info("copywriting token usage: input=%s, output=%s",
                     token_usage["input_tokens"], token_usage["output_tokens"])

    debug_info = {
        "provider": provider,
        "model": model,
        "system_prompt": system_prompt,
        "user_content": debug_content,
        "video_input": use_video,
        "video_file": os.path.basename(video_path) if use_video else None,
        "image_count": len(keyframe_paths) if use_vision else 0,
        "keyframe_paths": [os.path.basename(p) for p in keyframe_paths] if not use_video else [],
        "tos_urls": {os.path.basename(k): v[:80] + "..." for k, v in tos_urls.items()} if tos_urls else None,
        "full_request": full_request_log,
    }
    result = _parse_json_content(raw)

    # 补充 index
    for i, seg in enumerate(result.get("segments", [])):
        seg["index"] = i

    result["_debug"] = debug_info
    if token_usage:
        result["_usage"] = token_usage

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
    from pipeline.translate import resolve_provider_config

    client, model = resolve_provider_config(provider, user_id=user_id)

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
    result = _parse_json_content(raw)
    # 提取 token 用量
    usage = getattr(response, "usage", None)
    if usage:
        result["_usage"] = {
            "input_tokens": getattr(usage, "prompt_tokens", None),
            "output_tokens": getattr(usage, "completion_tokens", None),
        }
        log.info("rewrite_segment token usage: input=%s, output=%s",
                 result["_usage"]["input_tokens"], result["_usage"]["output_tokens"])
    return result
