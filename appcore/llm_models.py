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


# 老式 admin 偏好字符串 → 具体 model_id（用于 UI 显示 / invoke_chat 的
# model_override）。只用作纯数据映射，不创建客户端、不触发 SDK。
LEGACY_PROVIDER_MODEL_MAP: dict[str, str] = {
    "vertex_gemini_31_flash_lite":     "gemini-3.1-flash-lite-preview",
    "vertex_gemini_3_flash":           "gemini-3-flash-preview",
    "vertex_gemini_31_pro":            "gemini-3.1-pro-preview",
    "vertex_adc_gemini_31_flash_lite": "gemini-3.1-flash-lite-preview",
    "vertex_adc_gemini_3_flash":       "gemini-3-flash-preview",
    "vertex_adc_gemini_31_pro":        "gemini-3.1-pro-preview",
    "gemini_31_flash":                 "google/gemini-3.1-flash-lite-preview",
    "gemini_31_pro":                   "google/gemini-3.1-pro-preview",
    "gemini_3_flash":                  "google/gemini-3-flash-preview",
    "gpt_5_mini":                      "openai/gpt-5-mini",
    "gpt_5_5":                         "openai/gpt-5.5",
    "claude_sonnet":                   "anthropic/claude-sonnet-4.6",
    "openrouter":                      "anthropic/claude-sonnet-4.6",
    "doubao":                          "doubao-seed-2-0-pro-260215",
}


def legacy_provider_to_model(provider: str | None) -> str | None:
    """老 provider 字符串 → model_id；不命中返回 None。"""
    if not provider:
        return None
    return LEGACY_PROVIDER_MODEL_MAP.get(provider)


def legacy_provider_to_provider_code(provider: str | None) -> str | None:
    """老 provider 字符串 → adapter provider_code（doubao / openrouter /
    gemini_vertex / gemini_vertex_adc）。"""
    if not provider:
        return None
    if provider == "doubao":
        return "doubao"
    if provider.startswith("vertex_adc_"):
        return "gemini_vertex_adc"
    if provider.startswith("vertex_"):
        return "gemini_vertex"
    return "openrouter"


__all__ = [
    "VIDEO_CAPABLE_MODELS",
    "model_display_name",
    "LEGACY_PROVIDER_MODEL_MAP",
    "legacy_provider_to_model",
    "legacy_provider_to_provider_code",
]
