"""Algorithm bodies physically copied from multi / translate_lab into omni
namespace (Phase 2).

设计目的（spec §6.2 + plan Phase 2）：

- ``omni_translate`` 实验版要支持 ``multi-like`` / ``shot_char_limit`` / 等
  插件组合；这些算法的源头在生产 ``multi_translate`` runner 和实验
  ``translate_lab`` (V2) runner 上。
- 用户硬约束 "multi 不动"——本模块**物理复制**这些算法到 omni 命名空间，
  ``runtime_omni.py`` 的 OmniTranslateRunner 上挂 thin shim method 调过来。
  这样 multi runtime 之后任何改动（含 llm_debug 注入等持续演进）都不会
  污染 omni 实验环境。
- ``av_sentence`` translate / ``sentence_units`` subtitle / ``sentence_reconcile``
  tts **不**在本模块——它们已经是抽象包（``translate_profiles/av_sync_profile``
  + ``tts_strategies/sentence_reconcile``）的一部分，OmniProfile 直接复用。

每个 ``_step_*`` 函数签名第一个参数是 ``runner``（OmniTranslateRunner 实例），
其余跟原 method 一致。函数内 ``self.X`` 全部改成 ``runner.X``。
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
from typing import Any

import appcore.task_state as task_state
from appcore.api_keys import resolve_key
from appcore.events import (
    EVT_ENGLISH_ASR_RESULT,
    EVT_LAB_SHOT_DECOMPOSE_RESULT,
    EVT_LAB_TRANSLATE_PROGRESS,
    EVT_SUBTITLE_READY,
    EVT_TRANSLATE_RESULT,
)
from appcore.llm_debug_payloads import prompt_file_payload
from appcore.llm_debug_runtime import save_llm_debug_calls
from appcore.preview_artifacts import (
    build_asr_artifact,
    build_asr_normalize_artifact,
    build_subtitle_artifact,
    build_translate_artifact,
)
from appcore.runtime import (
    _build_review_segments,
    _llm_request_payload,
    _llm_response_payload,
    _log_translate_billing,
    _save_json,
)
from pipeline import asr_normalize as pipeline_asr_normalize
from pipeline.languages.registry import SOURCE_LANGS as _MANUAL_SOURCE_LANGUAGES
from pipeline.localization import build_source_full_text_zh, count_words
from pipeline.subtitle import build_srt_from_chunks, save_srt
from pipeline.subtitle_alignment import align_subtitle_chunks_to_asr
from pipeline.translate import generate_localized_translation
from pipeline.tts import _get_audio_duration

log = logging.getLogger(__name__)

# Translate use case (复制自 runtime_multi.py，保持 binding 一致)
_TRANSLATE_USE_CASE = "video_translate.localize"
_CJK_CHAR_RE = re.compile(r"[㐀-䶿一-鿿豈-﫿]")


def _resolve_translate_use_case_binding(use_case: str = _TRANSLATE_USE_CASE) -> tuple[str, str]:
    """Return the actual provider/model binding for display purposes.

    复制自 runtime_multi.py 的同名函数。
    """
    try:
        from appcore import llm_bindings
        binding = llm_bindings.resolve(use_case)
        return str(binding.get("provider") or use_case), str(binding.get("model") or use_case)
    except Exception:
        from appcore.llm_use_cases import get_use_case
        default = get_use_case(use_case)
        return default["default_provider"], default["default_model"]


def _count_source_speech_units(text: str) -> int:
    """Source speech density (CJK + spaced langs)."""
    if not text:
        return 0
    cjk_chars = len(_CJK_CHAR_RE.findall(text))
    if cjk_chars:
        return cjk_chars + count_words(_CJK_CHAR_RE.sub(" ", text))
    return count_words(text)


def _ensure_source_transcript_is_actionable(
    *, source_full_text: str, video_duration: float, target_lang: str,
) -> None:
    """Fail fast when ASR is too sparse to support long-duration dubbing."""
    source_unit_count = _count_source_speech_units(source_full_text)
    if video_duration < 8.0:
        return
    min_words = max(5, int(math.floor(video_duration * 0.45)))
    if source_unit_count >= min_words:
        return
    raise RuntimeError(
        f"源视频语音过短（{video_duration:.1f}s 仅识别到 {source_unit_count} 字/词，"
        f"低于可靠翻译所需的 {min_words} 字/词），无法安全生成 {target_lang.upper()} 配音；"
        "请检查源视频是否为可翻译口播素材，或更换原视频后重试。"
    )


# ---------------------------------------------------------------------------
# ① ASR 后处理：asr_normalize（multi-like）
# ---------------------------------------------------------------------------


def step_asr_normalize(runner, task_id: str) -> None:
    """ASR 文本统一翻成英文。

    源头: appcore/runtime_multi.py:_step_asr_normalize（master b50b72c1）。
    本函数物理复制；任何 multi 后续演进都不会自动同步过来——这是有意的
    隔离边界。
    """
    task = task_state.get(task_id)
    utterances = task.get("utterances") or []

    if not utterances:
        runner._set_step(task_id, "asr_normalize", "done", "无音频文本，跳过标准化")
        return

    # resume 幂等：artifact 或 utterances_en 已经在了
    if task.get("asr_normalize_artifact") or task.get("utterances_en"):
        runner._set_step(task_id, "asr_normalize", "done", "已标准化（resume 跳过）")
        return

    src_lang = (task.get("source_language") or "").strip()

    if src_lang not in _MANUAL_SOURCE_LANGUAGES:
        err = (
            f"source_language={src_lang!r} 不在支持范围 "
            f"({', '.join(_MANUAL_SOURCE_LANGUAGES)})；请手动选择源语言"
        )
        runner._set_step(task_id, "asr_normalize", "failed", err)
        task_state.update(task_id, error=err, status="error")
        return

    runner._set_step(
        task_id, "asr_normalize", "running",
        f"按手动选择的源语言 {src_lang} 标准化…",
    )
    try:
        artifact = pipeline_asr_normalize.run_user_specified(
            task_id=task_id, user_id=runner.user_id,
            utterances=utterances, source_language=src_lang,
        )
    except Exception as exc:
        err = f"按手动选择源语言标准化失败：{exc}"
        runner._set_step(task_id, "asr_normalize", "failed", err)
        task_state.update(task_id, error=err, status="error")
        return

    save_llm_debug_calls(
        task_id=task_id,
        task_dir=task.get("task_dir") or "",
        step="asr_normalize",
        calls=artifact.pop("_llm_debug_calls", []),
        save_json=_save_json,
    )

    utterances_en = artifact.pop("_utterances_en", None)
    updates = {
        "source_language": src_lang,
        "user_specified_source_language": True,
        "detected_source_language": artifact["detected_source_language"],
        "asr_normalize_artifact": artifact,
    }
    if artifact["route"] not in ("en_skip", "zh_skip"):
        updates["utterances_en"] = utterances_en
    task_state.update(task_id, **updates)

    msg_map = {
        "en_skip": "原文为英文，跳过标准化",
        "zh_skip": "原文为中文，走中文路径",
        "es_specialized": "西班牙语 → 英文标准化完成",
        "generic_fallback":
            f"{artifact['detected_source_language']} → 英文标准化完成（通用）",
        "generic_fallback_low_confidence":
            f"{artifact['detected_source_language']} → 英文标准化完成（低置信兜底）",
        "generic_fallback_mixed": "混合语言 → 英文标准化完成（兜底）",
    }
    base_msg = msg_map.get(artifact["route"], "原文标准化完成")
    if artifact.get("detection_source") == "user_specified":
        base_msg = f"{base_msg}（用户指定）"
    runner._set_step(task_id, "asr_normalize", "done", base_msg)
    task_state.set_artifact(task_id, "asr_normalize", build_asr_normalize_artifact(
        artifact,
        source_utterances=utterances,
        en_utterances=utterances_en,
    ))


# ---------------------------------------------------------------------------
# ② 镜头分镜：shot_decompose
# ---------------------------------------------------------------------------


def step_shot_decompose(runner, task_id: str, video_path: str, task_dir: str) -> None:
    """Gemini 视觉分析视频，切镜头列表 + 时间轴对齐。

    源头: appcore/runtime_v2.py:_step_shot_decompose（master b50b72c1）。
    适配: V2 task 用 ``video_duration``；omni 走 _step_extract 也写了这个字段。
    """
    from pipeline.shot_decompose import align_asr_to_shots, decompose_shots
    from appcore import llm_bindings

    _sd_model = llm_bindings.resolve("shot_decompose.run").get("model") or "gemini-3.1-pro-preview"
    runner._set_step(task_id, "shot_decompose", "running", "Gemini 分镜分析中...",
                     model_tag=f"gemini · {_sd_model}")
    task = task_state.get(task_id) or {}
    duration = float(task.get("video_duration") or 0.0)

    shots = decompose_shots(
        video_path,
        user_id=runner.user_id,
        duration_seconds=duration,
    )

    utterances = task.get("utterances") or []
    asr_segments: list[dict[str, Any]] = []
    for utt in utterances:
        asr_segments.append({
            "start": float(utt.get("start_time") or utt.get("start") or 0.0),
            "end": float(utt.get("end_time") or utt.get("end") or 0.0),
            "text": utt.get("text", ""),
        })

    aligned = align_asr_to_shots(shots, asr_segments)
    task_state.update(task_id, shots=aligned)
    runner._emit(task_id, EVT_LAB_SHOT_DECOMPOSE_RESULT, {"shots": aligned})
    runner._set_step(task_id, "shot_decompose", "done",
                     f"分镜完成，共 {len(aligned)} 段")


# ---------------------------------------------------------------------------
# ③ 翻译算法：standard（multi-like，可选 source_anchored INPUT NOTICE）
# ---------------------------------------------------------------------------


def step_translate_standard(runner, task_id: str, *, source_anchored: bool) -> None:
    """整段一次性翻译（multi-like）。

    ``source_anchored=True`` 时给 system prompt 加 INPUT NOTICE，告诉 LLM
    输入是 ASR 文本不要捏造原视频之外的内容（这是 omni 当前默认行为）。

    源头：
    - body 主结构: appcore/runtime_multi.py:_step_translate（master b50b72c1）
    - INPUT NOTICE 段: appcore/runtime_omni.py:_step_translate（master 上当前 omni 默认）
    """
    task = task_state.get(task_id)
    task_dir = task["task_dir"]
    if runner._complete_original_video_passthrough(
        task_id, task.get("video_path") or "", task_dir,
    ):
        return
    lang = runner._resolve_target_lang(task)
    source_language = (task.get("source_language") or "").strip()
    if source_language not in _MANUAL_SOURCE_LANGUAGES:
        message = (
            f"source_language={source_language!r} 不在支持范围 "
            f"({', '.join(_MANUAL_SOURCE_LANGUAGES)})；请手动选择源语言"
        )
        task_state.update(task_id, status="error", error=message)
        runner._set_step(task_id, "translate", "failed", message)
        return

    provider_code, model_id = _resolve_translate_use_case_binding(_TRANSLATE_USE_CASE)
    _model_tag = f"{provider_code} · {model_id}"
    if source_anchored:
        running_msg = f"正在从 {source_language.upper()} 直译为 {lang.upper()}（source-anchored）..."
    else:
        running_msg = f"正在翻译为 {lang.upper()}..."
    runner._set_step(task_id, "translate", "running", running_msg, model_tag=_model_tag)

    script_segments = task.get("script_segments", [])
    source_full_text = build_source_full_text_zh(script_segments)
    task_state.update(task_id, source_full_text_zh=source_full_text)
    _save_json(task_dir, "source_full_text.json",
               {"full_text": source_full_text, "language": source_language})

    from pipeline.extract import get_video_duration
    video_duration = get_video_duration(task.get("video_path") or "")
    _ensure_source_transcript_is_actionable(
        source_full_text=source_full_text,
        video_duration=video_duration,
        target_lang=lang,
    )

    base_prompt = runner._build_system_prompt(lang)
    if source_anchored:
        notice = (
            f"\n\nINPUT NOTICE: The source script provided below is in "
            f"{source_language.upper()}. It came from automatic speech recognition "
            f"of the original video and may contain transcription artifacts. "
            f"Treat it as the source of truth for content; do NOT invent details "
            f"that are not implied by it. If a segment is unintelligible, keep "
            f"your version brief instead of fabricating context."
        )
        system_prompt = base_prompt + notice
    else:
        system_prompt = base_prompt

    localized_translation = generate_localized_translation(
        source_full_text, script_segments, variant="normal",
        custom_system_prompt=system_prompt,
        user_id=runner.user_id,
        use_case=_TRANSLATE_USE_CASE,
        project_id=task_id,
    )

    initial_messages = localized_translation.pop("_messages", None)
    request_payload = _llm_request_payload(
        localized_translation, provider_code, _TRANSLATE_USE_CASE,
        messages=initial_messages,
    )
    if request_payload:
        request_payload["model"] = model_id
    if initial_messages:
        _save_json(task_dir, "localized_translate_messages.json", prompt_file_payload(
            phase="initial_translate",
            label="初始翻译",
            use_case_code=_TRANSLATE_USE_CASE,
            provider=provider_code,
            model=model_id,
            messages=initial_messages,
            request_payload=request_payload,
            meta={
                "target_language": lang,
                "source_language": source_language,
                "source_anchored": source_anchored,
                "custom_system_prompt_used": True,
            },
        ))
        task_state.add_llm_debug_ref(task_id, "translate", {
            "id": "translate.initial",
            "label": "初始翻译",
            "path": "localized_translate_messages.json",
            "use_case": _TRANSLATE_USE_CASE,
            "provider": provider_code,
            "model": model_id,
            "target_language": lang,
        })

    variants = dict(task.get("variants", {}))
    variant_state = dict(variants.get("normal", {}))
    variant_state["localized_translation"] = localized_translation
    variants["normal"] = variant_state
    _save_json(task_dir, "localized_translation.normal.json", localized_translation)

    review_segments = _build_review_segments(script_segments, localized_translation)
    requires_confirmation = bool(task.get("interactive_review"))
    task_state.update(
        task_id,
        source_full_text_zh=source_full_text,
        localized_translation=localized_translation,
        variants=variants,
        segments=review_segments,
        _segments_confirmed=not requires_confirmation,
    )
    task_state.set_artifact(task_id, "asr",
                             build_asr_artifact(task.get("utterances", []),
                                                source_full_text,
                                                source_language=source_language))
    task_state.set_artifact(task_id, "translate",
                             build_translate_artifact(source_full_text,
                                                      localized_translation,
                                                      source_language=source_language,
                                                      target_language=lang))
    _save_json(task_dir, "localized_translation.json", localized_translation)

    usage = localized_translation.get("_usage") or {}
    _log_translate_billing(
        user_id=runner.user_id,
        project_id=task_id,
        use_case_code=_TRANSLATE_USE_CASE,
        provider=_TRANSLATE_USE_CASE,
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"),
        success=True,
        request_payload=request_payload,
        response_payload=_llm_response_payload(localized_translation),
    )

    if requires_confirmation:
        task_state.set_current_review_step(task_id, "translate")
        runner._set_step(task_id, "translate", "waiting",
                         f"{lang.upper()} 翻译已生成，等待人工确认")
    else:
        task_state.set_current_review_step(task_id, "")
        if source_anchored:
            done_msg = f"{source_language.upper()} → {lang.upper()} 直译完成（source-anchored）"
        else:
            done_msg = f"{lang.upper()} 本土化翻译完成"
        runner._set_step(task_id, "translate", "done", done_msg)

    runner._emit(task_id, EVT_TRANSLATE_RESULT, {
        "source_full_text_zh": source_full_text,
        "localized_translation": localized_translation,
        "segments": review_segments,
        "requires_confirmation": requires_confirmation,
    })


# ---------------------------------------------------------------------------
# ③ 翻译算法：shot_char_limit（lab）
# ---------------------------------------------------------------------------


def step_translate_shot_limit(runner, task_id: str) -> None:
    """每镜头独立翻译，按"镜头时长 × cps"算字符上限。

    源头: appcore/runtime_v2.py:_step_translate（master b50b72c1）。
    适配:
    - V2 task 字段 ``target_language`` / ``chosen_voice`` → omni task
      字段 ``target_lang`` / ``selected_voice_id``。
    - V2 task 字段 ``shots`` 由 step_shot_decompose 写入（同 omni）。
    - V2 cps 基准初始化原本住在 _step_voice_match——按 spec §3，omni
      voice_match 步骤里若 cfg["translate_algo"] == "shot_char_limit" 时
      会自动调 initialize_baseline；此函数只负责读 cps 用。
    """
    from pipeline.speech_rate_model import get_rate
    from pipeline.translate_v2 import compute_char_limit, translate_shot
    from appcore import llm_bindings

    _tr_model = llm_bindings.resolve("translate_lab.shot_translate").get("model") or ""
    runner._set_step(task_id, "translate", "running", "正在按镜头翻译...",
                     model_tag=f"gemini · {_tr_model}")
    task = task_state.get(task_id) or {}
    shots: list[dict[str, Any]] = task.get("shots") or []
    if not shots:
        raise RuntimeError("translate_algo=shot_char_limit 需要 shots（请先开启 shot_decompose）")
    voice_id = task.get("selected_voice_id") or ""
    target_lang = runner._resolve_target_lang(task)
    default_cps = 15.0
    cps = get_rate(voice_id, target_lang) or default_cps

    translations: list[dict[str, Any]] = []
    for i, shot in enumerate(shots):
        if shot.get("silent"):
            translations.append({
                "shot_index": shot.get("index"),
                "translated_text": "",
                "char_count": 0,
                "over_limit": False,
                "retries": 0,
            })
            continue
        limit = compute_char_limit(float(shot.get("duration") or 0.0), cps)
        prev_translation = (
            translations[-1]["translated_text"] if translations else None
        )
        next_source = (
            shots[i + 1].get("source_text") if i + 1 < len(shots) else None
        )
        result = translate_shot(
            shot=shot,
            target_language=target_lang,
            char_limit=max(1, limit),
            prev_translation=prev_translation,
            next_source=next_source,
            user_id=runner.user_id,
        )
        translations.append(result)
        runner._emit(task_id, EVT_LAB_TRANSLATE_PROGRESS, {
            "index": shot.get("index"),
            "result": result,
        })

    task_state.update(task_id, translations=translations)

    # 构建下游 TTS / subtitle 需要的统一数据结构（兼容 base TTS loop + 字幕步骤）
    sentences = []
    for i, tr in enumerate(translations):
        if tr.get("translated_text"):
            sentences.append({
                "index": i,
                "text": tr["translated_text"],
                "source_segment_indices": [i],
            })
    localized_translation = {
        "full_text": "\n".join(
            tr["translated_text"] for tr in translations
            if tr.get("translated_text")
        ),
        "sentences": sentences,
    }

    script_segments = []
    for shot in shots:
        script_segments.append({
            "index": shot.get("index"),
            "text": shot.get("source_text", ""),
            "start_time": float(shot.get("start") or 0.0),
            "end_time": float(shot.get("end") or 0.0),
        })

    source_full_text = "\n".join(
        shot.get("source_text", "") for shot in shots
        if shot.get("source_text")
    )

    variants = dict(task.get("variants", {}))
    variant_state = dict(variants.get("normal", {}))
    variant_state["localized_translation"] = localized_translation
    variants["normal"] = variant_state

    task_state.update(
        task_id,
        localized_translation=localized_translation,
        script_segments=script_segments,
        source_full_text=source_full_text,
        source_full_text_zh=source_full_text,
        variants=variants,
    )
    runner._set_step(task_id, "translate", "done",
                     f"{target_lang.upper()} 镜头级翻译完成（{len(translations)} 段）")


# ---------------------------------------------------------------------------
# ⑥ 字幕生成：asr_realign（multi-like）
# ---------------------------------------------------------------------------


def step_subtitle_asr_realign(runner, task_id: str, task_dir: str) -> None:
    """TTS 后再跑一次 ASR 拿词级时间戳，按词重新对齐字幕。

    源头: appcore/runtime_multi.py:_step_subtitle（master b50b72c1）。
    """
    task = task_state.get(task_id)
    if runner._complete_original_video_passthrough(
        task_id, task.get("video_path") or "", task_dir,
    ):
        return
    lang = runner._resolve_target_lang(task)
    rules = runner._get_lang_rules(lang)

    from appcore import asr_router

    _sub_adapter, _ = asr_router.resolve_adapter("subtitle_asr", lang)
    _sub_model_tag = f"{_sub_adapter.display_name} · {_sub_adapter.model_id}"
    runner._set_step(
        task_id, "subtitle", "running",
        f"正在根据 {lang.upper()} 音频校正字幕...",
        model_tag=_sub_model_tag,
    )

    variants = dict(task.get("variants", {}))
    variant_state = dict(variants.get("normal", {}))
    tts_audio_path = variant_state.get("tts_audio_path", "")

    _sub_result = asr_router.transcribe(
        tts_audio_path, source_language=lang, stage="subtitle_asr",
    )
    utterances = _sub_result["utterances"]
    asr_result = {
        "full_text": " ".join(u.get("text", "").strip()
                                for u in utterances if u.get("text")).strip(),
        "utterances": utterances,
    }
    tts_script = variant_state.get("tts_script", {})
    total_duration = _get_audio_duration(tts_audio_path) if tts_audio_path else 0.0
    corrected_chunks = align_subtitle_chunks_to_asr(
        tts_script.get("subtitle_chunks", []),
        asr_result,
        total_duration=total_duration,
    )

    srt_content = build_srt_from_chunks(
        corrected_chunks,
        weak_boundary_words=rules.WEAK_STARTERS,
        max_chars_per_line=getattr(rules, "MAX_CHARS_PER_LINE", 42),
        max_lines=getattr(rules, "MAX_LINES", 2),
    )
    srt_content = rules.post_process_srt(srt_content)

    srt_path = save_srt(srt_content, os.path.join(task_dir, "subtitle.normal.srt"))

    variant_state.update({
        "english_asr_result": asr_result,
        "corrected_subtitle": {"chunks": corrected_chunks,
                                 "srt_content": srt_content},
        "srt_path": srt_path,
    })
    task_state.set_preview_file(task_id, "srt", srt_path)
    variants["normal"] = variant_state

    task_state.update(
        task_id, variants=variants,
        english_asr_result=asr_result,
        corrected_subtitle={"chunks": corrected_chunks,
                              "srt_content": srt_content},
        srt_path=srt_path,
    )
    task_state.set_artifact(task_id, "subtitle",
                             build_subtitle_artifact(asr_result, corrected_chunks,
                                                      srt_content,
                                                      target_language=lang))

    _save_json(task_dir, f"{lang}_asr_result.normal.json", asr_result)
    _save_json(task_dir, "corrected_subtitle.normal.json",
               {"chunks": corrected_chunks, "srt_content": srt_content})

    runner._emit(task_id, EVT_ENGLISH_ASR_RESULT, {"english_asr_result": asr_result})
    runner._emit(task_id, EVT_SUBTITLE_READY, {"srt": srt_content})
    runner._set_step(task_id, "subtitle", "done", f"{lang.upper()} 字幕生成完成")

    # Fire-and-forget translation-quality assessment.
    try:
        from appcore import quality_assessment as _qa
        _qa.trigger_assessment(
            task_id=task_id, project_type=runner.project_type,
            triggered_by="auto", user_id=runner.user_id,
        )
    except Exception:
        log.warning("[%s] failed to trigger quality assessment for task %s",
                    runner.project_type, task_id, exc_info=True)
