"""Framework-agnostic pipeline runner.

No Flask, no socketio, no web imports.
Uses EventBus to publish status events consumed by any adapter (web, desktop).
"""
from __future__ import annotations

import json
import logging
import math
import os
import uuid
from datetime import datetime

log = logging.getLogger(__name__)

import appcore.task_state as task_state
from appcore.api_keys import resolve_jianying_project_root
from appcore import ai_billing, tos_clients
from appcore.events import (
    EVT_ALIGNMENT_READY,
    EVT_ASR_RESULT,
    EVT_CAPCUT_READY,
    EVT_ENGLISH_ASR_RESULT,
    EVT_PIPELINE_DONE,
    EVT_PIPELINE_ERROR,
    EVT_STEP_UPDATE,
    EVT_SUBTITLE_READY,
    EVT_TRANSLATE_RESULT,
    EVT_TTS_SCRIPT_READY,
    Event,
    EventBus,
)
from web.preview_artifacts import (
    build_alignment_artifact,
    build_analysis_artifact,
    build_asr_artifact,
    build_compose_artifact,
    build_export_artifact,
    build_extract_artifact,
    build_subtitle_artifact,
    build_translate_artifact,
    build_tts_artifact,
)


def _upload_artifacts_to_tos(task: dict, task_id: str) -> None:
    """Upload final video/srt artifacts to TOS. Errors are silently ignored."""
    try:
        if not tos_clients.is_tos_configured():
            return
        user_id = task.get("_user_id", "anon")
        tos_uploads = dict(task.get("tos_uploads") or {})
        uploaded_at = datetime.now().isoformat(timespec="seconds")

        for variant, variant_state in (task.get("variants") or {}).items():
            result = variant_state.get("result", {})
            export_state = variant_state.get("exports", {})
            artifact_paths = {
                "soft_video": result.get("soft_video"),
                "hard_video": result.get("hard_video"),
                "srt": variant_state.get("srt_path"),
                "capcut_archive": export_state.get("capcut_archive"),
            }
            for artifact_kind, path in artifact_paths.items():
                if path and os.path.exists(path):
                    tos_key = tos_clients.build_artifact_object_key(user_id, task_id, variant, os.path.basename(path))
                    tos_clients.upload_file(path, tos_key)
                    tos_uploads[f"{variant}:{artifact_kind}"] = {
                        "tos_key": tos_key,
                        "artifact_kind": artifact_kind,
                        "variant": variant,
                        "file_size": os.path.getsize(path),
                        "uploaded_at": uploaded_at,
                    }

        if tos_uploads:
            import appcore.task_state as _ts
            _ts.update(task_id, tos_uploads=tos_uploads)
    except Exception:
        log.warning("[runtime] TOS artifact upload failed for task %s", task_id, exc_info=True)


def _save_json(task_dir: str, filename: str, data) -> None:
    path = os.path.join(task_dir, filename)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)


def _build_review_segments(script_segments: list[dict], localized_translation: dict) -> list[dict]:
    review_segments: list[dict] = []
    sentences = localized_translation.get("sentences", []) or []

    for fallback_index, sentence in enumerate(sentences):
        indices = sentence.get("source_segment_indices") or [fallback_index]
        source_segments = [
            script_segments[index]
            for index in indices
            if 0 <= index < len(script_segments)
        ]
        base_segment = source_segments[0] if source_segments else (
            script_segments[fallback_index] if fallback_index < len(script_segments) else {}
        )
        review_segments.append(
            {
                "index": sentence.get("index", fallback_index),
                "text": " ".join(
                    segment.get("text", "").strip()
                    for segment in source_segments
                    if segment.get("text")
                ).strip() or base_segment.get("text", ""),
                "translated": sentence.get("text", ""),
                "start_time": source_segments[0].get("start_time") if source_segments else base_segment.get("start_time"),
                "end_time": source_segments[-1].get("end_time") if source_segments else base_segment.get("end_time"),
                "source_segment_indices": indices,
            }
        )

    return review_segments


def _translate_billing_provider(provider: str) -> str:
    if provider == "doubao":
        return "doubao"
    if provider.startswith("vertex_"):
        return "gemini_vertex"
    return "openrouter"


def _translate_billing_model(provider: str, user_id: int | None) -> str:
    from pipeline.translate import get_model_display_name

    return get_model_display_name(provider, user_id)


def _log_translate_billing(
    *,
    user_id: int | None,
    project_id: str,
    use_case_code: str,
    provider: str,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    success: bool = True,
    extra: dict | None = None,
) -> None:
    ai_billing.log_request(
        use_case_code=use_case_code,
        user_id=user_id,
        project_id=project_id,
        provider=_translate_billing_provider(provider),
        model=_translate_billing_model(provider, user_id),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        units_type="tokens",
        success=success,
        extra=extra,
    )


def _seconds_to_request_units(audio_duration_seconds: float | None) -> int | None:
    if audio_duration_seconds is None:
        return None
    if audio_duration_seconds <= 0:
        return 0
    return int(math.ceil(audio_duration_seconds))


_VALID_TRANSLATE_PREFS = (
    # Vertex AI（Google Cloud Express Mode，复用图片翻译模块的 GEMINI_CLOUD_API_KEY）
    "vertex_gemini_31_flash_lite",   # gemini-3.1-flash-lite-preview（默认）
    "vertex_gemini_3_flash",         # gemini-3-flash-preview
    "vertex_gemini_31_pro",          # gemini-3.1-pro-preview
    # OpenRouter
    "gemini_31_flash",               # google/gemini-3.1-flash-lite-preview via openrouter
    "gemini_31_pro",                 # google/gemini-3.1-pro-preview via openrouter
    "gemini_3_flash",                # google/gemini-3-flash-preview via openrouter
    "claude_sonnet",                 # anthropic/claude-sonnet-4.6 via openrouter
    "openrouter",                    # legacy（= claude_sonnet）
    # 火山引擎
    "doubao",
)


def _resolve_translate_provider(user_id: int | None) -> str:
    """Return the user's preferred translate provider. 默认走 Vertex Flash-Lite。"""
    from appcore.api_keys import get_key
    default = "vertex_gemini_31_flash_lite"
    if user_id is None:
        return default
    pref = get_key(user_id, "translate_pref")
    return pref if pref in _VALID_TRANSLATE_PREFS else default


def _lang_display(label: str) -> str:
    """Convert language label (en/de/fr) to Chinese display name for step messages."""
    return {"en": "英语", "de": "德语", "fr": "法语"}.get(label, label)


# Default words-per-second by target language (fallback when no measured data).
_DEFAULT_WPS = {"en": 2.5, "de": 2.0, "fr": 2.8}


def _compute_next_target(
    round_index: int,
    last_audio_duration: float,
    wps: float,
    video_duration: float,
) -> tuple[float, int, str]:
    """Compute (target_duration, target_words, direction) for rewrite rounds 2+.

    Round 2 aims directly at video_duration (center of the [0.9v, 1.1v] range).
    Round 3+ uses adaptive over-correction: reverse half of the previous error,
    clamped to the range.

    Args:
        round_index: 2 or higher.
        last_audio_duration: audio length from the previous round (seconds).
        wps: words-per-second rate for this voice×language (measured or default).
        video_duration: original video duration (seconds).

    Returns:
        (target_duration_seconds, target_word_count, direction)
        direction ∈ {"shrink", "expand"}
    """
    duration_lo = video_duration * 0.9
    duration_hi = video_duration * 1.1
    center = video_duration

    if round_index == 2:
        target_duration = video_duration
        direction = "shrink" if last_audio_duration > center else "expand"
    else:  # round 3+
        raw = center - 0.5 * (last_audio_duration - center)
        target_duration = max(duration_lo, min(duration_hi, raw))
        direction = "shrink" if last_audio_duration > center else "expand"

    target_words = max(3, round(target_duration * wps))
    return target_duration, target_words, direction


