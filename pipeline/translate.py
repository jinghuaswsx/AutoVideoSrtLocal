import json
import logging
from typing import Dict, List

from openai import OpenAI

log = logging.getLogger(__name__)

from appcore.llm_provider_configs import (
    ProviderConfigError,
    require_provider_config,
)
from config import (
    DOUBAO_LLM_BASE_URL_DEFAULT,
    OPENROUTER_BASE_URL_DEFAULT,
)

# 默认 model_id 作为 DB 行为空时的兜底
_DEFAULT_CLAUDE_MODEL = "anthropic/claude-sonnet-4-5"
_DEFAULT_DOUBAO_MODEL = "doubao-seed-2-0-pro-260215"
from pipeline.localization import (
    LOCALIZED_TRANSLATION_RESPONSE_FORMAT,
    TTS_SCRIPT_RESPONSE_FORMAT,
    _split_segments_into_batches,
    build_localized_translation_messages,
    build_tts_script_messages,
    validate_localized_translation,
    validate_tts_script,
)


# 走 OpenRouter 的 provider 值 → 具体模型 ID
_OPENROUTER_PREF_MODELS = {
    "gemini_31_flash":  "google/gemini-3.1-flash-lite-preview",
    "gemini_31_pro":    "google/gemini-3.1-pro-preview",
    "gemini_3_flash":   "google/gemini-3-flash-preview",
    "gpt_5_mini":       "openai/gpt-5-mini",
    "gpt_5_5":          "openai/gpt-5.5",
    "claude_sonnet":    "anthropic/claude-sonnet-4.6",
    "openrouter":       "anthropic/claude-sonnet-4.6",  # legacy 值回落 claude
}

# 走 Vertex AI（Google Cloud Express Mode）的 provider 值 → Gemini model ID
_VERTEX_PREF_MODELS = {
    "vertex_gemini_31_flash_lite": "gemini-3.1-flash-lite-preview",
    "vertex_gemini_3_flash":       "gemini-3-flash-preview",
    "vertex_gemini_31_pro":        "gemini-3.1-pro-preview",
}


# ---------------------------------------------------------------------------
# use_case code 前置解析（对接 appcore.llm_bindings）
# ---------------------------------------------------------------------------

def _binding_lookup_for_use_case(code: str) -> dict | None:
    """如果入参看起来像 use_case code（含 '.'），查 bindings 表；否则 None。

    返回 {provider, model, extra, source} 或 None。
    """
    if not isinstance(code, str) or "." not in code:
        return None
    try:
        from appcore import llm_bindings
        return llm_bindings.resolve(code)
    except KeyError:
        return None


def _resolve_use_case_provider(provider_arg: str) -> str:
    """入口映射：use_case code → 老式 provider 字符串（保留业务函数 vertex_* 分流不变）。

    映射规则：
      gemini_vertex + 模型命中 _VERTEX_PREF_MODELS 反向表 → 返 vertex_*
      gemini_vertex + 未命中 → 写入 _VERTEX_PREF_MODELS["vertex_custom"] 并返 "vertex_custom"
      gemini_aistudio → translate.py 无此分支；best-effort 走 OpenRouter 的 google/<model>
      openrouter / doubao → 原样返回
    """
    binding = _binding_lookup_for_use_case(provider_arg)
    if not binding:
        return provider_arg

    p = binding["provider"]
    m = binding["model"]
    if p == "gemini_vertex":
        reverse = {
            v: k for k, v in _VERTEX_PREF_MODELS.items()
            if k.startswith("vertex_") and not k.startswith("vertex_adc_")
        }
        if m in reverse:
            return reverse[m]
        _VERTEX_PREF_MODELS["vertex_custom"] = m
        return "vertex_custom"
    if p == "gemini_vertex_adc":
        reverse = {
            v: "vertex_adc_" + k[len("vertex_"):]
            for k, v in _VERTEX_PREF_MODELS.items()
            if k.startswith("vertex_") and not k.startswith("vertex_adc_")
        }
        if m in reverse:
            return reverse[m]
        _VERTEX_PREF_MODELS["vertex_adc_custom"] = m
        return "vertex_adc_custom"
    if p == "gemini_aistudio":
        # translate.py 内没有 AIStudio 分支；回退到 OpenRouter 并补 google/ 前缀
        model_id = m if m.startswith("google/") else f"google/{m}"
        _OPENROUTER_PREF_MODELS["_gemini_aistudio_fallback"] = model_id
        return "_gemini_aistudio_fallback"
    # openrouter / doubao 原样
    return p


