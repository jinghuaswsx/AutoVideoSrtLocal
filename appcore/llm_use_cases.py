"""LLM UseCase 注册表。

每个业务功能（模块.功能）对应一个 use_case_code，绑定默认 provider + model，
以及记录 usage_logs 时的 service 名称。UI / resolver / adapter 全部读取这里。

provider_code 枚举：
    openrouter      - OpenRouter (OpenAI-compatible)
    doubao          - 火山引擎 ARK (OpenAI-compatible)
    gemini_aistudio - Google AI Studio (GEMINI_API_KEY)
    gemini_vertex   - Google Cloud Express Mode (GEMINI_CLOUD_API_KEY, vertexai=True)

视频翻译 v1 三项默认 provider 和 master 的
DEFAULT_TRANSLATE_PROVIDER="vertex_gemini_31_flash_lite" 对齐。
"""
from __future__ import annotations

from typing import TypedDict


class UseCase(TypedDict):
    code: str
    module: str
    label: str
    description: str
    default_provider: str
    default_model: str
    usage_log_service: str
    units_type: str


def _uc(code, module, label, desc, provider, model, service, units_type) -> UseCase:
    return {
        "code": code,
        "module": module,
        "label": label,
        "description": desc,
        "default_provider": provider,
        "default_model": model,
        "usage_log_service": service,
        "units_type": units_type,
    }


