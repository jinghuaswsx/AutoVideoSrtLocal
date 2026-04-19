from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Any

from google.genai import types as genai_types

from appcore.gemini_image import (
    GEMINI_AISTUDIO_API_KEY,
    GEMINI_CLOUD_API_KEY,
    OPENROUTER_API_KEY,
    OPENROUTER_BASE_URL,
    _get_image_client,
    _resolve_channel,
)
from appcore.image_translate_settings import CHANNEL_LABELS


_DEFAULT_MODEL = "gemini-3.1-flash-lite-preview"
_OPENROUTER_MODEL = "google/gemini-3.1-flash-lite-preview"


def _guess_mime(path: str | Path) -> str:
    mime, _ = mimetypes.guess_type(str(path))
    return mime or "image/jpeg"


def _build_prompt() -> str:
    return (
        "你会收到两张图片。第一张是网站抓取图，第二张是参考图。"
        "请忽略尺寸差异、压缩差异、导出格式差异，只判断从视觉上看，它们是否属于同一张基础图片。"
        "不要做语言、排版质量或翻译质量分析，也不要解释原因。"
        "只返回“是”或“不是”。"
    )


def _extract_genai_text(resp: Any) -> str:
    text = getattr(resp, "text", None)
    if text:
        return str(text).strip()
    for candidate in getattr(resp, "candidates", None) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or []:
            part_text = getattr(part, "text", None)
            if part_text:
                return str(part_text).strip()
    return ""


def _extract_openrouter_text(resp: Any) -> str:
    for choice in getattr(resp, "choices", None) or []:
        message = getattr(choice, "message", None)
        content = getattr(message, "content", "")
        if isinstance(content, str):
            if content.strip():
                return content.strip()
            continue
        if isinstance(content, list):
            chunks: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    value = str(item.get("text") or "").strip()
                    if value:
                        chunks.append(value)
            if chunks:
                return "\n".join(chunks)
    return ""


def _normalize_answer(text: str) -> str:
    normalized = (text or "").strip().lower()
    if not normalized:
        return "不是"
    if "不是" in normalized:
        return "不是"
    if normalized == "是" or normalized.startswith("是") or normalized in {"yes", "true"}:
        return "是"
    return "不是"


def _call_genai_same_image(*, channel: str, model: str, prompt: str, site_path: str | Path, reference_path: str | Path) -> dict:
    api_key = GEMINI_CLOUD_API_KEY if channel == "cloud" else GEMINI_AISTUDIO_API_KEY
    if not api_key:
        label = CHANNEL_LABELS.get(channel, channel)
        raise RuntimeError(f"{label} 未配置 API key")
    client = _get_image_client(api_key, backend=channel)
    contents = [
        genai_types.Part.from_bytes(data=Path(site_path).read_bytes(), mime_type=_guess_mime(site_path)),
        genai_types.Part.from_bytes(data=Path(reference_path).read_bytes(), mime_type=_guess_mime(reference_path)),
        genai_types.Part.from_text(text=prompt),
    ]
    resp = client.models.generate_content(model=model, contents=contents)
    return {
        "text": _extract_genai_text(resp),
        "channel": channel,
        "model": model,
    }


def _call_openrouter_same_image(*, channel: str, model: str, prompt: str, site_path: str | Path, reference_path: str | Path) -> dict:
    if not OPENROUTER_API_KEY:
        raise RuntimeError("OpenRouter 未配置 API key")
    from openai import OpenAI

    def _data_url(path: str | Path) -> str:
        raw = Path(path).read_bytes()
        mime = _guess_mime(path)
        encoded = base64.b64encode(raw).decode("ascii")
        return f"data:{mime};base64,{encoded}"

    client = OpenAI(api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL)
    resp = client.chat.completions.create(
        model=model,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": _data_url(site_path)}},
                {"type": "image_url", "image_url": {"url": _data_url(reference_path)}},
            ],
        }],
    )
    return {
        "text": _extract_openrouter_text(resp),
        "channel": channel,
        "model": model,
    }


def _call_same_image_model(*, channel: str, model: str, site_path: str | Path, reference_path: str | Path) -> dict:
    prompt = _build_prompt()
    if channel == "openrouter":
        return _call_openrouter_same_image(
            channel=channel,
            model=model,
            prompt=prompt,
            site_path=site_path,
            reference_path=reference_path,
        )
    return _call_genai_same_image(
        channel=channel,
        model=model,
        prompt=prompt,
        site_path=site_path,
        reference_path=reference_path,
    )


def judge_same_image(site_path: str | Path, reference_path: str | Path) -> dict:
    channel = _resolve_channel()
    model = _OPENROUTER_MODEL if channel == "openrouter" else _DEFAULT_MODEL
    try:
        payload = _call_same_image_model(
            channel=channel,
            model=model,
            site_path=site_path,
            reference_path=reference_path,
        )
        answer = _normalize_answer(str(payload.get("text") or ""))
        return {
            "status": "done",
            "answer": answer,
            "channel": channel,
            "channel_label": CHANNEL_LABELS.get(channel, channel),
            "model": model,
            "reason": "",
        }
    except Exception as exc:
        return {
            "status": "error",
            "answer": "",
            "channel": channel,
            "channel_label": CHANNEL_LABELS.get(channel, channel),
            "model": model,
            "reason": str(exc),
        }
