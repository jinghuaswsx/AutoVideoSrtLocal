"""ASR 后置 en-US 标准化步骤。

接 ASR 输出的 `utterances`（带时间戳的逐句文本，可能是任意语言），先用 Gemini Flash
检测原文语言，再按路由：
- en → 跳过（直接用原 utterances）
- zh → 跳过（保留中文路径）
- es → 走西语精修 prompt 翻译为 en-US 句级 utterances_en
- pt/fr/it/ja/nl/sv/fi → 走通用兜底 prompt
- other（白名单外） → 抛 UnsupportedSourceLanguageError

句级输出 1:1 映射回原 utterances 的 start/end 时间戳；调用方将结果写到
task["utterances_en"]，下游 alignment 入口走 utterances_en or utterances fallback。
"""
from __future__ import annotations

import json
import time
from typing import Any

from appcore import llm_client
from appcore.llm_prompt_configs import resolve_prompt_config


DETECT_SUPPORTED_LANGS: tuple[str, ...] = (
    "en", "zh", "es", "pt", "fr", "it", "ja", "nl", "sv", "fi",
)

LOW_CONFIDENCE_THRESHOLD: float = 0.6

LANG_LABELS: dict[str, str] = {
    "en": "英语",
    "zh": "中文",
    "es": "西班牙语",
    "pt": "葡萄牙语",
    "fr": "法语",
    "it": "意大利语",
    "ja": "日语",
    "nl": "荷兰语",
    "sv": "瑞典语",
    "fi": "芬兰语",
}

_USE_CASE_BY_ROUTE: dict[str, str] = {
    "es_specialized": "asr_normalize.translate_es_to_en",
    "generic_fallback": "asr_normalize.translate_generic_to_en",
    "generic_fallback_low_confidence": "asr_normalize.translate_generic_to_en",
    "generic_fallback_mixed": "asr_normalize.translate_generic_to_en",
}

_PROMPT_SLOT_BY_ROUTE: dict[str, str] = {
    "es_specialized": "asr_normalize.translate_es_en",
    "generic_fallback": "asr_normalize.translate_generic_en",
    "generic_fallback_low_confidence": "asr_normalize.translate_generic_en",
    "generic_fallback_mixed": "asr_normalize.translate_generic_en",
}


class DetectLanguageFailedError(RuntimeError):
    """detect API 重试耗尽仍失败。"""


class UnsupportedSourceLanguageError(RuntimeError):
    """detect 出 language='other'，超出当前流水线支持范围。"""


class TranslateOutputInvalidError(RuntimeError):
    """Claude 翻译输出 schema 不合法（长度对不上 / index 缺漏 / text_en 为空）。"""


def _parse_detect_result(raw_text: str) -> dict:
    """把 LLM 的 JSON 响应解析成 dict，做基本结构校验。"""
    payload = json.loads(raw_text)
    if not isinstance(payload, dict):
        raise ValueError("detect response is not a JSON object")
    for key in ("language", "confidence", "is_mixed"):
        if key not in payload:
            raise ValueError(f"detect response missing {key!r}")
    if not isinstance(payload["language"], str):
        raise ValueError("language must be string")
    if not isinstance(payload["confidence"], (int, float)):
        raise ValueError("confidence must be number")
    if not isinstance(payload["is_mixed"], bool):
        raise ValueError("is_mixed must be boolean")
    return {
        "language": payload["language"],
        "confidence": float(payload["confidence"]),
        "is_mixed": bool(payload["is_mixed"]),
    }


def detect_language(
    full_text: str, *, task_id: str, user_id: int | None,
) -> tuple[dict, dict]:
    """检测原文语言。返回 (parsed_dict, usage_tokens)。

    parsed_dict: {"language", "confidence", "is_mixed"}
    usage_tokens: {"input_tokens", "output_tokens"} or {} on failure
    """
    system_prompt = resolve_prompt_config("asr_normalize.detect", "")["content"]
    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "detect_language_result",
            "schema": {
                "type": "object",
                "properties": {
                    "language": {
                        "type": "string",
                        "enum": list(DETECT_SUPPORTED_LANGS) + ["other"],
                    },
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "is_mixed": {"type": "boolean"},
                },
                "required": ["language", "confidence", "is_mixed"],
                "additionalProperties": False,
            },
        },
    }
    last_exc: Exception | None = None
    for attempt in range(2):
        try:
            result = llm_client.invoke_chat(
                "asr_normalize.detect_language",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": full_text[:4000]},
                ],
                user_id=user_id, project_id=task_id,
                temperature=0.0,
                response_format=response_format,
            )
            parsed = _parse_detect_result(result["text"])
            usage = result.get("usage") or {"input_tokens": None, "output_tokens": None}
            return parsed, usage
        except Exception as exc:
            last_exc = exc
            if attempt == 0:
                time.sleep(2)
                continue
    raise DetectLanguageFailedError(
        f"detect_language failed after 2 attempts: {last_exc}"
    )


