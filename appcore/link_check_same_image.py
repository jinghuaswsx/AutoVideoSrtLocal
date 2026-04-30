from __future__ import annotations

from pathlib import Path

from appcore import llm_bindings, llm_client
from appcore.image_translate_settings import CHANNEL_LABELS
from appcore.llm_use_cases import get_use_case

_DEFAULT_MODEL = get_use_case("link_check.same_image")["default_model"]
_PROVIDER_CHANNELS = {
    "gemini_aistudio": "aistudio",
    "gemini_vertex": "cloud",
    "gemini_vertex_adc": "cloud_adc",
    "openrouter": "openrouter",
    "doubao": "doubao",
}
_PROVIDER_LABELS = {
    "doubao": "豆包",
    "gemini_vertex_adc": "Google Vertex AI (ADC)",
}


def _build_prompt() -> str:
    return (
        "你会收到两张图片。第一张是网站抓取图，第二张是参考图。\n"
        "请先分别提取两张图片中的全部可见文字。\n"
        "如果只要任意一张图能提取出文字且两张图文字不一致，就直接返回“不是”。\n"
        "如果两张图都没有可识别文字，继续判断它们是否属于同一张基础图片。\n"
        "只有在文字一致，或者两张图都没有可识别文字的前提下，才继续比较是否为同一张基础图片。\n"
        "比较时请忽略尺寸差异、压缩差异、导出格式差异。\n"
        "不要做语言、排版质量或翻译质量分析，也不要解释原因。\n"
        "只返回“是”或“不是”。"
    )


def _extract_text(raw: object) -> str:
    if not isinstance(raw, dict):
        return ""
    text = raw.get("text")
    if isinstance(text, str) and text.strip():
        return text.strip()
    json_payload = raw.get("json")
    if isinstance(json_payload, dict):
        answer = json_payload.get("answer")
        if isinstance(answer, str) and answer.strip():
            return answer.strip()
        text = json_payload.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()
    answer = raw.get("answer")
    if isinstance(answer, str) and answer.strip():
        return answer.strip()
    return ""


def _normalize_answer(text: str) -> str | None:
    normalized = (text or "").strip().lower()
    if not normalized:
        return None
    if "不是" in normalized or normalized in {"no", "false", "否"}:
        return "不是"
    if normalized == "是" or normalized.startswith("是") or normalized in {"yes", "true"}:
        return "是"
    return None


def _provider_to_channel(provider: str) -> str:
    return _PROVIDER_CHANNELS.get(provider, provider)


def _channel_label(*, provider: str, channel: str) -> str:
    if channel in CHANNEL_LABELS:
        return CHANNEL_LABELS[channel]
    if provider in _PROVIDER_LABELS:
        return _PROVIDER_LABELS[provider]
    return provider or channel


def judge_same_image(site_path: str | Path, reference_path: str | Path) -> dict:
    try:
        binding = llm_bindings.resolve("link_check.same_image")
        provider = str(binding.get("provider") or "")
        model = str(binding.get("model") or _DEFAULT_MODEL)
        channel = _provider_to_channel(provider)
        payload = llm_client.invoke_generate(
            "link_check.same_image",
            prompt=_build_prompt(),
            media=[Path(site_path), Path(reference_path)],
            temperature=0,
        )
        answer = _normalize_answer(_extract_text(payload))
        if answer is None:
            raise ValueError("LLM 返回为空或无法识别为“是 / 不是”")
        return {
            "status": "done",
            "answer": answer,
            "channel": channel,
            "channel_label": _channel_label(provider=provider, channel=channel),
            "model": model,
            "reason": "",
        }
    except Exception as exc:
        try:
            binding = llm_bindings.resolve("link_check.same_image")
            provider = str(binding.get("provider") or "")
            model = str(binding.get("model") or _DEFAULT_MODEL)
            channel = _provider_to_channel(provider)
            channel_label = _channel_label(provider=provider, channel=channel)
        except Exception:
            provider = ""
            model = _DEFAULT_MODEL
            channel = "aistudio"
            channel_label = CHANNEL_LABELS.get(channel, channel)
        return {
            "status": "error",
            "answer": "",
            "channel": channel,
            "channel_label": channel_label,
            "model": model,
            "reason": str(exc),
        }