def resolve_provider_config(
    provider: str,
    user_id: int | None = None,
    api_key_override: str | None = None,
) -> tuple[OpenAI, str]:
    """Return (client, model_id) for the given provider (OpenAI-compatible only).

    Vertex provider 不走这里——由 _call_vertex_json 单独处理。
    """
    from appcore.api_keys import resolve_extra, resolve_key

    if provider == "doubao":
        try:
            cfg = require_provider_config("doubao_llm")
            key = api_key_override or cfg.require_api_key()
            base_url = cfg.require_base_url(default=DOUBAO_LLM_BASE_URL_DEFAULT)
        except ProviderConfigError as exc:
            raise RuntimeError(str(exc)) from exc
        extra = resolve_extra(user_id, "doubao_llm") if user_id else {}
        model = extra.get("model_id") or cfg.model_id or _DEFAULT_DOUBAO_MODEL
    else:
        # 非 doubao 统一走 openrouter；根据 provider 字符串选模型
        try:
            cfg = require_provider_config("openrouter_text")
            key = api_key_override or cfg.require_api_key()
            base_url = cfg.require_base_url(default=OPENROUTER_BASE_URL_DEFAULT)
        except ProviderConfigError as exc:
            raise RuntimeError(str(exc)) from exc
        extra = resolve_extra(user_id, "openrouter") if user_id else {}
        # 优先级：用户在 OpenRouter 设置里显式 override 的 model_id > provider 映射 > legacy 默认
        user_override = (extra.get("model_id") or cfg.model_id or "").strip()
        if user_override:
            model = user_override
        else:
            model = _OPENROUTER_PREF_MODELS.get(provider, _DEFAULT_CLAUDE_MODEL)

    return OpenAI(api_key=key, base_url=base_url), model


def get_model_display_name(provider: str, user_id: int | None = None) -> str:
    """Return the model ID string for logging/display."""
    if provider.startswith("vertex_"):
        return _vertex_model_id(provider)
    _, model = resolve_provider_config(provider, user_id)
    return model


# Vertex JSON helper 与公共 messages/schema 工具迁到
# `appcore.llm_providers._helpers.vertex_json`，本模块以 re-export 保留
# 历史 import 路径，方便老调用方/测试 patch。
from appcore.llm_providers._helpers.vertex_json import (  # noqa: F401
    _GEMINI_VERTEX_UNSUPPORTED_SCHEMA_KEYS,
    _call_vertex_json,
    _extract_gemini_schema,
    _split_oai_messages,
    _strip_unsupported_schema,
    parse_json_content,
)


# ---------------------------------------------------------------------------
# use_case 入口 —— 直接走 appcore.llm_client.invoke_chat
# ---------------------------------------------------------------------------

def _invoke_chat_for_use_case(
    use_case: str,
    messages: list[dict],
    response_format: dict | None,
    *,
    user_id: int | None,
    project_id: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
):
    """跑一次 use_case 走的 invoke_chat，返回 (payload, usage)。

    把不同 adapter（openrouter/doubao/gemini_vertex/...）的返回值统一为
    业务函数期望的 (parsed_payload_dict_or_list, usage_dict_or_None) 二元组。
    """
    from appcore import llm_client

    result = llm_client.invoke_chat(
        use_case,
        messages=messages,
        user_id=user_id,
        project_id=project_id,
        response_format=response_format,
        temperature=temperature if temperature is not None else 0.2,
        max_tokens=max_tokens if max_tokens is not None else 4096,
    )

    payload = result.get("json")
    if payload is None:
        text = result.get("text") or ""
        payload = parse_json_content(text)

    raw_usage = result.get("usage") or {}
    usage = None
    if raw_usage:
        input_tokens = raw_usage.get("input_tokens")
        output_tokens = raw_usage.get("output_tokens")
        if input_tokens is not None or output_tokens is not None:
            usage = {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            }
    return payload, usage