def _distance_to_duration_range(duration: float, lower: float, upper: float) -> float:
    """Return the distance from duration to the inclusive [lower, upper] range."""
    if lower <= duration <= upper:
        return 0.0
    if duration > upper:
        return duration - upper
    return lower - duration


def _fit_tts_segments_to_duration(tts_segments: list[dict], target_duration: float) -> list[dict]:
    """Keep only the audible prefix of TTS segments within target_duration."""
    kept: list[dict] = []
    elapsed = 0.0
    target_duration = max(0.0, float(target_duration or 0.0))

    for segment in tts_segments:
        seg_duration = float(segment.get("tts_duration", 0.0) or 0.0)
        remaining = target_duration - elapsed
        if remaining <= 1e-6:
            break

        seg_copy = dict(segment)
        if seg_duration <= remaining + 1e-6:
            seg_copy["tts_duration"] = seg_duration
            kept.append(seg_copy)
            elapsed += seg_duration
            continue

        seg_copy["tts_duration"] = round(remaining, 3)
        kept.append(seg_copy)
        break

    return kept


def _trim_tts_metadata_to_segments(
    tts_script: dict,
    localized_translation: dict,
    tts_segments: list[dict],
) -> tuple[dict, dict]:
    """Trim script/localized metadata to the kept TTS segment indices."""
    kept_block_ids = {
        int(segment["index"])
        for segment in tts_segments
        if segment.get("index") is not None
    }
    new_blocks = [block for block in tts_script.get("blocks", []) if block.get("index") in kept_block_ids]
    new_subtitle_chunks = [
        chunk for chunk in tts_script.get("subtitle_chunks", [])
        if chunk.get("block_indices")
        and all(block_index in kept_block_ids for block_index in chunk["block_indices"])
    ]
    new_tts_script = {
        "full_text": " ".join(block.get("text", "") for block in new_blocks).strip(),
        "blocks": new_blocks,
        "subtitle_chunks": new_subtitle_chunks,
    }

    kept_sentence_ids: set[int] = set()
    for block in new_blocks:
        kept_sentence_ids.update(block.get("sentence_indices", []))
    new_sentences = [
        sentence for sentence in localized_translation.get("sentences", [])
        if sentence.get("index") in kept_sentence_ids
    ]
    new_localized_translation = {
        "full_text": " ".join(sentence.get("text", "") for sentence in new_sentences).strip(),
        "sentences": new_sentences,
    }
    return new_tts_script, new_localized_translation


