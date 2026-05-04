"""Localized translation / TTS-script / rewrite —— 全部走
appcore.llm_client.invoke_chat（adapter 解析 binding + 调 LLM SDK）。

本模块不再创建任何 OpenAI / google.genai 客户端，也不接受老 provider= 字符串
分流（vertex_* / openrouter / doubao）。三个对外函数 use_case= 必传。

D-4 之前曾保留 `from openai import OpenAI` + `_call_openai_compat /
resolve_provider_config / _resolve_use_case_provider / _vertex_model_id /
_OPENROUTER_PREF_MODELS / _VERTEX_PREF_MODELS` 等老入口作为兼容；本次
全部删除。所有业务调用方都已传 use_case=（A-3 / B-3 / C-2 完成）。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from appcore import llm_bindings
from appcore.llm_models import (
    LEGACY_PROVIDER_MODEL_MAP,
    legacy_provider_to_model,
    legacy_provider_to_provider_code,
)
from pipeline.localization import (
    LOCALIZED_TRANSLATION_RESPONSE_FORMAT,
    TTS_SCRIPT_RESPONSE_FORMAT,
    _split_segments_into_batches,
    build_localized_translation_messages,
    build_tts_script_messages,
    validate_localized_translation,
    validate_tts_script,
)

# Vertex JSON helper / parse_json_content / messages-schema 工具仍保留 re-export，
# 让历史 `from pipeline.translate import parse_json_content` 调用方继续 work。
from appcore.llm_providers._helpers.vertex_json import (  # noqa: F401
    _GEMINI_VERTEX_UNSUPPORTED_SCHEMA_KEYS,
    _call_vertex_json,
    _extract_gemini_schema,
    _split_oai_messages,
    _strip_unsupported_schema,
    parse_json_content,
)


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# UI 展示用：把任意 provider/use_case 字符串解析为 model_id（仅做字符串映射，
# 不创建客户端、不读 user-level api_keys；老调用方继续 work）
# ---------------------------------------------------------------------------

def get_model_display_name(provider: str, user_id: int | None = None) -> str:
    """Return model_id for UI display / model_tag.

    入参可以是：
      - use_case code（含 '.'）→ 查 binding，返回 binding.model
      - 老 provider 字符串（vertex_* / openrouter / doubao / claude_sonnet 等）
        → 查 LEGACY_PROVIDER_MODEL_MAP
      - 其它字符串 → 原样返回
    """
    del user_id  # 不再读 user-level api_keys；保留参数仅向后兼容
    if not isinstance(provider, str) or not provider:
        return ""
    if "." in provider:
        try:
            return llm_bindings.resolve(provider).get("model") or provider
        except KeyError:
            return provider
    mapped = legacy_provider_to_model(provider)
    if mapped:
        return mapped
    return provider


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
    provider_override: str | None = None,
    model_override: str | None = None,
):
    """跑一次 use_case 走的 invoke_chat，返回 (payload, usage)。

    把不同 adapter（openrouter/doubao/gemini_vertex/...）的返回值统一为
    业务函数期望的 (parsed_payload_dict_or_list, usage_dict_or_None) 二元组。

    过渡期注意：调用 invoke_chat 时刻意把 user_id 置 None，让 invoke_chat 内部
    _log_usage 立即 return（user_id is None → skip）。这样老业务 runtime
    外层的 _log_translate_billing 仍是唯一计费入口，迁移期间不会出现 ai_billing
    重复行。Phase A-4 删除外层 _log_translate_billing 后，再恢复透传 user_id。

    provider_override / model_override 让评测脚本（tools/translate_quality_eval
    等）跳过 binding 默认值，直接指定 provider+model 跑 A/B 对比。
    """
    from appcore import llm_client

    result = llm_client.invoke_chat(
        use_case,
        messages=messages,
        user_id=None,
        project_id=project_id,
        response_format=response_format,
        temperature=temperature if temperature is not None else 0.2,
        max_tokens=max_tokens if max_tokens is not None else 4096,
        provider_override=provider_override,
        model_override=model_override,
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


# ---------------------------------------------------------------------------
# 业务函数（generate_localized_translation / generate_tts_script /
# generate_localized_rewrite）—— 全部走 invoke_chat，use_case 必传。
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


def _require_use_case(use_case: str | None, *, fn: str) -> str:
    if not use_case or "." not in use_case:
        raise ValueError(
            f"{fn}: use_case= is required (must be a use_case code containing '.', "
            "e.g. 'video_translate.localize'). Old provider= dispatch was removed in D-4; "
            "all callers should pass use_case=. See appcore.llm_use_cases.USE_CASES."
        )
    return use_case


def _generate_localized_translation_single(
    source_full_text_zh: str,
    script_segments: list[dict],
    variant: str = "normal",
    custom_system_prompt: str | None = None,
    *,
    use_case: str,
    user_id: int | None = None,
    project_id: str | None = None,
    provider_override: str | None = None,
    model_override: str | None = None,
) -> dict:
    """Single-shot translation: original logic, no batching. Used directly for
    short videos and as the per-batch primitive for long-video batching."""
    use_case = _require_use_case(use_case, fn="_generate_localized_translation_single")
    messages = build_localized_translation_messages(
        source_full_text_zh,
        script_segments,
        variant=variant,
        custom_system_prompt=custom_system_prompt,
    )

    payload, usage = _invoke_chat_for_use_case(
        use_case, messages, LOCALIZED_TRANSLATION_RESPONSE_FORMAT,
        user_id=user_id, project_id=project_id,
        provider_override=provider_override,
        model_override=model_override,
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
    use_case: str,
    user_id: int | None,
    batch_size: int,
    project_id: str | None = None,
    provider_override: str | None = None,
    model_override: str | None = None,
    checkpoint_key: str | None = None,
) -> dict:
    """Long-video translation: split source segments into ~batch_size batches,
    call _single per batch, normalize per-batch indices to global, then merge.
    Each batch sees a small prompt so Claude/Gemini reliably return all the
    nested schema fields (source_segment_indices etc.).

    断点续传：每完成一批立刻把 accumulated 状态写到
    task_state._batch_checkpoints[checkpoint_key]，task 失败重跑时从已完成的
    那批之后接着跑（节省最贵的 LLM 调用）。"""
    batches = _split_segments_into_batches(script_segments, target_size=batch_size)
    log.info("localized_translation batched: %d segments → %d batches (size~%d)",
             len(script_segments), len(batches), batch_size)

    cp = _read_batch_checkpoint(project_id, checkpoint_key) or {}
    start_batch = int(cp.get("completed_batches") or 0)
    all_sentences: list[dict] = list(cp.get("all_sentences") or [])
    all_messages: list = list(cp.get("all_messages") or [])
    total_input = int(cp.get("total_input") or 0)
    total_output = int(cp.get("total_output") or 0)
    if start_batch > 0:
        log.info("localized_translation resume from checkpoint: batch %d/%d, cached %d sentences",
                 start_batch + 1, len(batches), len(all_sentences))

    for batch_idx, batch in enumerate(batches):
        if batch_idx < start_batch:
            continue
        log.info("localized_translation batch %d/%d (n=%d)",
                 batch_idx + 1, len(batches), len(batch))
        batch_source_text = "\n".join(
            (s.get("text") or "").strip() for s in batch if (s.get("text") or "").strip()
        )
        batch_result = _generate_localized_translation_single(
            batch_source_text, batch,
            variant=variant, custom_system_prompt=custom_system_prompt,
            use_case=use_case, user_id=user_id, project_id=project_id,
            provider_override=provider_override,
            model_override=model_override,
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
        _write_batch_checkpoint(project_id, checkpoint_key, {
            "completed_batches": batch_idx + 1,
            "all_sentences": all_sentences,
            "all_messages": all_messages,
            "total_input": total_input,
            "total_output": total_output,
        })

    for i, s in enumerate(all_sentences):
        s["index"] = i

    full_text = " ".join((s.get("text") or "").strip()
                          for s in all_sentences if (s.get("text") or "").strip())
    final = validate_localized_translation({"full_text": full_text, "sentences": all_sentences})
    if all_messages:
        final["_messages"] = all_messages
    if total_input or total_output:
        final["_usage"] = {"input_tokens": total_input, "output_tokens": total_output}
    _clear_batch_checkpoint(project_id, checkpoint_key)
    return final


def generate_localized_translation(
    source_full_text_zh: str,
    script_segments: list[dict],
    variant: str = "normal",
    custom_system_prompt: str | None = None,
    *,
    user_id: int | None = None,
    use_case: str | None = None,
    project_id: str | None = None,
    provider_override: str | None = None,
    model_override: str | None = None,
    checkpoint_key: str | None = None,
    # 已废弃；仅保留以避免老调用方 TypeError，值被忽略。
    provider: str | None = None,
    openrouter_api_key: str | None = None,
) -> dict:
    """Public entry: dispatches to single-shot for short videos and to the
    batched path for long videos based on config thresholds. Long-prompt LLM
    calls are the root cause of intermittent missing-field failures across
    Claude/Gemini; batching keeps each call's prompt size small.

    use_case 必传，走 appcore.llm_client.invoke_chat（adapter 解析 binding）。
    provider= / openrouter_api_key= 仅作为废弃 kwargs 保留以避免老调用方崩溃，
    实际值被忽略；如果要切 provider/model 用 provider_override / model_override。
    """
    del provider, openrouter_api_key  # noqa: F841 — 兼容签名但忽略
    use_case = _require_use_case(use_case, fn="generate_localized_translation")
    import config as _cfg
    if (
        getattr(_cfg, "MULTI_TRANSLATE_BATCH_ENABLED", True)
        and len(script_segments) > getattr(_cfg, "MULTI_TRANSLATE_BATCH_THRESHOLD", 18)
    ):
        return _generate_localized_translation_batched(
            source_full_text_zh, script_segments,
            variant=variant, custom_system_prompt=custom_system_prompt,
            use_case=use_case, user_id=user_id,
            batch_size=getattr(_cfg, "MULTI_TRANSLATE_BATCH_SIZE", 12),
            project_id=project_id,
            provider_override=provider_override,
            model_override=model_override,
            checkpoint_key=checkpoint_key,
        )
    return _generate_localized_translation_single(
        source_full_text_zh, script_segments,
        variant=variant, custom_system_prompt=custom_system_prompt,
        use_case=use_case, user_id=user_id, project_id=project_id,
        provider_override=provider_override,
        model_override=model_override,
    )


def _generate_tts_script_single(
    localized_translation: dict,
    *,
    use_case: str,
    user_id: int | None = None,
    messages_builder=None,
    response_format_override=None,
    validator=None,
    project_id: str | None = None,
    provider_override: str | None = None,
    model_override: str | None = None,
) -> dict:
    """Single-shot tts_script generation: original logic, no batching."""
    use_case = _require_use_case(use_case, fn="_generate_tts_script_single")
    builder = messages_builder or build_tts_script_messages
    messages = builder(localized_translation)
    rf = response_format_override or TTS_SCRIPT_RESPONSE_FORMAT

    payload, usage = _invoke_chat_for_use_case(
        use_case, messages, rf,
        user_id=user_id, project_id=project_id,
        provider_override=provider_override,
        model_override=model_override,
    )

    log.info("tts_script parsed payload type=%s keys=%s",
             type(payload).__name__,
             list(payload.keys()) if isinstance(payload, dict) else f"list[{len(payload)}]")
    validate_fn = validator or validate_tts_script
    sentences = (localized_translation or {}).get("sentences") or []
    try:
        result = validate_fn(payload, sentences=sentences)
    except TypeError:
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
    use_case: str,
    user_id: int | None,
    messages_builder,
    response_format_override,
    validator,
    batch_size: int,
    project_id: str | None = None,
    provider_override: str | None = None,
    model_override: str | None = None,
    checkpoint_key: str | None = None,
) -> dict:
    """Long-translation tts_script: split sentences into ~batch_size batches,
    generate per-batch blocks/subtitle_chunks, merge, then run a single
    validate_tts_script(sentences=...) so derive recomputes all nested
    indices coherently against the full sentence list.

    断点续传：每完成一批立刻写 task_state._batch_checkpoints[checkpoint_key]。"""
    sentences = localized_translation.get("sentences") or []
    sentence_batches = _split_segments_into_batches(sentences, target_size=batch_size)
    log.info("tts_script batched: %d sentences → %d batches",
             len(sentences), len(sentence_batches))

    cp = _read_batch_checkpoint(project_id, checkpoint_key) or {}
    start_batch = int(cp.get("completed_batches") or 0)
    all_blocks: list[dict] = list(cp.get("all_blocks") or [])
    all_chunks: list[dict] = list(cp.get("all_chunks") or [])
    all_messages: list = list(cp.get("all_messages") or [])
    total_input = int(cp.get("total_input") or 0)
    total_output = int(cp.get("total_output") or 0)
    if start_batch > 0:
        log.info("tts_script resume from checkpoint: batch %d/%d, cached %d blocks / %d chunks",
                 start_batch + 1, len(sentence_batches), len(all_blocks), len(all_chunks))

    for batch_idx, batch in enumerate(sentence_batches):
        if batch_idx < start_batch:
            continue
        log.info("tts_script batch %d/%d (n=%d)",
                 batch_idx + 1, len(sentence_batches), len(batch))
        sub_localized = {
            "full_text": " ".join((s.get("text") or "") for s in batch),
            "sentences": batch,
        }
        batch_result = _generate_tts_script_single(
            sub_localized,
            use_case=use_case, user_id=user_id, project_id=project_id,
            messages_builder=messages_builder,
            response_format_override=response_format_override,
            validator=validator,
            provider_override=provider_override,
            model_override=model_override,
        )
        all_blocks.extend(batch_result.get("blocks") or [])
        all_chunks.extend(batch_result.get("subtitle_chunks") or [])
        msgs = batch_result.get("_messages")
        if msgs:
            all_messages.extend(msgs if isinstance(msgs, list) else [msgs])
        usage = batch_result.get("_usage") or {}
        total_input += int(usage.get("input_tokens") or 0)
        total_output += int(usage.get("output_tokens") or 0)
        _write_batch_checkpoint(project_id, checkpoint_key, {
            "completed_batches": batch_idx + 1,
            "all_blocks": all_blocks,
            "all_chunks": all_chunks,
            "all_messages": all_messages,
            "total_input": total_input,
            "total_output": total_output,
        })

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
    _clear_batch_checkpoint(project_id, checkpoint_key)
    return final


def generate_tts_script(
    localized_translation: dict,
    *,
    user_id: int | None = None,
    use_case: str | None = None,
    project_id: str | None = None,
    messages_builder=None,
    response_format_override=None,
    validator=None,
    provider_override: str | None = None,
    model_override: str | None = None,
    checkpoint_key: str | None = None,
    # 已废弃；保留以避免老调用方 TypeError。
    provider: str | None = None,
    openrouter_api_key: str | None = None,
) -> dict:
    """Public entry. Long sentences trigger batched generation; per-batch
    blocks/subtitle_chunks are merged and the full sentence list drives a
    single validate_tts_script(sentences=...) so derive can recompute all
    nested indices coherently.

    use_case 必传。
    """
    del provider, openrouter_api_key  # noqa: F841
    use_case = _require_use_case(use_case, fn="generate_tts_script")
    import config as _cfg
    sentences = (localized_translation or {}).get("sentences") or []
    if (
        getattr(_cfg, "MULTI_TRANSLATE_BATCH_ENABLED", True)
        and len(sentences) > getattr(_cfg, "MULTI_TRANSLATE_BATCH_THRESHOLD", 18)
    ):
        return _generate_tts_script_batched(
            localized_translation,
            use_case=use_case, user_id=user_id, project_id=project_id,
            messages_builder=messages_builder,
            response_format_override=response_format_override,
            validator=validator,
            batch_size=getattr(_cfg, "MULTI_TRANSLATE_BATCH_SIZE", 12),
            provider_override=provider_override,
            model_override=model_override,
            checkpoint_key=checkpoint_key,
        )
    return _generate_tts_script_single(
        localized_translation,
        use_case=use_case, user_id=user_id, project_id=project_id,
        messages_builder=messages_builder,
        response_format_override=response_format_override,
        validator=validator,
        provider_override=provider_override,
        model_override=model_override,
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


def _read_batch_checkpoint(task_id: str | None, key: str | None) -> dict | None:
    """Read previously-saved per-batch progress for a long-running batched
    LLM call. Returns None if not found, or the saved dict."""
    if not task_id or not key:
        return None
    try:
        from appcore import task_state
        task = task_state.get(task_id) or {}
        all_cp = task.get("_batch_checkpoints") or {}
        return all_cp.get(key)
    except Exception:
        log.exception("batch checkpoint read failed key=%s", key)
        return None


def _write_batch_checkpoint(task_id: str | None, key: str | None, payload: dict) -> None:
    """Persist per-batch progress so a retry can skip already-completed batches.
    Called after every batch completion inside the batched LLM functions."""
    if not task_id or not key:
        return
    try:
        from appcore import task_state
        task = task_state.get(task_id) or {}
        all_cp = dict(task.get("_batch_checkpoints") or {})
        all_cp[key] = payload
        task_state.update(task_id, _batch_checkpoints=all_cp)
    except Exception:
        log.exception("batch checkpoint write failed key=%s", key)


def _clear_batch_checkpoint(task_id: str | None, key: str | None) -> None:
    """Drop a checkpoint after the batched call finishes successfully."""
    if not task_id or not key:
        return
    try:
        from appcore import task_state
        task = task_state.get(task_id) or {}
        all_cp = dict(task.get("_batch_checkpoints") or {})
        if key in all_cp:
            del all_cp[key]
            task_state.update(task_id, _batch_checkpoints=all_cp)
    except Exception:
        log.exception("batch checkpoint clear failed key=%s", key)


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
    use_case: str,
    user_id: int | None = None,
    temperature: float = 0.2,
    feedback_notes: str | None = None,
    project_id: str | None = None,
    provider_override: str | None = None,
    model_override: str | None = None,
) -> dict:
    """Single-shot rewrite: original logic, no batching. Used directly for
    short translations and as the per-batch primitive for long ones."""
    use_case = _require_use_case(use_case, fn="_generate_localized_rewrite_single")
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

    payload, usage = _invoke_chat_for_use_case(
        use_case, messages, LOCALIZED_TRANSLATION_RESPONSE_FORMAT,
        user_id=user_id, project_id=project_id,
        temperature=temperature,
        provider_override=provider_override,
        model_override=model_override,
    )

    log.info(
        "localized_rewrite parsed (use_case=%s, direction=%s, target_words=%d, "
        "temperature=%.2f, feedback=%s)",
        use_case, direction, target_words, temperature,
        "yes" if feedback_notes else "no",
    )
    # Rewrite 不应该改 source segment 对应关系——只是按字数重写文本。
    # 长 prompt / 高 temperature 下 LLM 偶尔漏 source_segment_indices，从
    # prev_localized_translation.sentences 按位补回。
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
    use_case: str,
    user_id: int | None,
    temperature: float,
    feedback_notes: str | None,
    batch_size: int,
    project_id: str | None = None,
    provider_override: str | None = None,
    model_override: str | None = None,
    checkpoint_key: str | None = None,
) -> dict:
    """Long-translation rewrite: split prev sentences into ~batch_size batches,
    allocate sub-target words proportionally by char count, rewrite each batch
    independently, then merge. Same long-prompt root-cause fix as translate.

    断点续传：每完成一批立刻写 task_state._batch_checkpoints[checkpoint_key]，
    重跑跳过已完成批次。"""
    sentences = prev_localized_translation.get("sentences") or []
    sentence_batches = _split_segments_into_batches(sentences, target_size=batch_size)
    sub_targets = _allocate_sub_target_words(sentence_batches, target_words)
    log.info(
        "localized_rewrite batched: %d sentences → %d batches, target=%d → %s",
        len(sentences), len(sentence_batches), target_words, sub_targets,
    )

    cp = _read_batch_checkpoint(project_id, checkpoint_key) or {}
    start_batch = int(cp.get("completed_batches") or 0)
    all_sentences: list[dict] = list(cp.get("all_sentences") or [])
    all_messages: list = list(cp.get("all_messages") or [])
    total_input = int(cp.get("total_input") or 0)
    total_output = int(cp.get("total_output") or 0)
    if start_batch > 0:
        log.info("localized_rewrite resume from checkpoint: batch %d/%d, cached %d sentences",
                 start_batch + 1, len(sentence_batches), len(all_sentences))

    for batch_idx, (batch, sub_target) in enumerate(zip(sentence_batches, sub_targets)):
        if batch_idx < start_batch:
            continue
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
            use_case=use_case, user_id=user_id, project_id=project_id,
            temperature=temperature, feedback_notes=feedback_notes,
            provider_override=provider_override,
            model_override=model_override,
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
        _write_batch_checkpoint(project_id, checkpoint_key, {
            "completed_batches": batch_idx + 1,
            "all_sentences": all_sentences,
            "all_messages": all_messages,
            "total_input": total_input,
            "total_output": total_output,
        })

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
    _clear_batch_checkpoint(project_id, checkpoint_key)
    return final


def generate_localized_rewrite(
    source_full_text: str,
    prev_localized_translation: dict,
    target_words: int,
    direction: str,
    source_language: str,
    messages_builder,
    *,
    user_id: int | None = None,
    use_case: str | None = None,
    project_id: str | None = None,
    temperature: float = 0.2,
    feedback_notes: str | None = None,
    provider_override: str | None = None,
    model_override: str | None = None,
    checkpoint_key: str | None = None,
    # 已废弃；保留以避免老调用方 TypeError。
    provider: str | None = None,
    openrouter_api_key: str | None = None,
) -> dict:
    """Rewrite an existing localized_translation to a target word count.

    use_case 必传；走 invoke_chat（adapter 解析 binding）。

    temperature 让上层（duration loop 内部 5 次 retry）逐次升温，避免 LLM 对同一
    prompt 输出字符级一致的同一份译文。feedback_notes 让上层把"前几次 attempt
    给出了多少词、目标多少"这种闭环反馈塞进 prompt，迫使 LLM 跳出固定模板。

    长 sentences 时自动走分批 rewrite（每批分配子目标字数），避免长 prompt 下
    Claude/Gemini 漏返 source_segment_indices 等嵌套字段。
    """
    del provider, openrouter_api_key  # noqa: F841
    use_case = _require_use_case(use_case, fn="generate_localized_rewrite")
    import config as _cfg
    sentences = prev_localized_translation.get("sentences") or []
    if (
        getattr(_cfg, "MULTI_TRANSLATE_BATCH_ENABLED", True)
        and len(sentences) > getattr(_cfg, "MULTI_TRANSLATE_BATCH_THRESHOLD", 18)
    ):
        return _generate_localized_rewrite_batched(
            source_full_text, prev_localized_translation, target_words,
            direction, source_language, messages_builder,
            use_case=use_case, user_id=user_id, project_id=project_id,
            temperature=temperature, feedback_notes=feedback_notes,
            batch_size=getattr(_cfg, "MULTI_TRANSLATE_BATCH_SIZE", 12),
            provider_override=provider_override,
            model_override=model_override,
            checkpoint_key=checkpoint_key,
        )
    return _generate_localized_rewrite_single(
        source_full_text, prev_localized_translation, target_words,
        direction, source_language, messages_builder,
        use_case=use_case, user_id=user_id, project_id=project_id,
        temperature=temperature, feedback_notes=feedback_notes,
        provider_override=provider_override,
        model_override=model_override,
    )