def _vertex_model_id(provider: str) -> str:
    if provider.startswith("vertex_adc_"):
        legacy_provider = "vertex_" + provider[len("vertex_adc_"):]
        return _VERTEX_PREF_MODELS.get(
            provider,
            _VERTEX_PREF_MODELS.get(legacy_provider, "gemini-3.1-flash-lite-preview"),
        )
    return _VERTEX_PREF_MODELS.get(provider, "gemini-3.1-flash-lite-preview")


def _vertex_provider_config_code(provider: str) -> str:
    if provider.startswith("vertex_adc_"):
        return "gemini_vertex_adc_text"
    return "gemini_cloud_text"


# ---------------------------------------------------------------------------
# OpenAI-兼容分支（OpenRouter / 豆包）
# ---------------------------------------------------------------------------

def _call_openai_compat(
    messages: list[dict],
    *,
    provider: str,
    user_id: int | None,
    api_key_override: str | None,
    response_format: dict | None,
    temperature: float = 0.2,
    max_tokens: int = 4096,
):
    """走 OpenAI 兼容接口返回 (parsed_payload, usage_dict, raw_text, model_id)。"""
    client, model = resolve_provider_config(provider, user_id, api_key_override=api_key_override)
    extra_body: dict = {}
    if provider != "doubao" and response_format is not None:
        extra_body["response_format"] = response_format
    if provider == "openrouter" or provider in _OPENROUTER_PREF_MODELS:
        # 非 doubao 都走 OpenRouter，启用 response-healing 让 JSON 更稳
        if provider != "doubao":
            extra_body["plugins"] = [{"id": "response-healing"}]

    response = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        **({"extra_body": extra_body} if extra_body else {}),
    )
    raw_content = response.choices[0].message.content
    log.info("openai-compat raw response (provider=%s, model=%s): %s",
             provider, model, (raw_content or "")[:2000])
    payload = parse_json_content(raw_content)
    usage_obj = getattr(response, "usage", None)
    usage = None
    if usage_obj is not None:
        usage = {
            "input_tokens": getattr(usage_obj, "prompt_tokens", None),
            "output_tokens": getattr(usage_obj, "completion_tokens", None),
        }
    return payload, usage, raw_content, model


# ---------------------------------------------------------------------------
# 对外业务函数
# ---------------------------------------------------------------------------

def _normalize_batch_source_indices(sentences: list[dict], batch_indices: list[int]) -> None:
    """LLM 在批量 prompt 下常常用 0-based 相对索引而非给定的全局索引；
    把所有 source_segment_indices 平移到全局 batch_indices 范围。in-place 修改。"""
    if not sentences or not batch_indices:
        return
    batch_set = set(batch_indices)
    relative_set = set(range(len(batch_indices)))
    seen: list[int] = []
    for s in sentences:
        for i in s.get("source_segment_indices") or []:
            try:
                seen.append(int(i))
            except (TypeError, ValueError):
                pass
    if not seen:
        return
    if all(i in batch_set for i in seen):
        return  # already global
    if all(i in relative_set for i in seen):
        for s in sentences:
            shifted = []
            for i in s.get("source_segment_indices") or []:
                try:
                    j = int(i)
                except (TypeError, ValueError):
                    continue
                if 0 <= j < len(batch_indices):
                    shifted.append(batch_indices[j])
            s["source_segment_indices"] = sorted(set(shifted))
        return
    # mixed: keep globals as-is, shift relatives
    for s in sentences:
        normalized: set[int] = set()
        for i in s.get("source_segment_indices") or []:
            try:
                j = int(i)
            except (TypeError, ValueError):
                continue
            if j in batch_set:
                normalized.add(j)
            elif 0 <= j < len(batch_indices):
                normalized.add(batch_indices[j])
        s["source_segment_indices"] = sorted(normalized)


