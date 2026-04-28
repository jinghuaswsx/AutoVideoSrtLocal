"""LLM UseCase 注册表。"""

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


def _uc(
    code: str,
    module: str,
    label: str,
    desc: str,
    provider: str,
    model: str,
    service: str,
    units_type: str,
) -> UseCase:
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
    # 全能视频翻译（多源语种实验模块，独立于 video_translate）
    "omni_translate.lid": _uc(
        "omni_translate.lid",
        "omni_translate",
        "源语言识别（LID）",
        "ASR 后用 LLM 识别 transcript 真实语种，覆盖 task.source_language",
        "gemini_vertex",
        "gemini-3.1-flash-lite-preview",
        "gemini_vertex",
        "tokens",
    ),
    # ASR 同语言纯净化（omni 用于 _step_asr_clean，multi 用于 asr_normalize 前置）
    "asr_clean.purify_primary": _uc(
        "asr_clean.purify_primary",
        "asr_clean",
        "ASR 同语言纯净化（主路）",
        "Gemini Flash 主路：把 ASR 结果纯净化为同语言纯净文本，保留时间戳",
        "gemini_vertex",
        "gemini-3.1-flash-lite-preview",
        "gemini_vertex",
        "tokens",
    ),
    "asr_clean.purify_fallback": _uc(
        "asr_clean.purify_fallback",
        "asr_clean",
        "ASR 同语言纯净化（兜底）",
        "Claude Sonnet 兜底：主路校验失败时重跑同样 prompt",
        "openrouter",
        "anthropic/claude-sonnet-4.6",
        "openrouter",
        "tokens",
    ),
    # 翻译质量评估（subtitle 完成后异步触发，omni / multi 共用）
    "translation_quality.assess": _uc(
        "translation_quality.assess",
        "translation_quality",
        "翻译质量评估",
        "对比原始 ASR / 翻译文案 / 二次 ASR 字幕，给出翻译质量分 + TTS 还原度分",
        "gemini_vertex",
        "gemini-3.1-flash-lite-preview",
        "gemini_vertex",
        "tokens",
    ),
    # 视频翻译 v1
    "video_translate.localize": _uc(
        "video_translate.localize",
        "video_translate",
        "本土化改写",
        "视频翻译主流程中的本土化文案生成",
        "gemini_vertex",
        "gemini-3.1-flash-lite-preview",
        "gemini_vertex",
        "tokens",
    ),
    "video_translate.tts_script": _uc(
        "video_translate.tts_script",
        "video_translate",
        "TTS 脚本生成",
        "根据本土化文案切分为适合配音的 TTS 脚本",
        "gemini_vertex",
        "gemini-3.1-flash-lite-preview",
        "gemini_vertex",
        "tokens",
    ),
    "video_translate.rewrite": _uc(
        "video_translate.rewrite",
        "video_translate",
        "字数收敛重写",
        "TTS 时长不达标时的局部重写",
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
        "校验最终 TTS 文案是否为目标语言",
        "openrouter",
        "google/gemini-3.1-flash-lite-preview",
        "openrouter",
        "tokens",
    ),
    "video_translate.asr": _uc(
        "video_translate.asr",
        "video_translate",
        "ASR 识别",
        "豆包 ASR 识别原视频语音",
        "doubao_asr",
        "big-model",
        "doubao_asr",
        "seconds",
    ),
    # 视频翻译 v2
    "video_translate.shot_notes": _uc(
        "video_translate.shot_notes",
        "video_translate",
        "画面笔记",
        "多模态分析视频并输出逐句画面笔记",
        "gemini_aistudio",
        "gemini-3.1-pro-preview",
        "gemini_video_analysis",
        "tokens",
    ),
    "video_translate.av_localize": _uc(
        "video_translate.av_localize",
        "video_translate",
        "音画同步翻译",
        "按画面笔记和时长约束生成本土化口播",
        "openrouter",
        "openai/gpt-5.5",
        "openrouter",
        "tokens",
    ),
    "video_translate.av_rewrite": _uc(
        "video_translate.av_rewrite",
        "video_translate",
        "音画同步重写",
        "音画同步流程中的局部重写",
        "openrouter",
        "openai/gpt-5.5",
        "openrouter",
        "tokens",
    ),
    # 日语视频翻译
    "ja_translate.localize": _uc(
        "ja_translate.localize",
        "ja_translate",
        "日语逐句本土化",
        "为日语视频生成自然的日语配音文案",
        "openrouter",
        "anthropic/claude-sonnet-4.6",
        "openrouter",
        "tokens",
    ),
    "ja_translate.rewrite": _uc(
        "ja_translate.rewrite",
        "ja_translate",
        "日语长度收敛重写",
        "日语视频翻译在 TTS 后按字符预算重写",
        "openrouter",
        "anthropic/claude-sonnet-4.6",
        "openrouter",
        "tokens",
    ),
    # 翻译实验室
    "translate_lab.shot_translate": _uc(
        "translate_lab.shot_translate",
        "translate_lab",
        "分镜逐句翻译",
        "翻译实验室按分镜逐句翻译口播文案",
        "gemini_aistudio",
        "gemini-3.1-pro-preview",
        "gemini",
        "tokens",
    ),
    "translate_lab.tts_refine": _uc(
        "translate_lab.tts_refine",
        "translate_lab",
        "TTS 超时压缩改写",
        "翻译实验室在 TTS 超时后压缩改写文案",
        "gemini_aistudio",
        "gemini-3.1-pro-preview",
        "gemini",
        "tokens",
    ),
    # 文案
    "copywriting.generate": _uc(
        "copywriting.generate",
        "copywriting",
        "文案生成",
        "根据商品信息生成带货文案",
        "openrouter",
        "anthropic/claude-sonnet-4.6",
        "openrouter",
        "tokens",
    ),
    "copywriting.rewrite": _uc(
        "copywriting.rewrite",
        "copywriting",
        "文案段落重写",
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
        "把英文带货文案翻译成目标语言",
        "openrouter",
        "anthropic/claude-sonnet-4.6",
        "openrouter",
        "tokens",
    ),
    # 视频分析
    "video_score.run": _uc(
        "video_score.run",
        "video_analysis",
        "视频评分",
        "按带货维度给视频成片打分",
        "gemini_aistudio",
        "gemini-3.1-pro-preview",
        "gemini_video_analysis",
        "tokens",
    ),
    "video_review.analyze": _uc(
        "video_review.analyze",
        "video_analysis",
        "视频评测",
        "按自定义 prompt 分析视频",
        "gemini_aistudio",
        "gemini-3.1-pro-preview",
        "gemini_video_analysis",
        "tokens",
    ),
    "shot_decompose.run": _uc(
        "shot_decompose.run",
        "video_analysis",
        "分镜拆解",
        "识别镜头切换并生成分镜描述",
        "gemini_aistudio",
        "gemini-3.1-pro-preview",
        "gemini_video_analysis",
        "tokens",
    ),
    "video_csk.analyze": _uc(
        "video_csk.analyze",
        "video_analysis",
        "CSK 深度分析",
        "对视频做深度特征锁定和关键帧抽取",
        "gemini_aistudio",
        "gemini-3.1-pro-preview",
        "gemini_video_analysis",
        "tokens",
    ),
    # 素材管理
    "material_evaluation.evaluate": _uc(
        "material_evaluation.evaluate",
        "material",
        "素材评估",
        "根据商品图、链接和视频评估市场适配度",
        "openrouter",
        "google/gemini-3.1-pro-preview",
        "openrouter",
        "tokens",
    ),
    # 图片与链接
    "image_translate.detect": _uc(
        "image_translate.detect",
        "image",
        "图片文字检测",
        "判断商品图中是否存在需要翻译的可读文字",
        "gemini_vertex",
        "gemini-3.1-flash-lite-preview",
        "gemini",
        "images",
    ),
    "image_translate.generate": _uc(
        "image_translate.generate",
        "image",
        "图片本地化重绘",
        "用图像模型重绘目标语言商品图",
        "gemini_aistudio",
        "gemini-3.1-flash-image-preview",
        "gemini",
        "images",
    ),
    "link_check.analyze": _uc(
        "link_check.analyze",
        "image",
        "链接商品图审核",
        "核查外链商品图文字是否匹配目标语言",
        "gemini_aistudio",
        "gemini-2.5-flash",
        "gemini",
        "tokens",
    ),
    "link_check.same_image": _uc(
        "link_check.same_image",
        "image",
        "同图判定",
        "比较抓取图与参考图是否属于同一基础图片",
        "gemini_aistudio",
        "gemini-3.1-flash-lite-preview",
        "gemini",
        "tokens",
    ),
    # 文本翻译
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
    "title_translate.generate": _uc(
        "title_translate.generate",
        "text_translate",
        "标题翻译",
        "把结构化标题文案翻译成目标语言",
        "openrouter",
        "anthropic/claude-sonnet-4.6",
        "openrouter",
        "tokens",
    ),
    # 提示词库
    "prompt_library.generate": _uc(
        "prompt_library.generate",
        "prompt_library",
        "提示词生成",
        "根据需求生成新的系统提示词",
        "openrouter",
        "anthropic/claude-sonnet-4.6",
        "openrouter",
        "tokens",
    ),
    "prompt_library.translate": _uc(
        "prompt_library.translate",
        "prompt_library",
        "提示词互译",
        "在中英文之间翻译提示词内容",
        "openrouter",
        "anthropic/claude-sonnet-4.6",
        "openrouter",
        "tokens",
    ),
    # 视频创作
    "video_creation.generate": _uc(
        "video_creation.generate",
        "video_creation",
        "视频创作生成",
        "调用 Seedance 2.0 生成新视频",
        "doubao",
        "doubao-seedance-2-0-260128",
        "doubao",
        "seconds",
    ),
    # 原文标准化（ASR 后插入步骤）
    "asr_normalize.detect_language": _uc(
        "asr_normalize.detect_language",
        "video_translate",
        "原文语言识别",
        "ASR 完成后识别原视频语言以决定标准化路由",
        "openrouter",
        "google/gemini-3.1-flash-lite-preview",
        "openrouter",
        "tokens",
    ),
    "asr_normalize.translate_zh_to_en": _uc(
        "asr_normalize.translate_zh_to_en",
        "video_translate",
        "中文 ASR → en-US 标准化",
        "中文素材 ASR 文本翻译为 en-US（注册保留，runner 当前路由跳过）",
        "openrouter",
        "anthropic/claude-sonnet-4.6",
        "openrouter",
        "tokens",
    ),
    "asr_normalize.translate_es_to_en": _uc(
        "asr_normalize.translate_es_to_en",
        "video_translate",
        "西语 ASR → en-US 标准化",
        "西班牙语素材 ASR 文本精修翻译为 en-US",
        "openrouter",
        "anthropic/claude-sonnet-4.6",
        "openrouter",
        "tokens",
    ),
    "asr_normalize.translate_generic_to_en": _uc(
        "asr_normalize.translate_generic_to_en",
        "video_translate",
        "任意源 → en-US 标准化（兜底）",
        "白名单内非中英文素材 ASR 文本通用翻译为 en-US",
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
    "omni_translate": "全能翻译",
    "video_translate": "视频翻译",
    "ja_translate": "日语翻译",
    "translate_lab": "翻译实验室",
    "copywriting": "文案创作",
    "video_analysis": "视频分析",
    "material": "素材管理",
    "image": "图片 & 链接",
    "text_translate": "文本翻译",
    "prompt_library": "提示词库",
    "video_creation": "视频创作",
    "asr_clean": "ASR 同语言纯净化",
    "translation_quality": "翻译质量评估",
}