class PipelineRunner:
    project_type: str = "translation"

    # ── TTS / localization 差异点（子类 override） ──
    tts_language_code: str | None = None           # ElevenLabs language_code; None=auto
    tts_model_id: str = "eleven_turbo_v2_5"        # ElevenLabs model_id
    tts_default_voice_language: str | None = None  # voice_library.ensure_defaults language; None=en
    localization_module: str = "pipeline.localization"
    target_language_label: str = "en"              # 中文消息展示标签，例如 "de" / "fr"

    # 是否在 compose 阶段生成软字幕视频（仅 v2 重新 override 为 True 保持原行为）
    include_soft_video: bool = False

    # 是否把 AI 视频分析放在主流程 _run() 的 steps 列表里（v2 override 为 True）
    include_analysis_in_main_flow: bool = False

    def __init__(self, bus: EventBus, user_id: int | None = None) -> None:
        self.bus = bus
        self.user_id = user_id

    def _emit(self, task_id: str, event_type: str, payload: dict) -> None:
        self.bus.publish(Event(type=event_type, task_id=task_id, payload=payload))

    def _set_step(self, task_id: str, step: str, status: str, message: str = "", *, model_tag: str = "") -> None:
        task_state.set_step(task_id, step, status)
        task_state.set_step_message(task_id, step, message)
        if model_tag:
            task_state.set_step_model_tag(task_id, step, model_tag)
        payload = {"step": step, "status": status, "message": message}
        existing_tag = model_tag or (task_state.get(task_id) or {}).get("step_model_tags", {}).get(step, "")
        if existing_tag:
            payload["model_tag"] = existing_tag
        self._emit(task_id, EVT_STEP_UPDATE, payload)

    def _emit_duration_round(self, task_id: str, round_index: int,
                             phase: str, record: dict) -> None:
        """Emit EVT_TTS_DURATION_ROUND with merged payload."""
        from appcore.events import EVT_TTS_DURATION_ROUND
        payload = dict(record)
        payload["round"] = round_index
        payload["phase"] = phase
        self._emit(task_id, EVT_TTS_DURATION_ROUND, payload)

    def _run_tts_duration_loop(
        self, *, task_id: str, task_dir: str, loc_mod,
        provider: str, video_duration: float, voice: dict,
        initial_localized_translation: dict, source_full_text: str,
        source_language: str, elevenlabs_api_key: str,
        script_segments: list, variant: str,
    ) -> dict:
        """Iterate translate_rewrite → tts_script_regen → audio_gen → measure
        up to 5 rounds until audio duration lands in [video-3, video].

        Returns dict with: localized_translation, tts_script, tts_audio_path,
        tts_segments, rounds, final_round.
        """
        import importlib
        from pipeline.tts import generate_full_audio, _get_audio_duration
        from pipeline.translate import generate_tts_script, generate_localized_rewrite

        MAX_ROUNDS = 5
        # Final target range (shown to the user, used for final success judgement):
        final_target_lo = max(0.0, video_duration - 3.0)
        final_target_hi = video_duration
        # Stage-1 convergence range (rewrite手段; approximate via ±10% of video):
        stage1_lo = video_duration * 0.9
        stage1_hi = video_duration * 1.1

        rounds: list[dict] = []
        round_products: list[dict] = []  # full per-round products (kept in-memory only)
        last_audio_duration = 0.0
        last_word_count = 0
        default_wps = _DEFAULT_WPS.get(self.target_language_label, 2.5)

        from functools import partial
        from pipeline.localization import count_words as _count_words
        validator = partial(
            getattr(loc_mod, "validate_tts_script", None)
            or importlib.import_module("pipeline.localization").validate_tts_script,
            max_words=14 if self.target_language_label in ("de", "fr") else 10,
        )

        for round_index in range(1, MAX_ROUNDS + 1):
            round_record: dict = {
                "round": round_index,
                "video_duration": video_duration,
                "duration_lo": final_target_lo,
                "duration_hi": final_target_hi,
                "stage1_lo": stage1_lo,
                "stage1_hi": stage1_hi,
                "artifact_paths": {},
            }

            # Phase 1: translate_rewrite (skipped on round 1).
            # IMPORTANT: rewrite uses the ORIGINAL ASR source and the INITIAL
            # localized_translation (round-1 product) as a style anchor — never
            # the previous rewrite's output. This prevents recursive drift
            # over multiple rounds.
            if round_index == 1:
                localized_translation = initial_localized_translation
                round_record["message"] = "初始译文（来自 translate 步骤）"
                # Round 1 没有 rewrite，但指向 translate 步骤落盘的初始 prompt，
                # UI 统一从 artifact_paths.initial_translate_messages 拉取
                if os.path.exists(os.path.join(task_dir, "localized_translate_messages.json")):
                    round_record["artifact_paths"]["initial_translate_messages"] = (
                        "localized_translate_messages.json"
                    )
                # 初始译文自身也写一份轮次快照，便于对比
                _save_json(
                    task_dir,
                    f"localized_translation.round_{round_index}.json",
                    localized_translation,
                )
                round_record["artifact_paths"]["localized_translation"] = (
                    f"localized_translation.round_{round_index}.json"
                )
                # token usage（若 translate 步骤记录过）
                init_usage = (initial_localized_translation or {}).get("_usage") or {}
                if init_usage:
                    round_record["translate_tokens_in"] = init_usage.get("input_tokens")
                    round_record["translate_tokens_out"] = init_usage.get("output_tokens")
            else:
                # wps: measured from round 1 if available, else language default.
                if last_audio_duration > 0 and last_word_count > 0:
                    wps = last_word_count / last_audio_duration
                else:
                    wps = default_wps
                target_duration, target_words, direction = _compute_next_target(
                    round_index, last_audio_duration, wps, video_duration,
                )
                round_record["target_duration"] = target_duration
                round_record["target_words"] = target_words
                round_record["wps_used"] = wps
                round_record["direction"] = direction
                round_record["message"] = (
                    f"第 {round_index} 轮：重译{_lang_display(self.target_language_label)}文案"
                    f"（目标 {target_words} 单词，{direction}）"
                )
                self._emit_duration_round(task_id, round_index, "translate_rewrite", round_record)

                # ========= 字数收敛内循环（最多 5 次 rewrite）=========
                # LLM 对 target_words 经常不听话。先确认文案字数在 ±10% 窗口内
                # 再去跑 TTS，避免浪费 TTS 调用。
                # 每次 attempt 的完整译文 JSON 单独落盘，UI 可逐一查看。
                MAX_REWRITE_ATTEMPTS = 5
                WORD_TOLERANCE = 0.10
                candidates: list[tuple[int, dict]] = []  # (abs_diff, translation)
                localized_translation = None
                chosen_attempt_idx = None
                for attempt in range(1, MAX_REWRITE_ATTEMPTS + 1):
                    candidate = generate_localized_rewrite(
                        source_full_text=source_full_text,
                        prev_localized_translation=initial_localized_translation,
                        target_words=target_words,
                        direction=direction,
                        source_language=source_language,
                        messages_builder=loc_mod.build_localized_rewrite_messages,
                        provider=provider,
                        user_id=self.user_id,
                    )
                    cand_words = _count_words(candidate.get("full_text", ""))
                    diff = abs(cand_words - target_words)
                    tolerance_abs = max(1, int(target_words * WORD_TOLERANCE))
                    candidates.append((diff, candidate))

                    # 每次 attempt 的完整译文都落盘，UI 可点链接查看
                    attempt_filename = (
                        f"localized_translation.round_{round_index}.attempt_{attempt}.json"
                    )
                    _save_json(task_dir, attempt_filename, candidate)

                    log.info(
                        "rewrite attempt %d/%d: got %d words (target %d, tol ±%d)",
                        attempt, MAX_REWRITE_ATTEMPTS, cand_words, target_words, tolerance_abs,
                    )
                    round_record.setdefault("rewrite_attempts", []).append({
                        "attempt": attempt,
                        "words": cand_words,
                        "diff": diff,
                        "accepted": diff <= tolerance_abs,
                        "artifact_path": attempt_filename,
                        # 取 full_text 前 200 字符作为快速预览，避免 UI 默认加载大 JSON
                        "preview_text": (candidate.get("full_text") or "")[:200],
                    })
                    if diff <= tolerance_abs:
                        localized_translation = candidate
                        chosen_attempt_idx = attempt - 1  # 列表下标
                        round_record["rewrite_attempt_used"] = attempt
                        round_record["rewrite_words_actual"] = cand_words
                        break
                if localized_translation is None:
                    # 5 次都没收敛 → 挑最接近 target 的
                    ranked = sorted(
                        enumerate(candidates), key=lambda kv: kv[1][0]
                    )
                    chosen_attempt_idx = ranked[0][0]
                    localized_translation = candidates[chosen_attempt_idx][1]
                    round_record["rewrite_attempt_used"] = chosen_attempt_idx + 1
                    round_record["rewrite_words_actual"] = _count_words(
                        localized_translation.get("full_text", "")
                    )
                    round_record["rewrite_converged"] = False
                    log.warning(
                        "rewrite did not converge after %d attempts, picking closest (%d words, target %d)",
                        MAX_REWRITE_ATTEMPTS,
                        round_record["rewrite_words_actual"],
                        target_words,
                    )
                else:
                    round_record["rewrite_converged"] = True
                # 标记哪一次 attempt 被选用作本轮 TTS 输入
                if chosen_attempt_idx is not None:
                    attempts_list = round_record.get("rewrite_attempts") or []
                    for i, a in enumerate(attempts_list):
                        a["is_used_for_tts"] = (i == chosen_attempt_idx)
                # =====================================================
                _save_json(task_dir, f"localized_translation.round_{round_index}.json", localized_translation)
                round_record["artifact_paths"]["localized_translation"] = f"localized_translation.round_{round_index}.json"
                # 捕获本轮 rewrite 的 token 消耗
                rewrite_usage = (localized_translation or {}).get("_usage") or {}
                if rewrite_usage:
                    round_record["translate_tokens_in"] = rewrite_usage.get("input_tokens")
                    round_record["translate_tokens_out"] = rewrite_usage.get("output_tokens")
                # Persist the actual LLM prompt used this round for audit / UI download.
                if localized_translation.get("_messages"):
                    rewrite_input_snapshot = [
                        {
                            "key": "source_full_text",
                            "title": "原始文本输入",
                            "content": source_full_text,
                        },
                        {
                            "key": "reference_translation",
                            "title": "本轮参考译文输入",
                            "content": json.dumps(
                                initial_localized_translation,
                                ensure_ascii=False,
                                indent=2,
                            ),
                        },
                    ]
                    _save_json(task_dir,
                               f"localized_rewrite_messages.round_{round_index}.json",
                               {"round": round_index,
                                "target_words": target_words,
                                "direction": direction,
                                "source_language": source_language,
                                "input_snapshot": rewrite_input_snapshot,
                                "messages": localized_translation["_messages"]})
                    round_record["artifact_paths"]["localized_rewrite_messages"] = (
                        f"localized_rewrite_messages.round_{round_index}.json"
                    )
                round_record["word_count_prev"] = last_word_count

            # Phase 2: tts_script_regen
            self._emit_duration_round(task_id, round_index, "tts_script_regen", round_record)
            tts_script = generate_tts_script(
                localized_translation,
                provider=provider, user_id=self.user_id,
                messages_builder=loc_mod.build_tts_script_messages,
                validator=validator,
            )
            _save_json(task_dir, f"tts_script.round_{round_index}.json", tts_script)
            round_record["artifact_paths"]["tts_script"] = f"tts_script.round_{round_index}.json"
            # TTS script 摘要 —— 朗读块数、平均/最大单块字数
            blocks = tts_script.get("blocks") or []
            if blocks:
                block_word_counts = [
                    _count_words(b.get("text", "")) for b in blocks
                ]
                round_record["tts_block_count"] = len(blocks)
                round_record["tts_avg_block_words"] = round(
                    sum(block_word_counts) / max(1, len(block_word_counts)), 1
                )
                round_record["tts_max_block_words"] = max(block_word_counts) if block_word_counts else 0
            # TTS script LLM token 消耗
            tts_usage = (tts_script or {}).get("_usage") or {}
            if tts_usage:
                round_record["tts_script_tokens_in"] = tts_usage.get("input_tokens")
                round_record["tts_script_tokens_out"] = tts_usage.get("output_tokens")

            # Phase 3: audio_gen
            self._emit_duration_round(task_id, round_index, "audio_gen", round_record)
            tts_segments = loc_mod.build_tts_segments(tts_script, script_segments)
            result = generate_full_audio(
                tts_segments, voice["elevenlabs_voice_id"], task_dir,
                variant=f"round_{round_index}",
                elevenlabs_api_key=elevenlabs_api_key,
                model_id=self.tts_model_id,
                language_code=self.tts_language_code,
            )
            round_record["artifact_paths"]["tts_full_audio"] = f"tts_full.round_{round_index}.mp3"

            # Phase 4: measure
            tts_full_text = tts_script.get("full_text", "")
            audio_duration = _get_audio_duration(result["full_audio_path"])
            word_count = _count_words(tts_full_text)
            round_record["audio_duration"] = audio_duration
            round_record["word_count"] = word_count
            round_record["tts_char_count"] = len(tts_full_text)
            round_record["wps_observed"] = (word_count / audio_duration) if audio_duration > 0 else 0.0

            # persist rounds incrementally so UI survives page refresh
            import appcore.task_state as task_state
            rounds.append(round_record)
            round_products.append({
                "localized_translation": localized_translation,
                "tts_script": tts_script,
                "tts_audio_path": result["full_audio_path"],
                "tts_segments": result["segments"],
            })
            task_state.update(task_id, tts_duration_rounds=rounds)

            self._emit_duration_round(task_id, round_index, "measure", round_record)

            if final_target_lo <= audio_duration <= final_target_hi:
                # 标记本轮为最终采用：UI 画 ✨ 徽章 + 底部摘要说明
                round_record["is_final"] = True
                round_record["final_reason"] = "converged"
                rounds[-1] = round_record
                task_state.update(
                    task_id,
                    tts_duration_rounds=rounds,
                    tts_duration_status="converged",
                    tts_final_round=round_index,
                    tts_final_reason="converged",
                    tts_final_distance=0.0,
                )
                self._emit_duration_round(task_id, round_index, "converged", round_record)
                return {
                    "localized_translation": localized_translation,
                    "tts_script": tts_script,
                    "tts_audio_path": result["full_audio_path"],
                    "tts_segments": result["segments"],
                    "rounds": rounds,
                    "final_round": round_index,
                }

            # Note: do NOT update `prev_localized` — every rewrite uses the initial.
            last_audio_duration = audio_duration
            last_word_count = word_count

        # MAX_ROUNDS rounds completed without landing in [video-3, video].
        # Pick the round whose audio_duration is closest to the final target range.
        import appcore.task_state as task_state
        best_i = min(
            range(len(rounds)),
            key=lambda i: _distance_to_duration_range(
                rounds[i]["audio_duration"], final_target_lo, final_target_hi,
            ),
        )
        best_record = rounds[best_i]
        best_product = round_products[best_i]
        best_distance = _distance_to_duration_range(
            best_record["audio_duration"], final_target_lo, final_target_hi,
        )
        best_record["message"] = (
            f"{MAX_ROUNDS} 轮未精确收敛，选第 {best_i + 1} 轮"
            f"（{best_record['audio_duration']:.1f}s，距 {video_duration:.1f}s 最近）"
        )
        best_record["is_final"] = True
        best_record["final_reason"] = "best_pick"
        best_record["final_distance"] = round(best_distance, 3)
        rounds[best_i] = best_record
        self._emit_duration_round(task_id, best_i + 1, "best_pick", best_record)
        task_state.update(
            task_id,
            tts_duration_rounds=rounds,
            tts_duration_status="converged",
            tts_final_round=best_i + 1,
            tts_final_reason="best_pick",
            tts_final_distance=round(best_distance, 3),
        )
        return {
            "localized_translation": best_product["localized_translation"],
            "tts_script": best_product["tts_script"],
            "tts_audio_path": best_product["tts_audio_path"],
            "tts_segments": best_product["tts_segments"],
            "rounds": rounds,
            "final_round": best_i + 1,
        }

    def _promote_final_artifacts(self, task_dir: str, final_round: int, variant: str) -> None:
        """Copy tts_full.round_{N}.mp3 to tts_full.{variant}.mp3 for downstream compatibility."""
        import shutil
        src = os.path.join(task_dir, f"tts_full.round_{final_round}.mp3")
        dst = os.path.join(task_dir, f"tts_full.{variant}.mp3")
        if os.path.exists(src):
            shutil.copy2(src, dst)

    def _truncate_audio_to_duration(
        self,
        *,
        input_audio_path: str,
        output_audio_path: str,
        duration: float,
        tts_segments: list,
        tts_script: dict,
        localized_translation: dict,
    ) -> dict:
        """Truncate final audio to duration and keep downstream metadata in sync."""
        import subprocess

        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", input_audio_path,
                "-t", str(round(float(duration), 3)),
                "-map", "0:a:0",
                "-vn",
                "-c:a", "libmp3lame",
                "-q:a", "2",
                output_audio_path,
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"最终音频截断失败: {result.stderr}")

        fitted_segments = _fit_tts_segments_to_duration(tts_segments, duration)
        fitted_tts_script, fitted_localized_translation = _trim_tts_metadata_to_segments(
            tts_script,
            localized_translation,
            fitted_segments,
        )
        removed_count = max(0, len(tts_segments) - len(fitted_segments))
        removed_duration = max(
            0.0,
            sum(float(segment.get("tts_duration", 0.0) or 0.0) for segment in tts_segments) - float(duration),
        )
        return {
            "skipped": False,
            "audio_path": output_audio_path,
            "tts_segments": fitted_segments,
            "tts_script": fitted_tts_script,
            "localized_translation": fitted_localized_translation,
            "removed_count": removed_count,
            "removed_duration": round(removed_duration, 3),
            "final_duration": round(float(duration), 3),
        }

    def _trim_tail_segments(
        self, *, task_dir: str, round_variant: str,
        tts_segments: list, tts_script: dict, localized_translation: dict,
        video_duration: float,
    ) -> dict:
        """Drop trailing blocks to land in [video-3, video].

        Policy:
          - If total audio ≤ video_duration, skip (short is acceptable).
          - Otherwise, drop blocks from the tail one at a time:
              * If a drop lands duration in [video-3, video] → stop, adopt it.
              * If a drop overshoots below video-3 → stop, pick the candidate
                (any drop state with duration ≤ video) whose duration is
                closest to [video-3, video]. Never return a state with
                duration > video (hard upper bound).
              * Else (still > video) → keep dropping.
          - If we run out of blocks, raise.

        Returns dict with keys:
          - skipped: True if total ≤ video_duration
          - audio_path, tts_script, localized_translation, tts_segments
          - removed_count, removed_duration, final_duration
        """
        import subprocess

        total = sum(float(s.get("tts_duration", 0.0) or 0.0) for s in tts_segments)
        if total <= video_duration:
            return {"skipped": True}

        final_target_lo = max(0.0, video_duration - 3.0)
        final_target_hi = video_duration

        kept = list(tts_segments)
        removed: list[dict] = []
        current = total
        # Candidates are states where audio ≤ video (satisfies the hard upper bound).
        candidates: list[dict] = []
        final_state: dict | None = None

        while kept:
            seg = kept.pop()
            removed.append(seg)
            current -= float(seg.get("tts_duration", 0.0) or 0.0)

            if current <= final_target_hi:
                candidates.append({
                    "kept": list(kept),
                    "removed": list(removed),
                    "duration": current,
                })

            if final_target_lo <= current <= final_target_hi:
                # Landed in range — perfect.
                final_state = candidates[-1]
                break

            if current < final_target_lo:
                # Overshot below target range — pick the candidate closest to [lo, hi].
                # candidates is non-empty here (we just appended one on this iteration).
                final_state = min(
                    candidates,
                    key=lambda c: _distance_to_duration_range(c["duration"], final_target_lo, final_target_hi),
                )
                break
            # current still > final_target_hi: keep dropping.

        if final_state is None or not final_state["kept"]:
            raise RuntimeError(
                "尾部裁剪后无剩余朗读块——单块时长已超视频时长，无法产出音频。"
            )

        kept = final_state["kept"]
        removed = final_state["removed"]
        current = final_state["duration"]

        seg_dir = os.path.join(task_dir, "tts_segments", round_variant)
        os.makedirs(seg_dir, exist_ok=True)
        concat_list = os.path.join(seg_dir, "concat_trimmed.txt")
        with open(concat_list, "w", encoding="utf-8") as f:
            for s in kept:
                f.write(f"file '{os.path.abspath(s['tts_path'])}'\n")
        out_path = os.path.join(task_dir, f"tts_full.{round_variant}.trimmed.mp3")
        r = subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_list,
             "-c", "copy", out_path],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            raise RuntimeError(f"尾部裁剪 ffmpeg 拼接失败: {r.stderr}")

        new_tts_script, new_loc = _trim_tts_metadata_to_segments(tts_script, localized_translation, kept)

        return {
            "skipped": False,
            "audio_path": out_path,
            "tts_script": new_tts_script,
            "localized_translation": new_loc,
            "tts_segments": kept,
            "removed_count": len(removed),
            "removed_duration": total - current,
            "final_duration": current,
        }

    def _resolve_voice(self, task: dict, loc_mod) -> dict:
        """Resolve voice for TTS: explicit task.voice_id → recommended → default.

        Falls back to loc_mod.DEFAULT_{MALE,FEMALE}_VOICE_ID if library has none.
        """
        from pipeline.tts import get_voice_by_id

        voice = None
        if task.get("voice_id"):
            voice = get_voice_by_id(task["voice_id"], self.user_id)
        if not voice and task.get("recommended_voice_id"):
            voice = get_voice_by_id(task["recommended_voice_id"], self.user_id)
        if not voice:
            from pipeline.voice_library import get_voice_library
            gender = task.get("voice_gender", "male")
            lib = get_voice_library()
            if self.tts_default_voice_language:
                lib.ensure_defaults(self.user_id, language=self.tts_default_voice_language)
                voice = lib.get_default_voice(self.user_id, gender=gender,
                                              language=self.tts_default_voice_language)
            else:
                voice = lib.get_default_voice(self.user_id, gender=gender)
        if not voice:
            default_male = getattr(loc_mod, "DEFAULT_MALE_VOICE_ID", None)
            default_female = getattr(loc_mod, "DEFAULT_FEMALE_VOICE_ID", None)
            gender = task.get("voice_gender", "male")
            voice = {
                "id": None,
                "elevenlabs_voice_id": default_male if gender == "male" else default_female,
                "name": "Default",
            }
        return voice

    def start(self, task_id: str) -> None:
        self._run(task_id, start_step="extract")

    def resume(self, task_id: str, start_step: str) -> None:
        self._run(task_id, start_step=start_step)

    def _get_pipeline_steps(self, task_id: str, video_path: str, task_dir: str) -> list:
        """Return ordered [(step_name, callable), ...] for the pipeline.

        Subclasses can override to insert or remove steps while keeping
        all of _run()'s error handling, resume, and waiting-pause behavior.
        """
        steps = [
            ("extract", lambda: self._step_extract(task_id, video_path, task_dir)),
            ("asr", lambda: self._step_asr(task_id, task_dir)),
            ("alignment", lambda: self._step_alignment(task_id, video_path, task_dir)),
            ("translate", lambda: self._step_translate(task_id)),
            ("tts", lambda: self._step_tts(task_id, task_dir)),
            ("subtitle", lambda: self._step_subtitle(task_id, task_dir)),
            ("compose", lambda: self._step_compose(task_id, video_path, task_dir)),
            ("analysis", lambda: self._step_analysis(task_id)),
            ("export", lambda: self._step_export(task_id, video_path, task_dir)),
        ]
        if not self.include_analysis_in_main_flow:
            steps = [s for s in steps if s[0] != "analysis"]
        return steps

    def _run(self, task_id: str, start_step: str = "extract") -> None:
        # Make sure the source video is present locally before any step runs.
        # If it was orphaned (e.g. uploads dir cleanup) but we have a TOS backup,
        # this pulls it back. Otherwise we fail loud with a user-friendly message.
        try:
            from appcore.source_video import ensure_local_source_video
            ensure_local_source_video(task_id)
        except Exception as exc:
            task_state.update(task_id, status="error", error=str(exc))
            task_state.set_expires_at(task_id, self.project_type)
            self._emit(task_id, EVT_PIPELINE_ERROR, {"error": str(exc)})
            return

        task = task_state.get(task_id)
        video_path = task["video_path"]
        task_dir = task["task_dir"]
        steps = self._get_pipeline_steps(task_id, video_path, task_dir)

        try:
            should_run = False
            for step_name, step_fn in steps:
                if step_name == start_step:
                    should_run = True
                if not should_run:
                    continue
                step_fn()
                current = task_state.get(task_id) or {}
                if current.get("steps", {}).get(step_name) == "waiting":
                    return
        except Exception as exc:
            task_state.update(task_id, status="error", error=str(exc))
            task_state.set_expires_at(task_id, self.project_type)
            self._emit(task_id, EVT_PIPELINE_ERROR, {"error": str(exc)})

    def _step_extract(self, task_id: str, video_path: str, task_dir: str) -> None:
        self._set_step(task_id, "extract", "running", "正在提取音频...")
        from pipeline.extract import extract_audio

        audio_path = extract_audio(video_path, task_dir)
        task_state.update(task_id, audio_path=audio_path)
        task_state.set_preview_file(task_id, "audio_extract", audio_path)
        task_state.set_artifact(task_id, "extract", build_extract_artifact())
        self._set_step(task_id, "extract", "done", "音频提取完成")

    def _step_asr(self, task_id: str, task_dir: str) -> None:
        task = task_state.get(task_id)
        audio_path = task["audio_path"]
        self._set_step(task_id, "asr", "running", "正在上传音频到 TOS...")
        from appcore.api_keys import resolve_key
        from pipeline.extract import get_video_duration
        from pipeline.asr import transcribe
        from pipeline.storage import delete_file, upload_file

        volc_api_key = resolve_key(self.user_id, "volc", "VOLC_API_KEY")
        tos_key = f"asr-audio/{task_id}_{uuid.uuid4().hex[:8]}.wav"
        audio_url = upload_file(audio_path, tos_key)
        self._set_step(task_id, "asr", "running", "正在识别中文语音...")
        try:
            utterances = transcribe(audio_url, volc_api_key=volc_api_key)
        finally:
            try:
                delete_file(tos_key)
            except Exception:
                pass

        task_state.update(task_id, utterances=utterances)
        task_state.set_artifact(task_id, "asr", build_asr_artifact(utterances))
        _save_json(task_dir, "asr_result.json", {"utterances": utterances})
        try:
            audio_duration_seconds = get_video_duration(audio_path)
        except Exception:
            audio_duration_seconds = max(
                (float(item.get("end_time") or 0.0) for item in utterances),
                default=0.0,
            )
        ai_billing.log_request(
            use_case_code="video_translate.asr",
            user_id=self.user_id,
            project_id=task_id,
            provider="doubao_asr",
            model="big-model",
            request_units=_seconds_to_request_units(audio_duration_seconds),
            units_type="seconds",
            audio_duration_seconds=audio_duration_seconds,
            success=True,
        )

        if not utterances:
            self._set_step(task_id, "asr", "done", "未检测到语音内容，可能是纯音乐/音效视频")
            self._emit(task_id, EVT_ASR_RESULT, {"segments": []})
            raise RuntimeError("未检测到语音内容。该视频可能是纯音乐或音效背景视频，无法进行语音翻译。")

        self._set_step(task_id, "asr", "done", f"识别完成，共 {len(utterances)} 段")
        self._emit(task_id, EVT_ASR_RESULT, {"segments": utterances})

    def _step_alignment(self, task_id: str, video_path: str, task_dir: str) -> None:
        task = task_state.get(task_id)
        self._set_step(task_id, "alignment", "running", "正在分析镜头并生成分段建议...")
        from pipeline.alignment import compile_alignment, detect_scene_cuts
        from pipeline.voice_library import get_voice_library

        scene_cuts = detect_scene_cuts(video_path)
        alignment = compile_alignment(task.get("utterances", []), scene_cuts=scene_cuts)
        suggested_voice = get_voice_library().recommend_voice(
            self.user_id,
            " ".join(item.get("text", "") for item in task.get("utterances", []))
        )
        task_state.update(
            task_id,
            scene_cuts=scene_cuts,
            alignment=alignment,
            script_segments=alignment["script_segments"],
            segments=alignment["script_segments"],
            recommended_voice_id=suggested_voice["id"] if suggested_voice else None,
            _alignment_confirmed=False,
        )
        task_state.set_artifact(
            task_id,
            "alignment",
            build_alignment_artifact(scene_cuts, alignment["script_segments"], alignment["break_after"]),
        )
        _save_json(task_dir, "alignment_result.json", alignment)

        current = task_state.get(task_id) or {}
        payload = {
            "utterances": task.get("utterances", []),
            "scene_cuts": scene_cuts,
            "alignment": alignment,
            "break_after": alignment["break_after"],
            "recommended_voice_id": suggested_voice["id"] if suggested_voice else None,
            "requires_confirmation": bool(current.get("interactive_review")),
        }
        if current.get("interactive_review"):
            task_state.set_current_review_step(task_id, "alignment")
            self._set_step(task_id, "alignment", "waiting", "分段结果已生成，等待人工确认")
            self._emit(task_id, EVT_ALIGNMENT_READY, payload)
            return

        task_state.set_current_review_step(task_id, "")
        task_state.update(task_id, _alignment_confirmed=True)
        self._set_step(task_id, "alignment", "done", "分段分析完成")
        self._emit(task_id, EVT_ALIGNMENT_READY, payload)

    def _step_translate(self, task_id: str) -> None:
        task = task_state.get(task_id)
        task_dir = task["task_dir"]
        from pipeline.localization import build_source_full_text_zh
        from pipeline.translate import generate_localized_translation

        provider = _resolve_translate_provider(self.user_id)
        from pipeline.translate import get_model_display_name as _get_model_name
        _model_tag = f"{provider} · {_get_model_name(provider, self.user_id)}"
        self._set_step(task_id, "translate", "running", "正在生成整段本土化翻译...", model_tag=_model_tag)

        script_segments = task.get("script_segments", [])
        source_full_text_zh = build_source_full_text_zh(script_segments)

        variant = "normal"
        custom_prompt = task.get("custom_translate_prompt")
        localized_translation = generate_localized_translation(
            source_full_text_zh, script_segments, variant=variant,
            custom_system_prompt=custom_prompt,
            provider=provider, user_id=self.user_id,
        )

        # 先把初始翻译的 Prompt 单独落盘，后续时长迭代 round 1 可以复用
        initial_messages = localized_translation.pop("_messages", None)
        if initial_messages:
            _save_json(task_dir, "localized_translate_messages.json", {
                "phase": "initial_translate",
                "variant": variant,
                "custom_system_prompt_used": bool(custom_prompt),
                "messages": initial_messages,
            })

        variants = dict(task.get("variants", {}))
        variant_state = dict(variants.get(variant, {}))
        variant_state["localized_translation"] = localized_translation
        variants[variant] = variant_state
        _save_json(task_dir, "localized_translation.normal.json", localized_translation)

        review_segments = _build_review_segments(script_segments, localized_translation)
        requires_confirmation = bool(task.get("interactive_review"))
        task_state.update(
            task_id,
            source_full_text_zh=source_full_text_zh,
            localized_translation=localized_translation,
            variants=variants,
            segments=review_segments,
            _segments_confirmed=not requires_confirmation,
        )
        task_state.set_artifact(task_id, "asr", build_asr_artifact(task.get("utterances", []), source_full_text_zh))
        task_state.set_artifact(task_id, "translate", build_translate_artifact(source_full_text_zh, localized_translation))

        _save_json(task_dir, "source_full_text_zh.json", {"full_text": source_full_text_zh})
        _save_json(task_dir, "localized_translation.json", localized_translation)

        _translate_usage = localized_translation.get("_usage") or {}
        _log_translate_billing(
            user_id=self.user_id,
            project_id=task_id,
            use_case_code="video_translate.localize",
            provider=provider,
            input_tokens=_translate_usage.get("input_tokens"),
            output_tokens=_translate_usage.get("output_tokens"),
            success=True,
        )

        if requires_confirmation:
            task_state.set_current_review_step(task_id, "translate")
            self._set_step(task_id, "translate", "waiting", "翻译结果已生成，等待人工确认")
        else:
            task_state.set_current_review_step(task_id, "")
            self._set_step(task_id, "translate", "done", "本土化翻译完成")

        self._emit(task_id, EVT_TRANSLATE_RESULT, {
            "source_full_text_zh": source_full_text_zh,
            "localized_translation": localized_translation,
            "segments": review_segments,
            "requires_confirmation": requires_confirmation,
        })

    def _step_tts(self, task_id: str, task_dir: str) -> None:
        import importlib
        import appcore.task_state as task_state

        task = task_state.get(task_id)
        loc_mod = importlib.import_module(self.localization_module)

        lang_display = _lang_display(self.target_language_label)

        from appcore.api_keys import resolve_key
        from pipeline.extract import get_video_duration

        provider = _resolve_translate_provider(self.user_id)
        from pipeline.translate import get_model_display_name as _get_model_name
        _tts_model_tag = f"{provider} · {_get_model_name(provider, self.user_id)}"
        self._set_step(task_id, "tts", "running", f"正在生成{lang_display}配音...", model_tag=_tts_model_tag)
        elevenlabs_api_key = resolve_key(self.user_id, "elevenlabs", "ELEVENLABS_API_KEY")
        voice = self._resolve_voice(task, loc_mod)

        variant = "normal"
        variants = dict(task.get("variants", {}))
        variant_state = dict(variants.get(variant, {}))
        initial_localized = variant_state.get("localized_translation", {}) \
                            or task.get("localized_translation", {})
        source_full_text = task.get("source_full_text_zh") or task.get("source_full_text", "")
        source_language = task.get("source_language", "zh")
        video_duration = get_video_duration(task["video_path"])

        # reset duration tracking for a fresh run (e.g. resume)；
        # 顺带保存本次翻译走的 provider + model + channel，给前端 Duration Loop 头部展示
        from pipeline.translate import get_model_display_name
        if provider.startswith("vertex_"):
            _channel_label = "Vertex AI"
        elif provider == "doubao":
            _channel_label = "火山引擎 (豆包)"
        else:
            _channel_label = "OpenRouter"
        task_state.update(
            task_id,
            tts_duration_rounds=[],
            tts_duration_status="running",
            tts_translate_provider=provider,
            tts_translate_model=get_model_display_name(provider, self.user_id),
            tts_translate_channel=_channel_label,
        )

        loop_result = self._run_tts_duration_loop(
            task_id=task_id,
            task_dir=task_dir,
            loc_mod=loc_mod,
            provider=provider,
            video_duration=video_duration,
            voice=voice,
            initial_localized_translation=initial_localized,
            source_full_text=source_full_text,
            source_language=source_language,
            elevenlabs_api_key=elevenlabs_api_key,
            script_segments=task.get("script_segments", []),
            variant=variant,
        )

        # Final selection:
        # - if audio > video, truncate the final audio to video duration;
        # - if audio <= video, keep it as-is.
        from pipeline.tts import _get_audio_duration
        final_round = loop_result["final_round"]
        pre_trim_duration = _get_audio_duration(loop_result["tts_audio_path"])
        import shutil
        final_audio_path = os.path.join(task_dir, f"tts_full.{variant}.mp3")
        if pre_trim_duration > video_duration:
            trim_record = {
                "pre_trim_duration": pre_trim_duration,
                "video_duration": video_duration,
                "message": (
                    f"音频 {pre_trim_duration:.1f}s 超过视频 {video_duration:.1f}s，"
                    "正在直接截断到视频时长..."
                ),
            }
            self._emit_duration_round(task_id, final_round, "truncate_audio", trim_record)
            trim_result = self._truncate_audio_to_duration(
                input_audio_path=loop_result["tts_audio_path"],
                output_audio_path=final_audio_path,
                duration=video_duration,
                tts_segments=loop_result["tts_segments"],
                tts_script=loop_result["tts_script"],
                localized_translation=loop_result["localized_translation"],
            )
            if not trim_result.get("skipped"):
                loop_result["tts_audio_path"] = trim_result["audio_path"]
                loop_result["tts_script"] = trim_result["tts_script"]
                loop_result["localized_translation"] = trim_result["localized_translation"]
                loop_result["tts_segments"] = trim_result["tts_segments"]
                trimmed_record = {
                    "pre_trim_duration": pre_trim_duration,
                    "removed_count": trim_result["removed_count"],
                    "removed_duration": trim_result["removed_duration"],
                    "final_duration": trim_result["final_duration"],
                    "video_duration": video_duration,
                    "message": (
                        f"截断完成：最终音频 {trim_result['final_duration']:.1f}s，"
                        f"对齐视频 {video_duration:.1f}s"
                    ),
                }
                self._emit_duration_round(task_id, final_round, "truncated", trimmed_record)

        # Copy the final audio to the standard variant filename when needed.
        import shutil
        final_audio_path = os.path.join(task_dir, f"tts_full.{variant}.mp3")
        if os.path.abspath(loop_result["tts_audio_path"]) != os.path.abspath(final_audio_path):
            shutil.copy2(loop_result["tts_audio_path"], final_audio_path)

        from pipeline.timeline import build_timeline_manifest
        timeline_manifest = build_timeline_manifest(
            loop_result["tts_segments"], video_duration=video_duration,
        )

        variant_state.update({
            "segments": loop_result["tts_segments"],
            "tts_script": loop_result["tts_script"],
            "tts_audio_path": final_audio_path,
            "timeline_manifest": timeline_manifest,
            "voice_id": voice.get("id"),
            "localized_translation": loop_result["localized_translation"],
        })
        variants[variant] = variant_state

        task_state.set_preview_file(task_id, "tts_full_audio", final_audio_path)
        _save_json(task_dir, "tts_script.normal.json", loop_result["tts_script"])
        _save_json(task_dir, "tts_result.normal.json", loop_result["tts_segments"])
        _save_json(task_dir, "timeline_manifest.normal.json", timeline_manifest)
        _save_json(task_dir, "localized_translation.normal.json", loop_result["localized_translation"])
        _save_json(task_dir, "tts_duration_rounds.json", loop_result["rounds"])

        task_state.update(
            task_id,
            variants=variants,
            segments=loop_result["tts_segments"],
            tts_script=loop_result["tts_script"],
            tts_audio_path=final_audio_path,
            voice_id=voice.get("id"),
            timeline_manifest=timeline_manifest,
            localized_translation=loop_result["localized_translation"],
        )

        task_state.set_artifact(task_id, "tts",
            build_tts_artifact(loop_result["tts_script"], loop_result["tts_segments"],
                               duration_rounds=loop_result["rounds"]))

        from appcore.events import EVT_TTS_SCRIPT_READY
        self._emit(task_id, EVT_TTS_SCRIPT_READY, {"tts_script": loop_result["tts_script"]})
        self._set_step(
            task_id, "tts", "done",
            f"{lang_display}配音生成完成（{loop_result['final_round']} 轮收敛）",
        )

        # Usage log for LLM + ElevenLabs (rewrite rounds 2/3 also recorded)
        for round_record in loop_result["rounds"]:
            round_idx = round_record["round"]
            if round_idx >= 2:
                _log_translate_billing(
                    user_id=self.user_id,
                    project_id=task_id,
                    use_case_code="video_translate.rewrite",
                    provider=provider,
                    input_tokens=round_record.get("translate_tokens_in"),
                    output_tokens=round_record.get("translate_tokens_out"),
                    success=True,
                )
            _log_translate_billing(
                user_id=self.user_id,
                project_id=task_id,
                use_case_code="video_translate.tts_script",
                provider=provider,
                input_tokens=round_record.get("tts_script_tokens_in"),
                output_tokens=round_record.get("tts_script_tokens_out"),
                success=True,
            )
            tts_char_count = round_record.get("tts_char_count")
            if tts_char_count is None and round_idx == loop_result["final_round"]:
                final_text = (loop_result.get("tts_script") or {}).get("full_text") or ""
                tts_char_count = len(final_text) if final_text else None
            ai_billing.log_request(
                use_case_code="video_translate.tts",
                user_id=self.user_id,
                project_id=task_id,
                provider="elevenlabs",
                model=self.tts_model_id,
                request_units=tts_char_count,
                units_type="chars",
                success=True,
            )

    def _step_subtitle(self, task_id: str, task_dir: str) -> None:
        task = task_state.get(task_id)
        self._set_step(task_id, "subtitle", "running", "正在根据英文音频校正字幕...")
        from appcore.api_keys import resolve_key
        from pipeline.asr import transcribe_local_audio

        volc_api_key = resolve_key(self.user_id, "volc", "VOLC_API_KEY")
        from pipeline.subtitle import build_srt_from_chunks, save_srt
        from pipeline.subtitle_alignment import align_subtitle_chunks_to_asr

        variant = "normal"
        variants = dict(task.get("variants", {}))
        variant_state = dict(variants.get(variant, {}))
        tts_audio_path = variant_state.get("tts_audio_path", "")

        english_utterances = transcribe_local_audio(
            tts_audio_path, prefix=f"tts-asr/{task_id}/normal", volc_api_key=volc_api_key
        )
        english_asr_result = {
            "full_text": " ".join(
                u.get("text", "").strip() for u in english_utterances if u.get("text")
            ).strip(),
            "utterances": english_utterances,
        }
        tts_script = variant_state.get("tts_script", {})
        from pipeline.tts import _get_audio_duration
        total_duration = _get_audio_duration(tts_audio_path) if tts_audio_path else 0.0
        corrected_chunks = align_subtitle_chunks_to_asr(
            tts_script.get("subtitle_chunks", []),
            english_asr_result,
            total_duration=total_duration,
        )
        srt_content = build_srt_from_chunks(corrected_chunks)
        srt_path = save_srt(srt_content, os.path.join(task_dir, "subtitle.normal.srt"))

        variant_state.update({
            "english_asr_result": english_asr_result,
            "corrected_subtitle": {"chunks": corrected_chunks, "srt_content": srt_content},
            "srt_path": srt_path,
        })
        task_state.set_preview_file(task_id, "srt", srt_path)
        variants[variant] = variant_state

        task_state.update(
            task_id,
            variants=variants,
            english_asr_result=english_asr_result,
            corrected_subtitle={"chunks": corrected_chunks, "srt_content": srt_content},
            srt_path=srt_path,
        )
        task_state.set_artifact(task_id, "subtitle", build_subtitle_artifact(english_asr_result, corrected_chunks, srt_content))
        _save_json(task_dir, "english_asr_result.normal.json", english_asr_result)
        _save_json(task_dir, "corrected_subtitle.normal.json", {"chunks": corrected_chunks, "srt_content": srt_content})

        self._emit(task_id, EVT_ENGLISH_ASR_RESULT, {"english_asr_result": english_asr_result})
        self._emit(task_id, EVT_SUBTITLE_READY, {"srt": srt_content})
        self._set_step(task_id, "subtitle", "done", "英文字幕生成完成")

    def _step_compose(self, task_id: str, video_path: str, task_dir: str) -> None:
        task = task_state.get(task_id)
        self._set_step(task_id, "compose", "running", "正在合成视频...")
        from pipeline.compose import compose_video

        variant = "normal"
        variants = dict(task.get("variants", {}))
        variant_state = dict(variants.get(variant, {}))
        result = compose_video(
            video_path=video_path,
            tts_audio_path=variant_state["tts_audio_path"],
            srt_path=variant_state["srt_path"],
            output_dir=task_dir,
            subtitle_position=task.get("subtitle_position", "bottom"),
            timeline_manifest=variant_state.get("timeline_manifest"),
            variant=variant,
            font_name=task.get("subtitle_font", "Impact"),
            font_size_preset=task.get("subtitle_size", "medium"),
            subtitle_position_y=float(task.get("subtitle_position_y", 0.68)),
            with_soft=self.include_soft_video,
        )
        variant_state["result"] = result
        variants[variant] = variant_state

        task_state.update(task_id, variants=variants, result=result, status="composing_done")
        if result.get("soft_video"):
            task_state.set_preview_file(task_id, "soft_video", result["soft_video"])
        if result.get("hard_video"):
            task_state.set_preview_file(task_id, "hard_video", result["hard_video"])
        task_state.set_artifact(task_id, "compose", build_compose_artifact())
        self._set_step(task_id, "compose", "done", "视频合成完成")

    def _step_analysis(self, task_id: str) -> None:
        """用 Gemini 对硬字幕视频做评分 + CSK 深度分析，结果并列展示。"""
        from pipeline import video_csk, video_score
        from appcore.gemini import resolve_config, model_display_name

        task = task_state.get(task_id) or {}
        variants = task.get("variants") or {}
        variant_state = variants.get("normal") or {}
        hard_video = (variant_state.get("result") or {}).get("hard_video")

        _, resolved_model = resolve_config(
            self.user_id, service="gemini_video_analysis",
            default_model=video_score.SCORE_MODEL,
        )
        model_label = model_display_name(resolved_model)
        _analysis_model_tag = f"gemini · {resolved_model}"
        self._set_step(task_id, "analysis", "running", "AI 分析中（评分 + CSK）...", model_tag=_analysis_model_tag)

        score_result = None
        csk_result = None
        score_err = ""
        csk_err = ""

        if not hard_video or not os.path.isfile(hard_video):
            self._set_step(task_id, "analysis", "done", "未找到硬字幕视频，跳过 AI 分析")
            task_state.set_artifact(task_id, "analysis", build_analysis_artifact(
                None, None,
                score_prompt=video_score.SYSTEM_PROMPT,
                csk_prompt=video_csk.CSK_PROMPT,
                score_error="未找到硬字幕视频",
                csk_error="未找到硬字幕视频",
                model_label=model_label,
            ))
            return

        try:
            score_result = video_score.score_video(hard_video, user_id=self.user_id, project_id=task_id)
        except Exception as e:
            score_err = str(e)
            log.warning("video_score 失败：%s", e)

        try:
            csk_result = video_csk.analyze_video(hard_video, user_id=self.user_id, project_id=task_id)
        except Exception as e:
            csk_err = str(e)
            log.warning("video_csk 失败：%s", e)

        task_state.set_artifact(task_id, "analysis", build_analysis_artifact(
            score_result, csk_result,
            score_prompt=video_score.SYSTEM_PROMPT,
            csk_prompt=video_csk.CSK_PROMPT,
            score_error=score_err,
            csk_error=csk_err,
            model_label=model_label,
        ))

        if score_err and csk_err:
            self._set_step(task_id, "analysis", "done", "AI 分析失败（评分与 CSK 均未成功）")
        elif score_err or csk_err:
            self._set_step(task_id, "analysis", "done", "AI 分析部分完成")
        else:
            total = (score_result or {}).get("total", 0)
            self._set_step(task_id, "analysis", "done", f"AI 分析完成，评分 {total}/100")

    def _step_export(self, task_id: str, video_path: str, task_dir: str) -> None:
        task = task_state.get(task_id)
        self._set_step(task_id, "export", "running", "正在导出 CapCut 项目...")
        from pipeline.capcut import export_capcut_project

        variant = "normal"
        variants = dict(task.get("variants", {}))
        variant_state = dict(variants.get(variant, {}))
        jianying_project_root = resolve_jianying_project_root(self.user_id)
        draft_title = (
            task.get("display_name")
            or task.get("original_filename")
            or os.path.basename(video_path)
        )
        export_result = export_capcut_project(
            video_path=video_path,
            tts_audio_path=variant_state["tts_audio_path"],
            srt_path=variant_state["srt_path"],
            output_dir=task_dir,
            timeline_manifest=variant_state.get("timeline_manifest"),
            variant=variant,
            draft_title=draft_title,
            jianying_project_root=jianying_project_root,
            subtitle_position=task.get("subtitle_position", "bottom"),
            subtitle_font=task.get("subtitle_font", "Impact"),
            subtitle_size=task.get("subtitle_size", 14),
            subtitle_position_y=float(task.get("subtitle_position_y", 0.68)),
        )
        exports = {
            "capcut_project": export_result["project_dir"],
            "capcut_archive": export_result["archive_path"],
            "capcut_manifest": export_result["manifest_path"],
            "jianying_project_dir": export_result.get("jianying_project_dir", ""),
        }
        variant_state["exports"] = exports
        variants[variant] = variant_state

        manifest_text = ""
        try:
            with open(export_result["manifest_path"], "r", encoding="utf-8") as fh:
                manifest_text = fh.read()
        except OSError:
            pass
        archive_url = f"/api/tasks/{task_id}/download/capcut?variant=normal"

        task_state.update(task_id, variants=variants, exports=exports, status="done")
        task_state.set_expires_at(task_id, self.project_type)
        task_state.set_artifact(task_id, "export", build_export_artifact(manifest_text, archive_url=archive_url))
        self._set_step(task_id, "export", "done", "CapCut 项目已导出")
        self._emit(task_id, EVT_CAPCUT_READY, {"variants": ["normal"]})
        self._emit(task_id, EVT_PIPELINE_DONE, {
            "task_id": task_id,
            "exports": {"normal": exports},
        })
        _upload_artifacts_to_tos(task_state.get(task_id) or {}, task_id)


def run_analysis_only(
    task_id: str,
    runner: "PipelineRunner",
) -> None:
    """单独执行 AI 视频分析步骤，不影响任务整体 status。

    所有异常只更新 steps.analysis 为 error、记录 step_message；
    绝不触碰 task 整体 status 与 error 字段。
    """
    try:
        runner._step_analysis(task_id)
    except Exception as exc:
        log.exception("AI 分析执行失败 task_id=%s", task_id)
        try:
            runner._set_step(task_id, "analysis", "error", f"AI 分析失败：{exc}")
        except Exception:
            pass