def _generate_localized_translation_single(
    source_full_text_zh: str,
    script_segments: list[dict],
    variant: str = "normal",
    custom_system_prompt: str | None = None,
    *,
    provider: str = "openrouter",
    user_id: int | None = None,
    openrouter_api_key: str | None = None,
    use_case: str | None = None,
    project_id: str | None = None,
) -> dict:
    """Single-shot translation: original logic, no batching. Used directly for
    short videos and as the per-batch primitive for long-video batching."""
    messages = build_localized_translation_messages(
        source_full_text_zh,
        script_segments,
        variant=variant,
        custom_system_prompt=custom_system_prompt,
    )

    if use_case:
        payload, usage = _invoke_chat_for_use_case(
            use_case, messages, LOCALIZED_TRANSLATION_RESPONSE_FORMAT,
            user_id=user_id, project_id=project_id,
        )
    else:
        provider = _resolve_use_case_provider(provider)
        if provider.startswith("vertex_"):
            payload, usage, _ = _call_vertex_json(
                messages, _vertex_model_id(provider), LOCALIZED_TRANSLATION_RESPONSE_FORMAT,
                provider_config_code=_vertex_provider_config_code(provider),
            )
        else:
            payload, usage, _, _ = _call_openai_compat(
                messages, provider=provider, user_id=user_id,
                api_key_override=openrouter_api_key,
                response_format=LOCALIZED_TRANSLATION_RESPONSE_FORMAT,
            )

    log.info("localized_translation parsed payload type=%s keys=%s",
             type(payload).__name__,
             list(payload.keys()) if isinstance(payload, dict) else f"list[{len(payload)}]")
    result = validate_localized_translation(payload)
    if usage:
        result["_usage"] = usage
        log.info("localized_translation token usage: input=%s, output=%s",
                 usage["input_tokens"], usage["output_tokens"])
    # 把实际发送给 LLM 的 messages 也回传，方便调用方落盘供 UI 审计
    result["_messages"] = messages
    return result


def _generate_localized_translation_batched(
    source_full_text_zh: str,
    script_segments: list[dict],
    variant: str,
    custom_system_prompt: str | None,
    *,
    provider: str,
    user_id: int | None,
    openrouter_api_key: str | None,
    batch_size: int,
    use_case: str | None = None,
    project_id: str | None = None,
) -> dict:
    """Long-video translation: split source segments into ~batch_size batches,
    call _single per batch, normalize per-batch indices to global, then merge.
    Each batch sees a small prompt so Claude/Gemini reliably return all the
    nested schema fields (source_segment_indices etc.)."""
    batches = _split_segments_into_batches(script_segments, target_size=batch_size)
    log.info("localized_translation batched: %d segments → %d batches (size~%d)",
             len(script_segments), len(batches), batch_size)

    all_sentences: list[dict] = []
    all_messages: list = []
    total_input = 0
    total_output = 0
    for batch_idx, batch in enumerate(batches):
        log.info("localized_translation batch %d/%d (n=%d)",
                 batch_idx + 1, len(batches), len(batch))
        batch_source_text = "\n".join(
            (s.get("text") or "").strip() for s in batch if (s.get("text") or "").strip()
        )
        batch_result = _generate_localized_translation_single(
            batch_source_text, batch,
            variant=variant, custom_system_prompt=custom_system_prompt,
            provider=provider, user_id=user_id,
            openrouter_api_key=openrouter_api_key,
            use_case=use_case, project_id=project_id,
        )
        batch_indices = [int(s["index"]) for s in batch]
        _normalize_batch_source_indices(batch_result.get("sentences") or [], batch_indices)
        all_sentences.extend(batch_result.get("sentences") or [])
        msgs = batch_result.get("_messages")
        if msgs:
            all_messages.extend(msgs if isinstance(msgs, list) else [msgs])
        usage = batch_result.get("_usage") or {}
        total_input += int(usage.get("input_tokens") or 0)
        total_output += int(usage.get("output_tokens") or 0)

    for i, s in enumerate(all_sentences):
        s["index"] = i

    full_text = " ".join((s.get("text") or "").strip()
                          for s in all_sentences if (s.get("text") or "").strip())
    final = validate_localized_translation({"full_text": full_text, "sentences": all_sentences})
    if all_messages:
        final["_messages"] = all_messages
    if total_input or total_output:
        final["_usage"] = {"input_tokens": total_input, "output_tokens": total_output}
    return final


