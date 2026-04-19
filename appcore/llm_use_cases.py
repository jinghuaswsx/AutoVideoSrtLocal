"""LLM UseCase 注册表。

每个业务功能（模块.功能）对应一个 use_case_code，绑定默认 provider + model
以及写 usage_logs 时的 service 名称。UI / resolver / adapter 全部读这里。

provider_code 枚举：
    openrouter      - OpenRouter (OpenAI-compatible)
    doubao          - 火山引擎 ARK (OpenAI-compatible)
    gemini_aistudio - Google AI Studio (GEMINI_API_KEY)
    gemini_vertex   - Google Cloud Express Mode (GEMINI_CLOUD_API_KEY, vertexai=True)

视频翻译三项默认 provider 和 master 的 DEFAULT_TRANSLATE_PROVIDER="vertex_gemini_31_flash_lite" 对齐。
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


def _uc(code, module, label, desc, provider, model, service) -> UseCase:
    return {
        "code": code, "module": module, "label": label,
        "description": desc, "default_provider": provider,
        "default_model": model, "usage_log_service": service,
    }


USE_CASES: dict[str, UseCase] = {
    # ── 视频翻译 ── 默认走 Vertex Express Mode（与 master DEFAULT_TRANSLATE_PROVIDER 对齐）
    "video_translate.localize": _uc(
        "video_translate.localize", "video_translate", "本土化改写",
        "视频翻译主流程中把中文转成目标语言本土化文本",
        "gemini_vertex", "gemini-3.1-flash-lite-preview", "gemini_vertex",
    ),
    "video_translate.tts_script": _uc(
        "video_translate.tts_script", "video_translate", "TTS 脚本生成",
        "根据本土化文本切分成适合配音的 TTS 脚本段落",
        "gemini_vertex", "gemini-3.1-flash-lite-preview", "gemini_vertex",
    ),
    "video_translate.rewrite": _uc(
        "video_translate.rewrite", "video_translate", "字数收敛重写",
        "TTS 时长不达标时回卷到文案重写的内循环",
        "gemini_vertex", "gemini-3.1-flash-lite-preview", "gemini_vertex",
    ),
    # ── 文案创作 ──
    "copywriting.generate": _uc(
        "copywriting.generate", "copywriting", "文案生成",
        "根据关键帧+商品信息生成带货文案",
        "openrouter", "anthropic/claude-sonnet-4.6", "openrouter",
    ),
    "copywriting.rewrite": _uc(
        "copywriting.rewrite", "copywriting", "文案段重写",
        "单段文案重写",
        "openrouter", "anthropic/claude-sonnet-4.6", "openrouter",
    ),
    # ── 视频分析 ──（沿用 Gemini AI Studio，usage_log 归到 gemini_video_analysis）
    "video_score.run": _uc(
        "video_score.run", "video_analysis", "视频评分",
        "对硬字幕成片按美国带货要素打分",
        "gemini_aistudio", "gemini-3.1-pro-preview", "gemini_video_analysis",
    ),
    "video_review.analyze": _uc(
        "video_review.analyze", "video_analysis", "视频评测",
        "按用户自定义 prompt 分析视频",
        "gemini_aistudio", "gemini-3.1-pro-preview", "gemini_video_analysis",
    ),
    "shot_decompose.run": _uc(
        "shot_decompose.run", "video_analysis", "分镜拆解",
        "Gemini 识别镜头切换并描述画面",
        "gemini_aistudio", "gemini-3.1-pro-preview", "gemini_video_analysis",
    ),
    # ── 图片 & 链接 ──（usage_log 归到通用 gemini）
    "image_translate.detect": _uc(
        "image_translate.detect", "image", "图片文字检测",
        "判定商品图是否已本地化为目标语种",
        "gemini_aistudio", "gemini-2.5-flash", "gemini",
    ),
    "image_translate.generate": _uc(
        "image_translate.generate", "image", "图片本地化重绘",
        "用图像模型重绘目标语种的商品图",
        "gemini_aistudio", "gemini-3-pro-image-preview", "gemini",
    ),
    "link_check.analyze": _uc(
        "link_check.analyze", "image", "链接商品图审查",
        "核查外链商品图文字是否匹配目标语种",
        "gemini_aistudio", "gemini-2.5-flash", "gemini",
    ),
    # ── 文本翻译 ──（主线未改 pipeline/text_translate.py，保持 OpenRouter 默认）
    "text_translate.generate": _uc(
        "text_translate.generate", "text_translate", "纯文本翻译",
        "把任意文本翻译到目标语言",
        "openrouter", "google/gemini-3.1-flash-lite-preview", "openrouter",
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
    "image": "图片 & 链接",
    "text_translate": "文本翻译",
}