def translate_to_en(
    utterances: list[dict],
    detected_language: str,
    *,
    route: str,
    task_id: str,
    user_id: int | None,
) -> tuple[list[dict], dict]:
    """把 utterances 整体翻译为 en-US 句级。返回 (utterances_en, usage_tokens)。

    utterances_en 结构同 utterances（含 index/start/end/text），text 字段为英文。
    """
    if route not in _USE_CASE_BY_ROUTE:
        raise ValueError(f"translate_to_en got unsupported route: {route!r}")

    use_case_code = _USE_CASE_BY_ROUTE[route]
    prompt_slot = _PROMPT_SLOT_BY_ROUTE[route]
    system_prompt = resolve_prompt_config(prompt_slot, "")["content"]

    full_text = " ".join(u["text"] for u in utterances)
    user_payload = {
        "source_language": detected_language,
        "is_mixed": route == "generic_fallback_mixed",
        "low_confidence": route == "generic_fallback_low_confidence",
        "full_text": full_text,
        "utterances": [{"index": i, "text": u["text"]} for i, u in enumerate(utterances)],
    }

    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "asr_normalize_translate_result",
            "schema": {
                "type": "object",
                "properties": {
                    "utterances_en": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "index": {"type": "integer"},
                                "text_en": {"type": "string", "minLength": 1},
                            },
                            "required": ["index", "text_en"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["utterances_en"],
                "additionalProperties": False,
            },
        },
    }

    result = llm_client.invoke_chat(
        use_case_code,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ],
        user_id=user_id, project_id=task_id,
        temperature=0.2,
        response_format=response_format,
    )

    payload = json.loads(result["text"])
    items = payload["utterances_en"]

    if len(items) != len(utterances):
        raise TranslateOutputInvalidError(
            f"length mismatch: input={len(utterances)} output={len(items)}",
        )
    by_index = {item["index"]: item["text_en"] for item in items}
    if set(by_index.keys()) != set(range(len(utterances))):
        missing = set(range(len(utterances))) - set(by_index.keys())
        raise TranslateOutputInvalidError(
            f"index coverage mismatch: missing {missing}",
        )

    utterances_en = [
        {
            "index": i,
            "start": utterances[i].get("start", utterances[i].get("start_time")),
            "end": utterances[i].get("end", utterances[i].get("end_time")),
            "text": by_index[i],
        }
        for i in range(len(utterances))
    ]
    usage = result.get("usage") or {"input_tokens": None, "output_tokens": None}
    return utterances_en, usage


def run_asr_normalize(
    *,
    task_id: str,
    user_id: int | None,
    utterances: list[dict],
) -> dict:
    """主入口。封装 detect → 路由 → translate → artifact 构建。

    成功路径返回 artifact dict（含内部字段 _utterances_en，由 runner 拿走后写到
    task["utterances_en"]，然后从 artifact 删掉再 set_artifact）。
    失败路径直接抛异常（DetectLanguageFailedError / UnsupportedSourceLanguageError /
    TranslateOutputInvalidError 或 translate_to_en 内的 LLM 异常）。
    """
    t0 = time.monotonic()
    full_text = " ".join(u["text"] for u in utterances)

    detect_result, detect_tokens = detect_language(
        full_text, task_id=task_id, user_id=user_id,
    )
    lang = detect_result["language"]
    conf = detect_result["confidence"]
    is_mixed = detect_result["is_mixed"]

    # === Same-language ASR purification (multi keeps utterances_en mid-step) ===
    purify_artifact: dict[str, Any] = {"performed": False}
    if lang in {"zh", "en", "es", "pt", "fr", "it", "ja", "de", "nl", "sv", "fi"}:
        from pipeline import asr_clean as _asr_clean
        purify_result = _asr_clean.purify_utterances(
            utterances, language=lang, task_id=task_id, user_id=user_id,
        )
        purify_artifact = {
            "performed": True,
            "language": lang,
            "cleaned": purify_result["cleaned"],
            "fallback_used": purify_result["fallback_used"],
            "model_used": purify_result["model_used"],
            "validation_errors": purify_result["validation_errors"],
        }
        if purify_result["cleaned"]:
            utterances = purify_result["utterances"]
    # ===========================================================================

    if lang == "other":
        raise UnsupportedSourceLanguageError(
            f"原视频语言检测为「other」（confidence={conf:.2f}），"
            f"当前流水线仅支持中文/英文/西班牙语/葡萄牙语/法语/意大利语/日语/荷兰语/瑞典语/芬兰语。"
            f"请使用支持的语言素材重建项目。"
        )

    if lang == "en":
        route = "en_skip"
    elif lang == "zh":
        route = "zh_skip"
    elif is_mixed:
        route = "generic_fallback_mixed"
    elif conf < LOW_CONFIDENCE_THRESHOLD:
        route = "generic_fallback_low_confidence"
    elif lang == "es":
        route = "es_specialized"
    else:
        route = "generic_fallback"

    utterances_en: list[dict] | None = None
    translate_tokens: dict = {}
    if route not in ("en_skip", "zh_skip"):
        utterances_en, translate_tokens = translate_to_en(
            utterances, detected_language=lang, route=route,
            task_id=task_id, user_id=user_id,
        )

    artifact: dict[str, Any] = {
        "detected_source_language": lang,
        "confidence": conf,
        "is_mixed": is_mixed,
        "route": route,
        "detection_source": "llm",
        "input": {
            "language_label": LANG_LABELS.get(lang, lang),
            "full_text_preview": full_text[:200],
            "utterance_count": len(utterances),
        },
        "output": {
            "full_text_preview": (
                " ".join(u["text"] for u in utterances_en)[:200]
                if utterances_en else full_text[:200]
            ),
            "utterance_count": len(utterances_en) if utterances_en else len(utterances),
        },
        "tokens": {"detect": detect_tokens, "translate": translate_tokens},
        "elapsed_ms": int((time.monotonic() - t0) * 1000),
        "model": {
            "detect": "gemini-3.1-flash-lite-preview",
            "translate": "anthropic/claude-sonnet-4.6" if utterances_en else None,
        },
        "asr_clean": purify_artifact,
    }
    if utterances_en:
        artifact["_utterances_en"] = utterances_en
    return artifact