def generate_localized_translation(
    source_full_text_zh: str,
    script_segments: list[dict],
    variant: str = "normal",
    custom_system_prompt: str | None = None,
    *,
    provider: str = "openrouter",
    user_id: int | None = None,
    openrouter_api_key: str | None = None,
    use_case: str | None = None,
    project_id: str | None = None,
) -> dict:
    """Public entry: dispatches to single-shot for short videos and to the
    batched path for long videos based on config thresholds. Long-prompt LLM
    calls are the root cause of intermittent missing-field failures across
    Claude/Gemini; batching keeps each call's prompt size small.

    传 use_case 时直接走 appcore.llm_client.invoke_chat（adapter 解析 binding），
    不再走 provider 字符串映射；老 provider= 入参仍兼容。优先级：use_case > provider。
    """
    import config as _cfg
    if (
        getattr(_cfg, "MULTI_TRANSLATE_BATCH_ENABLED", True)
        and len(script_segments) > getattr(_cfg, "MULTI_TRANSLATE_BATCH_THRESHOLD", 18)
    ):
        return _generate_localized_translation_batched(
            source_full_text_zh, script_segments,
            variant=variant, custom_system_prompt=custom_system_prompt,
            provider=provider, user_id=user_id,
            openrouter_api_key=openrouter_api_key,
            batch_size=getattr(_cfg, "MULTI_TRANSLATE_BATCH_SIZE", 12),
            use_case=use_case, project_id=project_id,
        )
    return _generate_localized_translation_single(
        source_full_text_zh, script_segments,
        variant=variant, custom_system_prompt=custom_system_prompt,
        provider=provider, user_id=user_id,
        openrouter_api_key=openrouter_api_key,
        use_case=use_case, project_id=project_id,
    )


def _generate_tts_script_single(
    localized_translation: dict,
    *,
    provider: str = "openrouter",
    user_id: int | None = None,
    openrouter_api_key: str | None = None,
    messages_builder=None,
    response_format_override=None,
    validator=None,
    use_case: str | None = None,
    project_id: str | None = None,
) -> dict:
    """Single-shot tts_script generation: original logic, no batching."""
    builder = messages_builder or build_tts_script_messages
    messages = builder(localized_translation)
    rf = response_format_override or TTS_SCRIPT_RESPONSE_FORMAT

    if use_case:
        payload, usage = _invoke_chat_for_use_case(
            use_case, messages, rf,
            user_id=user_id, project_id=project_id,
        )
    else:
        provider = _resolve_use_case_provider(provider)
        if provider.startswith("vertex_"):
            payload, usage, _ = _call_vertex_json(
                messages, _vertex_model_id(provider), rf,
                provider_config_code=_vertex_provider_config_code(provider),
            )
        else:
            payload, usage, _, _ = _call_openai_compat(
                messages, provider=provider, user_id=user_id,
                api_key_override=openrouter_api_key,
                response_format=rf,
            )

    log.info("tts_script parsed payload type=%s keys=%s",
             type(payload).__name__,
             list(payload.keys()) if isinstance(payload, dict) else f"list[{len(payload)}]")
    validate_fn = validator or validate_tts_script
    sentences = (localized_translation or {}).get("sentences") or []
    try:
        result = validate_fn(payload, sentences=sentences)
    except TypeError:
        # Custom validators (test injection / language overrides) may not accept the
        # sentences kwarg yet. Fall back to the legacy single-arg call.
        result = validate_fn(payload)
    if usage:
        result["_usage"] = usage
        log.info("tts_script token usage: input=%s, output=%s",
                 usage["input_tokens"], usage["output_tokens"])
    result["_messages"] = messages
    return result


