"""LLM 模型枚举与展示工具。

仅放纯数据 + 字符串映射；不导入 google.genai / openai SDK，让业务模块
（settings / runtime / video_review）可以脱离 appcore.gemini 这种 SDK
直连模块也能拿到模型清单和展示名。

历史上 VIDEO_CAPABLE_MODELS / model_display_name 住在 appcore/gemini.py，
B-1/B-3 把 SDK helper 抽到 _helpers/gemini_calls.py 后，gemini.py 只剩
service-routed generate / generate_stream / resolve_config 兼容入口；
业务模块再 import 整个 gemini 只为拿模型枚举太重，全部迁到本模块。
"""
from __future__ import annotations


# 支持视频分析的 Gemini 3 系列模型（id, display_name）
VIDEO_CAPABLE_MODELS: list[tuple[str, str]] = [
    ("gemini-3.1-pro-preview",        "Gemini 3.1 Pro"),
    ("gemini-3-flash-preview",        "Gemini 3 Flash"),
    ("gemini-3.1-flash-lite-preview", "Gemini 3.1 Flash-Lite"),
]


def model_display_name(model_id: str) -> str:
    """根据 model_id 返回可展示的名称；找不到时回退原始 id。"""
    for mid, label in VIDEO_CAPABLE_MODELS:
        if mid == model_id:
            return label
    return model_id or ""


__all__ = ["VIDEO_CAPABLE_MODELS", "model_display_name"]