_USER_SPECIFIED_ROUTES: dict[str, str] = {
    "zh": "zh_skip",
    "en": "en_skip",
    "es": "es_specialized",
    "pt": "generic_fallback",
    "fr": "generic_fallback",
    "it": "generic_fallback",
    "ja": "generic_fallback",
    "de": "generic_fallback",
    "nl": "generic_fallback",
    "sv": "generic_fallback",
    "fi": "generic_fallback",
}


def run_user_specified(
    *,
    task_id: str,
    user_id: int | None,
    utterances: list[dict],
    source_language: str,
) -> dict:
    """用户在 UI 明确指定了源语言，跳过 detect_language 直接路由 + translate。

    与 run_asr_normalize 同 artifact 形状，区别：
    - confidence 固定 1.0、is_mixed 固定 False
    - detection_source="user_specified"
    - tokens.detect 为空
    - model.detect 为 None

    支持 source_language ∈ {zh, en, es, pt}；其余抛 ValueError。
    """
    if source_language not in _USER_SPECIFIED_ROUTES:
        raise ValueError(
            f"run_user_specified: source_language must be one of "
            f"{list(_USER_SPECIFIED_ROUTES)}, got {source_language!r}",
        )

    t0 = time.monotonic()
    full_text = " ".join(u["text"] for u in utterances)
    route = _USER_SPECIFIED_ROUTES[source_language]

    # === Same-language ASR purification ===
    purify_artifact: dict[str, Any] = {"performed": False}
    if source_language in {"zh", "en", "es", "pt", "fr", "it", "ja", "de", "nl", "sv", "fi"}:
        from pipeline import asr_clean as _asr_clean
        purify_result = _asr_clean.purify_utterances(
            utterances, language=source_language, task_id=task_id, user_id=user_id,
        )
        purify_artifact = {
            "performed": True,
            "language": source_language,
            "cleaned": purify_result["cleaned"],
            "fallback_used": purify_result["fallback_used"],
            "model_used": purify_result["model_used"],
            "validation_errors": purify_result["validation_errors"],
        }
        if purify_result["cleaned"]:
            utterances = purify_result["utterances"]
    # ======================================

    utterances_en: list[dict] | None = None
    translate_tokens: dict = {}
    if route not in ("en_skip", "zh_skip"):
        utterances_en, translate_tokens = translate_to_en(
            utterances, detected_language=source_language, route=route,
            task_id=task_id, user_id=user_id,
        )

    artifact: dict[str, Any] = {
        "detected_source_language": source_language,
        "confidence": 1.0,
        "is_mixed": False,
        "route": route,
        "detection_source": "user_specified",
        "input": {
            "language_label": LANG_LABELS.get(source_language, source_language),
            "full_text_preview": full_text[:200],
            "utterance_count": len(utterances),
        },
        "output": {
            "full_text_preview": (
                " ".join(u["text"] for u in utterances_en)[:200]
                if utterances_en else full_text[:200]
            ),
            "utterance_count": len(utterances_en) if utterances_en else len(utterances),
        },
        "tokens": {"detect": {}, "translate": translate_tokens},
        "elapsed_ms": int((time.monotonic() - t0) * 1000),
        "model": {
            "detect": None,
            "translate": "anthropic/claude-sonnet-4.6" if utterances_en else None,
        },
        "asr_clean": purify_artifact,
    }
    if utterances_en:
        artifact["_utterances_en"] = utterances_en
    return artifact