def _generate_tts_script_batched(
    localized_translation: dict,
    *,
    provider: str,
    user_id: int | None,
    openrouter_api_key: str | None,
    messages_builder,
    response_format_override,
    validator,
    batch_size: int,
    use_case: str | None = None,
    project_id: str | None = None,
) -> dict:
    """Long-translation tts_script: split sentences into ~batch_size batches,
    generate per-batch blocks/subtitle_chunks, merge, then run a single
    validate_tts_script(sentences=...) so derive recomputes all nested
    indices coherently against the full sentence list."""
    sentences = localized_translation.get("sentences") or []
    sentence_batches = _split_segments_into_batches(sentences, target_size=batch_size)
    log.info("tts_script batched: %d sentences → %d batches",
             len(sentences), len(sentence_batches))

    all_blocks: list[dict] = []
    all_chunks: list[dict] = []
    all_messages: list = []
    total_input = 0
    total_output = 0
    for batch_idx, batch in enumerate(sentence_batches):
        log.info("tts_script batch %d/%d (n=%d)",
                 batch_idx + 1, len(sentence_batches), len(batch))
        sub_localized = {
            "full_text": " ".join((s.get("text") or "") for s in batch),
            "sentences": batch,
        }
        batch_result = _generate_tts_script_single(
            sub_localized,
            provider=provider, user_id=user_id,
            openrouter_api_key=openrouter_api_key,
            messages_builder=messages_builder,
            response_format_override=response_format_override,
            validator=validator,
            use_case=use_case, project_id=project_id,
        )
        all_blocks.extend(batch_result.get("blocks") or [])
        all_chunks.extend(batch_result.get("subtitle_chunks") or [])
        msgs = batch_result.get("_messages")
        if msgs:
            all_messages.extend(msgs if isinstance(msgs, list) else [msgs])
        usage = batch_result.get("_usage") or {}
        total_input += int(usage.get("input_tokens") or 0)
        total_output += int(usage.get("output_tokens") or 0)

    for i, b in enumerate(all_blocks):
        b["index"] = i
    for i, c in enumerate(all_chunks):
        c["index"] = i
    full_text = " ".join(
        (b.get("text") or "").strip()
        for b in all_blocks if (b.get("text") or "").strip()
    )
    merged = {"full_text": full_text, "blocks": all_blocks, "subtitle_chunks": all_chunks}
    validate_fn = validator or validate_tts_script
    try:
        final = validate_fn(merged, sentences=sentences)
    except TypeError:
        final = validate_fn(merged)
    if all_messages:
        final["_messages"] = all_messages
    if total_input or total_output:
        final["_usage"] = {"input_tokens": total_input, "output_tokens": total_output}
    return final


def generate_tts_script(
    localized_translation: dict,
    *,
    provider: str = "openrouter",
    user_id: int | None = None,
    openrouter_api_key: str | None = None,
    messages_builder=None,
    response_format_override=None,
    validator=None,
    use_case: str | None = None,
    project_id: str | None = None,
) -> dict:
    """Public entry. Long sentences trigger batched generation; per-batch
    blocks/subtitle_chunks are merged and the full sentence list drives a
    single validate_tts_script(sentences=...) so derive can recompute all
    nested indices coherently.

    传 use_case 时走 invoke_chat；老 provider= 入参兼容。
    """
    import config as _cfg
    sentences = (localized_translation or {}).get("sentences") or []
    if (
        getattr(_cfg, "MULTI_TRANSLATE_BATCH_ENABLED", True)
        and len(sentences) > getattr(_cfg, "MULTI_TRANSLATE_BATCH_THRESHOLD", 18)
    ):
        return _generate_tts_script_batched(
            localized_translation,
            provider=provider, user_id=user_id,
            openrouter_api_key=openrouter_api_key,
            messages_builder=messages_builder,
            response_format_override=response_format_override,
            validator=validator,
            batch_size=getattr(_cfg, "MULTI_TRANSLATE_BATCH_SIZE", 12),
            use_case=use_case, project_id=project_id,
        )
    return _generate_tts_script_single(
        localized_translation,
        provider=provider, user_id=user_id,
        openrouter_api_key=openrouter_api_key,
        messages_builder=messages_builder,
        response_format_override=response_format_override,
        validator=validator,
        use_case=use_case, project_id=project_id,
    )


def _patch_missing_source_indices_from_prev(
    out_sentences,
    prev_sentences: list[dict],
) -> None:
    """Rewrite outputs sometimes drop source_segment_indices from individual
    sentences (long prompt, high temperature). Since rewrite never changes
    the source-segment correspondence — it only adjusts wording / word
    count — fill the missing field from the matching prev sentence
    (or the union of all prev sentences as last-resort fallback).
    Mutates out_sentences in place."""
    if not isinstance(out_sentences, list) or not out_sentences:
        return
    if not prev_sentences:
        return
    fallback = sorted({
        int(idx)
        for s in prev_sentences
        for idx in (s.get("source_segment_indices") or [])
    })
    for i, s in enumerate(out_sentences):
        if not isinstance(s, dict):
            continue
        idxs = s.get("source_segment_indices")
        if isinstance(idxs, list) and idxs:
            continue
        if i < len(prev_sentences):
            prev_idx = prev_sentences[i].get("source_segment_indices") or []
            if prev_idx:
                s["source_segment_indices"] = sorted({int(x) for x in prev_idx})
                continue
        if fallback:
            s["source_segment_indices"] = list(fallback)


