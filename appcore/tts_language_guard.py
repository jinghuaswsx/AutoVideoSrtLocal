"""Gemini-backed guard for verifying the final TTS copy language."""
from __future__ import annotations

from typing import Any

from appcore import llm_client


LANGUAGE_NAMES = {
    "en": "English",
    "de": "German",
    "fr": "French",
    "es": "Spanish",
    "it": "Italian",
    "pt": "Portuguese",
    "ja": "Japanese",
    "nl": "Dutch",
    "sv": "Swedish",
    "fi": "Finnish",
    "zh": "Chinese",
}


class TtsLanguageValidationError(RuntimeError):
    """Raised when Gemini says the generated TTS copy is not in target language."""

    def __init__(self, message: str, *, result: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.result = result or {}


def language_display_name(code: str | None) -> str:
    normalized = (code or "").strip().lower()
    return LANGUAGE_NAMES.get(normalized, normalized or "target language")


def extract_tts_script_text(tts_script: dict | None) -> str:
    script = tts_script or {}
    full_text = str(script.get("full_text") or "").strip()
    if full_text:
        return full_text
    blocks = script.get("blocks") or []
    return " ".join(str(block.get("text") or "").strip() for block in blocks).strip()


def build_tts_language_check_messages(text: str, target_language: str) -> list[dict]:
    target_name = language_display_name(target_language)
    system = (
        "你是一个严格的 TTS 文案语种校验器。\n"
        f"判断用户给出的文案主体是否是 {target_name}。\n"
        "品牌名、产品名、URL、数字、单位和极短外语引用可以忽略。\n"
        "只返回一个字：是 或 否。不要解释，不要标点，不要 JSON。"
    )
    user_content = (
        f"目标语种代码：{(target_language or '').strip().lower()}\n"
        f"目标语种名称：{target_name}\n"
        "TTS 文案：\n"
        f"{text}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user_content},
    ]


def _normalize_result(answer: str, target_language: str) -> dict:
    normalized = (answer or "").strip()
    first_char = normalized[:1]
    target_name = language_display_name(target_language)
    is_target_language = first_char == "是"
    return {
        "is_target_language": is_target_language,
        "answer": first_char or normalized,
        "target_language": target_name,
        "detected_language": "target" if is_target_language else "not_target",
        "confidence": 1.0 if first_char in {"是", "否"} else 0.0,
        "reason": f"Gemini returned {first_char or normalized!r}.",
        "problem_excerpt": "",
    }


def _error_message(result: dict) -> str:
    return (
        "TTS language check failed: "
        f"target={result.get('target_language') or 'unknown'}, "
        f"detected={result.get('detected_language') or 'unknown'}, "
        f"answer={result.get('answer') or ''}. "
        f"{result.get('reason') or ''}"
    ).strip()


def validate_tts_script_language_or_raise(
    *,
    text: str,
    target_language: str,
    user_id: int | None,
    project_id: str,
    variant: str,
    round_index: int,
) -> dict:
    """Validate TTS copy language and raise before video composition on mismatch."""
    clean_text = (text or "").strip()
    target_name = language_display_name(target_language)
    if not clean_text:
        result = {
            "is_target_language": False,
            "answer": "否",
            "target_language": target_name,
            "detected_language": "empty",
            "confidence": 1.0,
            "reason": "TTS copy is empty, so it cannot be verified as the target language.",
            "problem_excerpt": "",
        }
        raise TtsLanguageValidationError(_error_message(result), result=result)

    try:
        response = llm_client.invoke_chat(
            "video_translate.tts_language_check",
            messages=build_tts_language_check_messages(clean_text, target_language),
            user_id=user_id,
            project_id=project_id,
            temperature=0,
            max_tokens=8,
            provider_override="openrouter",
            model_override="google/gemini-3.1-flash-lite-preview",
            billing_extra={"variant": variant, "round": round_index},
        )
        result = _normalize_result(str(response.get("text") or ""), target_language)
    except TtsLanguageValidationError:
        raise
    except Exception as exc:
        result = {
            "is_target_language": False,
            "answer": "否",
            "target_language": target_name,
            "detected_language": "unknown",
            "confidence": 0.0,
            "reason": f"Gemini language check failed: {exc}",
            "problem_excerpt": clean_text[:160],
        }
        raise TtsLanguageValidationError(_error_message(result), result=result) from exc

    if result["is_target_language"] is not True:
        raise TtsLanguageValidationError(_error_message(result), result=result)
    return result