USE_CASES: dict[str, UseCase] = {
    # 视频翻译 v1
    "video_translate.localize": _uc(
        "video_translate.localize",
        "video_translate",
        "本土化改写",
        "视频翻译主流程中把中文转成目标语言本土化文本",
        "gemini_vertex",
        "gemini-3.1-flash-lite-preview",
        "gemini_vertex",
        "tokens",
    ),
    "video_translate.tts_script": _uc(
        "video_translate.tts_script",
        "video_translate",
        "TTS 脚本生成",
        "根据本土化文本切分成适合配音的 TTS 脚本段落",
        "gemini_vertex",
        "gemini-3.1-flash-lite-preview",
        "gemini_vertex",
        "tokens",
    ),
    "video_translate.rewrite": _uc(
        "video_translate.rewrite",
        "video_translate",
        "字数收敛重写",
        "TTS 时长不达标时回卷到文案重写的内循环",
        "gemini_vertex",
        "gemini-3.1-flash-lite-preview",
        "gemini_vertex",
        "tokens",
    ),
    "video_translate.tts": _uc(
        "video_translate.tts",
        "video_translate",
        "TTS 配音",
        "ElevenLabs 生成本土化配音",
        "elevenlabs",
        "<runtime>",
        "elevenlabs",
        "chars",
    ),
    "video_translate.tts_language_check": _uc(
        "video_translate.tts_language_check",
        "video_translate",
        "TTS 语言校验",
        "Gemini 检查 ElevenLabs 最终使用的 TTS 文案是否为目标语种",
        "openrouter",
        "google/gemini-3.1-flash-lite-preview",
        "openrouter",
        "tokens",
    ),
    "video_translate.asr": _uc(
        "video_translate.asr",
        "video_translate",
        "ASR 识别",
        "豆包语音识别原视频",
        "doubao_asr",
        "big-model",
        "doubao_asr",
        "seconds",
    ),
    # 视频翻译 v2（音画同步）
    "video_translate.shot_notes": _uc(
        "video_translate.shot_notes",
        "video_translate",
        "画面笔记",
        "v2 Stage1: 多模态 LLM 看视频，输出全局摘要 + 逐句画面笔记",
        "gemini_aistudio",
        "gemini-3.1-pro-preview",
        "gemini_video_analysis",
        "tokens",
    ),
    "video_translate.av_localize": _uc(
        "video_translate.av_localize",
        "video_translate",
        "音画同步翻译",
        "v2 Stage2: 纯文本 LLM 按画面笔记 + 带货 context + 时长约束做本地化口播",
        "openrouter",
        "anthropic/claude-sonnet-4.6",
        "openrouter",
        "tokens",
    ),
    "video_translate.av_rewrite": _uc(
        "video_translate.av_rewrite",
        "video_translate",
        "音画同步单句重写",
        "v2 Stage2 的时长超限局部重写",
        "openrouter",
        "anthropic/claude-sonnet-4.6",
        "openrouter",
        "tokens",
    ),
    # 日语视频翻译专用流程
    "ja_translate.localize": _uc(
        "ja_translate.localize",
        "ja_translate",
        "日语逐句本土化",
        "视频翻译（日语）按 ASR 段和字符预算生成自然日语配音文案",
        "openrouter",
        "anthropic/claude-sonnet-4.6",
        "openrouter",
        "tokens",
    ),
    "ja_translate.rewrite": _uc(
        "ja_translate.rewrite",
        "ja_translate",
        "日语尺长收敛重写",
        "视频翻译（日语）在 TTS 实测后按字符预算做多轮长短收敛",
        "openrouter",
        "anthropic/claude-sonnet-4.6",
        "openrouter",
        "tokens",
    ),
    # 文案创作
    "copywriting.generate": _uc(
        "copywriting.generate",
        "copywriting",
        "文案生成",
        "根据关键帧+商品信息生成带货文案",
        "openrouter",
        "anthropic/claude-sonnet-4.6",
        "openrouter",
        "tokens",
    ),
    "copywriting.rewrite": _uc(
        "copywriting.rewrite",
        "copywriting",
        "文案段重写",
        "单段文案重写",
        "openrouter",
        "anthropic/claude-sonnet-4.6",
        "openrouter",
        "tokens",
    ),
    "copywriting_translate.generate": _uc(
        "copywriting_translate.generate",
        "copywriting",
        "文案翻译",
        "把英文带货文案翻译成目标语种",
        "openrouter",
        "anthropic/claude-sonnet-4.6",
        "openrouter",
        "tokens",
    ),
    # 视频分析（沿用 Gemini AI Studio；usage_log 归到 gemini_video_analysis）
    "video_score.run": _uc(
        "video_score.run",
        "video_analysis",
        "视频评分",
        "对硬字幕成片按美国带货要素打分",
        "gemini_aistudio",
        "gemini-3.1-pro-preview",
        "gemini_video_analysis",
        "tokens",
    ),
    "video_review.analyze": _uc(
        "video_review.analyze",
        "video_analysis",
        "视频评测",
        "按用户自定义 prompt 分析视频",
        "gemini_aistudio",
        "gemini-3.1-pro-preview",
        "gemini_video_analysis",
        "tokens",
    ),
    "shot_decompose.run": _uc(
        "shot_decompose.run",
        "video_analysis",
        "分镜拆解",
        "Gemini 识别镜头切换并描述画面",
        "gemini_aistudio",
        "gemini-3.1-pro-preview",
        "gemini_video_analysis",
        "tokens",
    ),
    # 素材管理：商品主图 + 商品链接 + 第一条英语推广视频的欧洲小语种市场评估。
    "material_evaluation.evaluate": _uc(
        "material_evaluation.evaluate",
        "material",
        "商品素材推广评估",
        "根据商品主图、商品链接和第一条英语推广视频，评估欧洲小语种市场推广适配度",
        "openrouter",
        "google/gemini-3.1-pro-preview",
        "openrouter",
        "tokens",
    ),
    # 图片 & 链接（usage_log 归到通用 gemini）
    "image_translate.detect": _uc(
        "image_translate.detect",
        "image",
        "图片文字检测",
        "判定商品图中是否存在需要翻译的可读文字",
        "gemini_vertex",
        "gemini-3.1-flash-lite-preview",
        "gemini",
        "images",
    ),
    "image_translate.generate": _uc(
        "image_translate.generate",
        "image",
        "图片本地化重绘",
        "用图像模型重绘目标语种的商品图",
        "gemini_aistudio",
        "gemini-3.1-flash-image-preview",
        "gemini",
        "images",
    ),
    "link_check.analyze": _uc(
        "link_check.analyze",
        "image",
        "链接商品图审查",
        "核查外链商品图文字是否匹配目标语种",
        "gemini_aistudio",
        "gemini-2.5-flash",
        "gemini",
        "tokens",
    ),
    "link_check.same_image": _uc(
        "link_check.same_image",
        "image",
        "同图判断",
        "比较抓取图与参考图是否属于同一基础图片",
        "gemini_aistudio",
        "gemini-3.1-flash-lite-preview",
        "gemini",
        "tokens",
    ),
    # 文本翻译（对齐重构前，translate_text() 默认 provider="openrouter"）
    "text_translate.generate": _uc(
        "text_translate.generate",
        "text_translate",
        "纯文本翻译",
        "把任意文本翻译到目标语言",
        "openrouter",
        "anthropic/claude-sonnet-4.6",
        "openrouter",
        "tokens",
    ),
}


def get_use_case(code: str) -> UseCase:
    if code not in USE_CASES:
        raise KeyError(f"unknown use_case: {code}")
    return USE_CASES[code]


def list_by_module() -> dict[str, list[UseCase]]:
    groups: dict[str, list[UseCase]] = {}
    for uc in USE_CASES.values():
        groups.setdefault(uc["module"], []).append(uc)
    return groups


MODULE_LABELS: dict[str, str] = {
    "video_translate": "视频翻译",
    "copywriting": "文案创作",
    "video_analysis": "视频分析",
    "material": "素材管理",
    "image": "图片 & 链接",
    "text_translate": "文本翻译",
}