def _allocate_sub_target_words(sentence_batches: list[list[dict]], total_target: int) -> list[int]:
    """Distribute the global target_words proportionally across batches by
    character count. Last batch absorbs rounding remainder so sum == total."""
    if not sentence_batches:
        return []
    char_counts = [
        sum(len((s.get("text") or "")) for s in batch)
        for batch in sentence_batches
    ]
    total_chars = sum(char_counts)
    if total_chars <= 0:
        n = len(sentence_batches)
        base = total_target // n
        remainder = total_target - base * n
        return [base + (1 if i < remainder else 0) for i in range(n)]
    sub = [max(1, round(total_target * c / total_chars)) for c in char_counts]
    diff = total_target - sum(sub)
    sub[-1] = max(1, sub[-1] + diff)
    return sub


def _generate_localized_rewrite_single(
    source_full_text: str,
    prev_localized_translation: dict,
    target_words: int,
    direction: str,
    source_language: str,
    messages_builder,
    *,
    provider: str = "openrouter",
    user_id: int | None = None,
    openrouter_api_key: str | None = None,
    temperature: float = 0.2,
    feedback_notes: str | None = None,
    use_case: str | None = None,
    project_id: str | None = None,
) -> dict:
    """Single-shot rewrite: original logic, no batching. Used directly for
    short translations and as the per-batch primitive for long ones."""
    builder_kwargs = dict(
        source_full_text=source_full_text,
        prev_localized_translation=prev_localized_translation,
        target_words=target_words,
        direction=direction,
        source_language=source_language,
    )
    if feedback_notes:
        builder_kwargs["feedback_notes"] = feedback_notes
    messages = messages_builder(**builder_kwargs)

    if use_case:
        payload, usage = _invoke_chat_for_use_case(
            use_case, messages, LOCALIZED_TRANSLATION_RESPONSE_FORMAT,
            user_id=user_id, project_id=project_id,
            temperature=temperature,
        )
    else:
        provider = _resolve_use_case_provider(provider)
        if provider.startswith("vertex_"):
            payload, usage, _ = _call_vertex_json(
                messages, _vertex_model_id(provider), LOCALIZED_TRANSLATION_RESPONSE_FORMAT,
                temperature=temperature,
                provider_config_code=_vertex_provider_config_code(provider),
            )
        else:
            payload, usage, _, _ = _call_openai_compat(
                messages, provider=provider, user_id=user_id,
                api_key_override=openrouter_api_key,
                response_format=LOCALIZED_TRANSLATION_RESPONSE_FORMAT,
                temperature=temperature,
            )

    log.info(
        "localized_rewrite parsed (provider=%s, direction=%s, target_words=%d, "
        "temperature=%.2f, feedback=%s)",
        provider, direction, target_words, temperature,
        "yes" if feedback_notes else "no",
    )
    # Rewrite 不应该改 source segment 对应关系——只是按字数重写文本。
    # 长 prompt / 高 temperature 下 LLM 偶尔漏 source_segment_indices，从
    # prev_localized_translation.sentences 按位补回（同位 → 取对应 prev sentence；
    # 兜底 → 取整批 prev sentences 的 union），避免 validate 失败炸流水线。
    _patch_missing_source_indices_from_prev(
        (payload or {}).get("sentences") if isinstance(payload, dict) else None,
        (prev_localized_translation or {}).get("sentences") or [],
    )
    result = validate_localized_translation(payload)
    if usage:
        result["_usage"] = usage
    result["_messages"] = messages
    return result


def _generate_localized_rewrite_batched(
    source_full_text: str,
    prev_localized_translation: dict,
    target_words: int,
    direction: str,
    source_language: str,
    messages_builder,
    *,
    provider: str,
    user_id: int | None,
    openrouter_api_key: str | None,
    temperature: float,
    feedback_notes: str | None,
    batch_size: int,
    use_case: str | None = None,
    project_id: str | None = None,
) -> dict:
    """Long-translation rewrite: split prev sentences into ~batch_size batches,
    allocate sub-target words proportionally by char count, rewrite each batch
    independently, then merge. Same long-prompt root-cause fix as translate."""
    sentences = prev_localized_translation.get("sentences") or []
    sentence_batches = _split_segments_into_batches(sentences, target_size=batch_size)
    sub_targets = _allocate_sub_target_words(sentence_batches, target_words)
    log.info(
        "localized_rewrite batched: %d sentences → %d batches, target=%d → %s",
        len(sentences), len(sentence_batches), target_words, sub_targets,
    )

    all_sentences: list[dict] = []
    all_messages: list = []
    total_input = 0
    total_output = 0
    for batch_idx, (batch, sub_target) in enumerate(zip(sentence_batches, sub_targets)):
        log.info(
            "localized_rewrite batch %d/%d (n=%d, sub_target=%d)",
            batch_idx + 1, len(sentence_batches), len(batch), sub_target,
        )
        sub_prev = {
            "full_text": " ".join((s.get("text") or "") for s in batch),
            "sentences": batch,
        }
        batch_result = _generate_localized_rewrite_single(
            source_full_text, sub_prev, sub_target, direction, source_language,
            messages_builder,
            provider=provider, user_id=user_id,
            openrouter_api_key=openrouter_api_key,
            temperature=temperature, feedback_notes=feedback_notes,
            use_case=use_case, project_id=project_id,
        )
        batch_global_indices = sorted({
            int(idx)
            for s in batch
            for idx in (s.get("source_segment_indices") or [])
        })
        if batch_global_indices:
            _normalize_batch_source_indices(
                batch_result.get("sentences") or [], batch_global_indices,
            )
        all_sentences.extend(batch_result.get("sentences") or [])
        msgs = batch_result.get("_messages")
        if msgs:
            all_messages.extend(msgs if isinstance(msgs, list) else [msgs])
        usage = batch_result.get("_usage") or {}
        total_input += int(usage.get("input_tokens") or 0)
        total_output += int(usage.get("output_tokens") or 0)

    for i, s in enumerate(all_sentences):
        s["index"] = i
    full_text = " ".join(
        (s.get("text") or "").strip()
        for s in all_sentences if (s.get("text") or "").strip()
    )
    final = validate_localized_translation({"full_text": full_text, "sentences": all_sentences})
    if all_messages:
        final["_messages"] = all_messages
    if total_input or total_output:
        final["_usage"] = {"input_tokens": total_input, "output_tokens": total_output}
    return final


def generate_localized_rewrite(
    source_full_text: str,
    prev_localized_translation: dict,
    target_words: int,
    direction: str,
    source_language: str,
    messages_builder,
    *,
    provider: str = "openrouter",
    user_id: int | None = None,
    openrouter_api_key: str | None = None,
    temperature: float = 0.2,
    feedback_notes: str | None = None,
    use_case: str | None = None,
    project_id: str | None = None,
) -> dict:
    """Rewrite an existing localized_translation to a target word count.

    provider 可以是 openrouter 派生值、vertex_* 或 doubao；所有路径都把实际发给
    LLM 的 messages 放在 result["_messages"] 里，供 UI/审计。

    temperature 让上层（duration loop 内部 5 次 retry）逐次升温，避免 LLM 对同一
    prompt 输出字符级一致的同一份译文。feedback_notes 让上层把"前几次 attempt
    给出了多少词、目标多少"这种闭环反馈塞进 prompt，迫使 LLM 跳出固定模板。

    长 sentences 时自动走分批 rewrite（每批分配子目标字数），避免长 prompt 下
    Claude/Gemini 漏返 source_segment_indices 等嵌套字段。

    传 use_case 时走 invoke_chat；老 provider= 入参兼容。
    """
    import config as _cfg
    sentences = prev_localized_translation.get("sentences") or []
    if (
        getattr(_cfg, "MULTI_TRANSLATE_BATCH_ENABLED", True)
        and len(sentences) > getattr(_cfg, "MULTI_TRANSLATE_BATCH_THRESHOLD", 18)
    ):
        return _generate_localized_rewrite_batched(
            source_full_text, prev_localized_translation, target_words,
            direction, source_language, messages_builder,
            provider=provider, user_id=user_id,
            openrouter_api_key=openrouter_api_key,
            temperature=temperature, feedback_notes=feedback_notes,
            batch_size=getattr(_cfg, "MULTI_TRANSLATE_BATCH_SIZE", 12),
            use_case=use_case, project_id=project_id,
        )
    return _generate_localized_rewrite_single(
        source_full_text, prev_localized_translation, target_words,
        direction, source_language, messages_builder,
        provider=provider, user_id=user_id,
        openrouter_api_key=openrouter_api_key,
        temperature=temperature, feedback_notes=feedback_notes,
        use_case=use_case, project_id=project_id,
    )
