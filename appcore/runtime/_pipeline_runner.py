"""Framework-agnostic pipeline runner.

No Flask, no socketio, no web imports.
Uses EventBus to publish status events consumed by any adapter (web, desktop).
"""
from __future__ import annotations

import json
import logging
import math
import os
import time
import uuid
from datetime import datetime

# Task-level auto-retry budget：失败时不直接阻塞流水线，先指数退避自愈几次。
# 配合 LLM 网络重试 + ElevenLabs 网络重试 + TTS segment 段缓存复用，瞬时
# 抖动最多让任务"慢一点"，不会让整条流水线一次失败就报死。
_TASK_AUTO_RETRY_MAX = 3
_TASK_AUTO_RETRY_DELAYS = [5, 30, 120]  # seconds before retry 1, 2, 3
_ALL_STEP_NAMES = (
    "extract", "asr", "asr_normalize", "voice_match", "alignment",
    "translate", "tts", "subtitle", "compose", "export",
)

import config

log = logging.getLogger(__name__)
logger = logging.getLogger(__name__)

import appcore.task_state as task_state
from appcore.api_keys import resolve_jianying_project_root
from appcore import ai_billing
from appcore import tts_generation_stats
from appcore.cancellation import OperationCancelled, throw_if_cancel_requested
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
    EVT_VOICE_MATCH_READY,
    Event,
    EventBus,
)
from appcore.preview_artifacts import (
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
from appcore.tts_language_guard import (
    TtsLanguageValidationError,
    extract_tts_script_text,
    validate_tts_script_language_or_raise,
)


from ._helpers import (
    _VALID_TRANSLATE_PREFS,
    _skip_legacy_artifact_upload,
    _save_json,
    _count_visible_chars,
    _join_utterance_text,
    _resolve_original_video_passthrough,
    _is_original_video_passthrough,
    _build_review_segments,
    _translate_billing_provider,
    _translate_billing_model,
    _log_translate_billing,
    _llm_request_payload,
    _llm_response_payload,
    _seconds_to_request_units,
    _resolve_translate_provider,
    _resolve_task_translate_provider,
    _lang_display,
    _is_av_pipeline_task,
    _av_target_lang,
    _tts_final_target_range,
    _DEFAULT_WPS,
    _compute_next_target,
    _distance_to_duration_range,
    _fit_tts_segments_to_duration,
    _trim_tts_metadata_to_segments,
)


def run_av_localize(*args, **kwargs):
    """Delegate AV dispatch back to the runtime facade to preserve old globals."""
    from . import run_av_localize as _run_av_localize

    return _run_av_localize(*args, **kwargs)


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

    def _emit_substep_msg(self, task_id: str, step: str, sub_msg: str) -> None:
        """Emit and persist a refreshed step message for live UI and polling."""
        task_state.set_step_message(task_id, step, sub_msg)
        task = task_state.get(task_id) or {}
        status = (task.get("steps") or {}).get(step, "running")
        payload = {"step": step, "status": status, "message": sub_msg}
        existing_tag = (task.get("step_model_tags") or {}).get(step, "")
        if existing_tag:
            payload["model_tag"] = existing_tag
        self._emit(task_id, EVT_STEP_UPDATE, payload)

    def _get_localization_module(self, task: dict):
        del task
        import importlib
        return importlib.import_module(self.localization_module)

    def _get_tts_target_language_label(self, task: dict) -> str:
        del task
        return self.target_language_label

    def _get_tts_model_id(self, task: dict) -> str:
        del task
        return self.tts_model_id

    def _get_tts_language_code(self, task: dict) -> str | None:
        del task
        return self.tts_language_code

    def _emit_duration_round(self, task_id: str, round_index: int,
                             phase: str, record: dict) -> None:
        """Emit EVT_TTS_DURATION_ROUND with merged payload."""
        from appcore.events import EVT_TTS_DURATION_ROUND
        payload = dict(record)
        payload["round"] = round_index
        payload["phase"] = phase
        payload["__current_phase"] = phase
        self._persist_duration_round(task_id, payload)
        self._emit(task_id, EVT_TTS_DURATION_ROUND, payload)

    def _persist_duration_round(self, task_id: str, payload: dict) -> None:
        """Keep websocket-only TTS progress visible to polling clients."""
        task = task_state.get(task_id) or {}
        if not task:
            return

        rounds = [dict(item) for item in (task.get("tts_duration_rounds") or [])]
        round_index = payload.get("round")
        idx = next((i for i, item in enumerate(rounds) if item.get("round") == round_index), -1)
        if idx >= 0:
            merged = dict(rounds[idx])
            merged.update(payload)
            rounds[idx] = merged
        else:
            rounds.append(dict(payload))

        phase = payload.get("phase")
        if phase in {"converged", "best_pick", "trimmed", "truncated"}:
            status = "converged"
        elif phase == "failed":
            status = "failed"
        else:
            status = "running"
        task_state.update(task_id, tts_duration_rounds=rounds, tts_duration_status=status)

    def _run_tts_duration_loop(
        self, *, task_id: str, task_dir: str, loc_mod,
        provider: str, video_duration: float, voice: dict,
        initial_localized_translation: dict, source_full_text: str,
        source_language: str, elevenlabs_api_key: str,
        script_segments: list, variant: str,
        target_language_label: str | None = None,
        tts_model_id: str | None = None,
        tts_language_code: str | None = None,
    ) -> dict:
        """Iterate translate_rewrite → tts_script_regen → audio_gen → measure
        up to 5 rounds until audio duration lands in [video-1, video+2].

        Returns dict with: localized_translation, tts_script, tts_audio_path,
        tts_segments, rounds, final_round.
        """
        import importlib
        from pipeline.tts import generate_full_audio, _get_audio_duration
        from pipeline.translate import generate_tts_script, generate_localized_rewrite

        target_language_label = target_language_label or self.target_language_label
        tts_model_id = tts_model_id or self.tts_model_id
        if tts_language_code is None:
            tts_language_code = self.tts_language_code

        MAX_ROUNDS = 5
        # Final target range (shown to the user, used for final success judgement):
        final_target_lo, final_target_hi = _tts_final_target_range(video_duration)
        # Stage-1 convergence range (rewrite手段; approximate via ±10% of video):
        stage1_lo = video_duration * 0.9
        stage1_hi = video_duration * 1.1

        rounds: list[dict] = []
        round_products: list[dict] = []  # full per-round products (kept in-memory only)
        last_audio_duration = 0.0
        last_word_count = 0
        default_wps = _DEFAULT_WPS.get(target_language_label, 2.5)

        from functools import partial
        from pipeline.localization import count_words as _count_words
        validator = partial(
            getattr(loc_mod, "validate_tts_script", None)
            or importlib.import_module("pipeline.localization").validate_tts_script,
            max_words=14 if target_language_label in ("de", "fr") else 10,
        )

        def _substep(sub: str) -> None:
            self._emit_substep_msg(
                task_id, "tts",
                f"正在生成{_lang_display(target_language_label)}配音 · 第 {round_index} 轮 · {sub}",
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
                    f"第 {round_index} 轮：重译{_lang_display(target_language_label)}文案"
                    f"（目标 {target_words} 单词，{direction}）"
                )
                _substep("准备重写译文")
                self._emit_duration_round(task_id, round_index, "translate_rewrite", round_record)

                # ========= 字数收敛内循环（最多 5 次 rewrite）=========
                # LLM 对 target_words 经常不听话。先确认文案字数在置信窗口内，
                # 再去跑 TTS，避免浪费 TTS 调用。
                # 每次 attempt 的完整译文 JSON 单独落盘，UI 可逐一查看。
                #
                # 不收敛事故复盘（2026-04-25 880694eb…1058c8 任务）：5 次 attempt
                # 用完全相同的 prompt + 默认 temperature=0.2，导致 Gemini Vertex 在
                # round 3 / round 5 的 5 次输出**字符级一致**，重试机制等于 1 次。
                # 修复：① 第一次低温稳一下，第二次起拉到 1.0 让分布发散；② attempt
                # 2+ 把"上次给了多少词、目标多少"塞进 prompt，迫使 LLM 跳出固定模板。
                # 这两条同时上才有意义——单独打温度，LLM 仍可能落到同一 basin（85
                # 词长版本 / 54 词短版本）；单独加反馈但保持低温也不会真的换文案。
                MAX_REWRITE_ATTEMPTS = 5
                WORD_TOLERANCE = 0.20
                candidates: list[tuple[int, dict]] = []  # (abs_diff, translation)
                localized_translation = None
                chosen_attempt_idx = None
                tolerance_abs = max(1, int(target_words * WORD_TOLERANCE))
                round_record["rewrite_word_tolerance_ratio"] = WORD_TOLERANCE
                round_record["rewrite_word_tolerance_abs"] = tolerance_abs
                round_record["rewrite_word_window"] = [
                    target_words - tolerance_abs,
                    target_words + tolerance_abs,
                ]
                prior_word_counts: list[int] = []
                for attempt in range(1, MAX_REWRITE_ATTEMPTS + 1):
                    attempt_temperature = 0.6 if attempt == 1 else 1.0
                    _substep(
                        f"重写译文 attempt {attempt}/{MAX_REWRITE_ATTEMPTS}"
                        f"（目标 {target_words} 词，{direction}）"
                    )
                    feedback_notes = None
                    if prior_word_counts:
                        feedback_notes = (
                            "RETRY CONTEXT (attempt {n} of {m}):\n"
                            "  · Earlier attempts in this round produced these word counts: "
                            "{prior} (target {target}, allowed window [{lo}, {hi}], direction {dir}).\n"
                            "  · ALL were rejected as outside the window.\n"
                            "  · DO NOT repeat the same translation. Generate a SUBSTANTIVELY "
                            "DIFFERENT version: vary sentence count, sentence boundaries, "
                            "vocabulary, and openings.\n"
                            "  · Push {dir} more aggressively this time. Land inside [{lo}, {hi}]."
                        ).format(
                            n=attempt,
                            m=MAX_REWRITE_ATTEMPTS,
                            prior=prior_word_counts,
                            target=target_words,
                            lo=target_words - tolerance_abs,
                            hi=target_words + tolerance_abs,
                            dir=direction,
                        )

                    candidate = generate_localized_rewrite(
                        source_full_text=source_full_text,
                        prev_localized_translation=initial_localized_translation,
                        target_words=target_words,
                        direction=direction,
                        source_language=source_language,
                        messages_builder=loc_mod.build_localized_rewrite_messages,
                        provider=provider,
                        user_id=self.user_id,
                        temperature=attempt_temperature,
                        feedback_notes=feedback_notes,
                        use_case="video_translate.rewrite",
                        project_id=task_id,
                        checkpoint_key=f"rewrite.{variant}.r{round_index}.a{attempt}",
                    )
                    cand_words = _count_words(candidate.get("full_text", ""))
                    diff = abs(cand_words - target_words)
                    candidates.append((diff, candidate))
                    prior_word_counts.append(cand_words)

                    # 每次 attempt 的完整译文都落盘，UI 可点链接查看
                    attempt_filename = (
                        f"localized_translation.round_{round_index}.attempt_{attempt}.json"
                    )
                    _save_json(task_dir, attempt_filename, candidate)

                    log.info(
                        "rewrite attempt %d/%d: got %d words (target %d, tol ±%d, T=%.2f)",
                        attempt, MAX_REWRITE_ATTEMPTS, cand_words, target_words,
                        tolerance_abs, attempt_temperature,
                    )
                    round_record.setdefault("rewrite_attempts", []).append({
                        "attempt": attempt,
                        "words": cand_words,
                        "diff": diff,
                        "accepted": diff <= tolerance_abs,
                        "temperature": attempt_temperature,
                        "had_feedback": feedback_notes is not None,
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
                    # 5 次都没收敛 → 记录最接近候选，但不进入 TTS。
                    # 一旦 round 1 拿到实测语速，后续文案必须先落在词数置信区间内；
                    # 偏离太多的候选即使合成音频也大概率无意义，只会浪费 TTS。
                    ranked = sorted(
                        enumerate(candidates), key=lambda kv: kv[1][0]
                    )
                    chosen_attempt_idx = ranked[0][0]
                    closest_diff, closest_candidate = candidates[chosen_attempt_idx]
                    closest_words = _count_words(closest_candidate.get("full_text", ""))
                    round_record["rewrite_attempt_closest"] = chosen_attempt_idx + 1
                    round_record["rewrite_words_actual"] = _count_words(
                        closest_candidate.get("full_text", "")
                    )
                    round_record["rewrite_converged"] = False
                    round_record["rewrite_audio_skipped"] = True
                    round_record["rewrite_reject_reason"] = (
                        f"closest candidate has {closest_words} words; "
                        f"target {target_words} ±{tolerance_abs}"
                    )
                    log.warning(
                        "rewrite did not converge after %d attempts, skipping TTS "
                        "(closest %d words, target %d ±%d, diff %d)",
                        MAX_REWRITE_ATTEMPTS,
                        closest_words,
                        target_words,
                        tolerance_abs,
                        closest_diff,
                    )
                    attempts_list = round_record.get("rewrite_attempts") or []
                    for i, a in enumerate(attempts_list):
                        a["is_closest"] = (i == chosen_attempt_idx)
                        a["is_used_for_tts"] = False
                    round_record["message"] = (
                        f"第 {round_index} 轮文案未进入词数置信区间，跳过语音生成"
                    )
                    rounds.append(round_record)
                    round_products.append(None)
                    task_state.update(task_id, tts_duration_rounds=rounds)
                    self._emit_duration_round(task_id, round_index, "rewrite_rejected", round_record)
                    continue
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
            _substep("切分朗读文案中")
            self._emit_duration_round(task_id, round_index, "tts_script_regen", round_record)
            tts_script = generate_tts_script(
                localized_translation,
                provider=provider, user_id=self.user_id,
                messages_builder=loc_mod.build_tts_script_messages,
                validator=validator,
                use_case="video_translate.tts_script",
                project_id=task_id,
                checkpoint_key=f"tts_script.{variant}.r{round_index}",
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
            tts_segments = loc_mod.build_tts_segments(tts_script, script_segments)
            round_record["audio_segments_total"] = len(tts_segments)
            round_record["audio_segments_done"] = 0
            _substep(f"生成 ElevenLabs 音频 0/{len(tts_segments)}")
            self._emit_duration_round(task_id, round_index, "audio_gen", round_record)

            from appcore.runtime._helpers import make_tts_progress_emitter

            def _sync_round_record(snap):
                round_record["audio_segments_done"] = snap["done"]
                round_record["audio_segments_total"] = snap["total"]
                self._emit_duration_round(task_id, round_index, "audio_gen", round_record)

            on_progress = make_tts_progress_emitter(
                self, task_id,
                lang_label=_lang_display(target_language_label),
                round_label=f"第 {round_index} 轮",
                extra_state_update=_sync_round_record,
            )

            result = generate_full_audio(
                tts_segments, voice["elevenlabs_voice_id"], task_dir,
                variant=f"round_{round_index}",
                elevenlabs_api_key=elevenlabs_api_key,
                model_id=tts_model_id,
                language_code=tts_language_code,
                on_progress=on_progress,
            )
            round_record["artifact_paths"]["tts_full_audio"] = f"tts_full.round_{round_index}.mp3"

            _substep("校验语言 / 测量时长")
            language_check_filename = f"tts_language_check.round_{round_index}.json"
            try:
                language_check = validate_tts_script_language_or_raise(
                    text=extract_tts_script_text(tts_script),
                    target_language=target_language_label,
                    user_id=self.user_id,
                    project_id=task_id,
                    variant=variant,
                    round_index=round_index,
                )
            except TtsLanguageValidationError as exc:
                language_check = exc.result or {
                    "is_target_language": False,
                    "reason": str(exc),
                }
                _save_json(task_dir, language_check_filename, language_check)
                round_record["language_check"] = language_check
                round_record["artifact_paths"]["tts_language_check"] = language_check_filename
                self._emit_duration_round(task_id, round_index, "language_check_failed", round_record)
                self._set_step(task_id, "tts", "error", str(exc))
                raise

            _save_json(task_dir, language_check_filename, language_check)
            round_record["language_check"] = language_check
            round_record["artifact_paths"]["tts_language_check"] = language_check_filename

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
                # 已落入 range，但常常仍差 1-2s；用 ffmpeg atempo 兜底精确对齐
                final_audio_path = self._maybe_tempo_align(
                    audio_path=result["full_audio_path"],
                    audio_duration=audio_duration,
                    video_duration=video_duration,
                    task_dir=task_dir, variant=variant,
                    round_record=round_record, task_id=task_id,
                )
                round_products[-1]["tts_audio_path"] = final_audio_path
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
                tts_generation_stats.finalize(
                    task_id=task_id,
                    task=task_state.get(task_id) or {},
                    rounds=rounds,
                )
                return {
                    "localized_translation": localized_translation,
                    "tts_script": tts_script,
                    "tts_audio_path": final_audio_path,
                    "tts_segments": result["segments"],
                    "rounds": rounds,
                    "round_products": round_products,
                    "final_round": round_index,
                }

            # ============= 变速短路分支（2026-05-04） =============
            # 进入 ±10% 但不在 [v-1, v+2] 时，用 ElevenLabs voice_settings.speed
            # 重新合成一遍音频。命中 final 即收敛；未命中走 atempo 兜底；变速本身
            # 失败则回退到原始音频走 atempo。无论哪条路径，都立即终结，不再继续
            # 后续 rewrite 轮次。
            from appcore.runtime import _in_speedup_window, _speedup_ratio
            if _in_speedup_window(
                audio_duration=audio_duration, video_duration=video_duration,
            ):
                speed = _speedup_ratio(audio_duration, video_duration)
                round_record["speedup_applied"] = True
                round_record["speedup_speed"] = round(speed, 4)
                round_record["speedup_pre_duration"] = audio_duration
                round_record["is_final"] = True
                _substep(f"变速短路：speed={speed:.4f}, 重生成 ElevenLabs 音频")
                self._emit_duration_round(task_id, round_index, "speedup_start", round_record)

                speedup_audio_path = None
                speedup_duration = None
                speedup_result = None
                speedup_failed_reason = None
                try:
                    from pipeline.tts import regenerate_full_audio_with_speed

                    def _on_speedup_seg_done(done, total, info):
                        self._emit_substep_msg(
                            task_id, "tts",
                            f"正在生成{_lang_display(target_language_label)}配音 · 第 {round_index} 轮 · 变速重生成 ElevenLabs 音频 {done}/{total}",
                        )
                        # 不更新 round_record 的 audio_segments_done（那是原始 round 的字段），
                        # 用专门的 speedup 字段，避免混淆 UI
                        round_record["speedup_segments_done"] = done
                        round_record["speedup_segments_total"] = total
                        self._emit_duration_round(task_id, round_index, "speedup_progress", round_record)

                    speedup_result = regenerate_full_audio_with_speed(
                        result["segments"],
                        voice["elevenlabs_voice_id"],
                        task_dir,
                        variant=f"round_{round_index}",
                        speed=speed,
                        elevenlabs_api_key=elevenlabs_api_key,
                        model_id=tts_model_id,
                        language_code=tts_language_code,
                        on_segment_done=_on_speedup_seg_done,
                    )
                    speedup_audio_path = speedup_result["full_audio_path"]
                    speedup_duration = _get_audio_duration(speedup_audio_path)
                    round_record["speedup_audio_path"] = (
                        os.path.relpath(speedup_audio_path, task_dir)
                    )
                    round_record["speedup_post_duration"] = speedup_duration
                    round_record["speedup_chars_used"] = sum(
                        len((s.get("tts_text") or "")) for s in result["segments"]
                    )
                except Exception as exc:
                    log.exception(
                        "[task %s] speedup regeneration failed at round %d, falling back",
                        task_id, round_index,
                    )
                    speedup_failed_reason = str(exc)[:500]
                    round_record["speedup_failed_reason"] = speedup_failed_reason

                # Decide which audio is the final adopted one.
                if speedup_audio_path is None:
                    # Fallback：原始音频 + atempo
                    final_audio_path = self._maybe_tempo_align(
                        audio_path=result["full_audio_path"],
                        audio_duration=audio_duration,
                        video_duration=video_duration,
                        task_dir=task_dir, variant=variant,
                        round_record=round_record, task_id=task_id,
                    )
                    round_record["final_reason"] = "speedup_failed_fallback"
                    round_record["speedup_hit_final"] = False
                else:
                    hit_final = (
                        final_target_lo <= speedup_duration <= final_target_hi
                    )
                    round_record["speedup_hit_final"] = hit_final
                    if hit_final:
                        # 命中：再走一次 atempo 兜底精确对齐（误差 ≤ 5% 时拉伸到精确等长）
                        final_audio_path = self._maybe_tempo_align(
                            audio_path=speedup_audio_path,
                            audio_duration=speedup_duration,
                            video_duration=video_duration,
                            task_dir=task_dir, variant=f"{variant}_speedup",
                            round_record=round_record, task_id=task_id,
                        )
                        round_record["final_reason"] = "speedup_converged"
                    else:
                        # 未命中 final：仍然终结，对变速产物跑 atempo
                        final_audio_path = self._maybe_tempo_align(
                            audio_path=speedup_audio_path,
                            audio_duration=speedup_duration,
                            video_duration=video_duration,
                            task_dir=task_dir, variant=f"{variant}_speedup",
                            round_record=round_record, task_id=task_id,
                        )
                        round_record["final_reason"] = "speedup_then_atempo"

                # 同步 AI 评估（仅当变速成功有 audio_post 才跑）
                eval_id = None
                if speedup_audio_path is not None:
                    try:
                        from appcore import tts_speedup_eval
                        eval_id = tts_speedup_eval.run_evaluation(
                            task_id=task_id,
                            round_index=round_index,
                            language=target_language_label or "",
                            video_duration=video_duration,
                            audio_pre_path=result["full_audio_path"],
                            audio_pre_duration=audio_duration,
                            audio_post_path=speedup_audio_path,
                            audio_post_duration=speedup_duration,
                            speed_ratio=speed,
                            hit_final_range=bool(
                                round_record.get("speedup_hit_final")
                            ),
                            user_id=self.user_id,
                        )
                    except Exception:
                        log.exception(
                            "[task %s] tts_speedup_eval.run_evaluation raised; ignoring",
                            task_id,
                        )
                round_record["speedup_eval_id"] = eval_id

                round_products[-1]["tts_audio_path"] = final_audio_path
                rounds[-1] = round_record
                if round_record["final_reason"] == "speedup_converged":
                    final_distance = 0.0
                else:
                    # speedup_failed_fallback or speedup_then_atempo
                    measured_duration = speedup_duration if speedup_duration is not None else audio_duration
                    final_distance = round(_distance_to_duration_range(
                        measured_duration, final_target_lo, final_target_hi,
                    ), 3)
                round_record["final_distance"] = final_distance
                task_state.update(
                    task_id,
                    tts_duration_rounds=rounds,
                    tts_duration_status="converged",
                    tts_final_round=round_index,
                    tts_final_reason=round_record["final_reason"],
                    tts_final_distance=final_distance,
                )
                self._emit_duration_round(
                    task_id, round_index, "speedup_done", round_record,
                )
                tts_generation_stats.finalize(
                    task_id=task_id,
                    task=task_state.get(task_id) or {},
                    rounds=rounds,
                )
                return {
                    "localized_translation": localized_translation,
                    "tts_script": tts_script,
                    "tts_audio_path": final_audio_path,
                    "tts_segments": (
                        speedup_result["segments"] if speedup_audio_path is not None
                        else result["segments"]
                    ),
                    "rounds": rounds,
                    "round_products": round_products,
                    "final_round": round_index,
                }
            # ============= 变速短路分支结束 =============

            # Note: do NOT update `prev_localized` — every rewrite uses the initial.
            last_audio_duration = audio_duration
            last_word_count = word_count

        # MAX_ROUNDS rounds completed without landing in [video-1, video+2].
        # Pick the round whose audio_duration is closest to the final target range.
        import appcore.task_state as task_state
        eligible_indices = [
            i for i, rec in enumerate(rounds)
            if rec.get("audio_duration") is not None
            and i < len(round_products)
            and round_products[i]
        ]
        if not eligible_indices:
            raise RuntimeError("No TTS audio round was generated")
        best_i = min(
            eligible_indices,
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
        # ffmpeg atempo 兜底：误差 ≤5% 时拉伸/压缩到精确等于视频长度
        final_best_audio = self._maybe_tempo_align(
            audio_path=best_product["tts_audio_path"],
            audio_duration=best_record["audio_duration"],
            video_duration=video_duration,
            task_dir=task_dir, variant=variant,
            round_record=best_record, task_id=task_id,
        )
        best_product["tts_audio_path"] = final_best_audio
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
        tts_generation_stats.finalize(
            task_id=task_id,
            task=task_state.get(task_id) or {},
            rounds=rounds,
        )
        return {
            "localized_translation": best_product["localized_translation"],
            "tts_script": best_product["tts_script"],
            "tts_audio_path": best_product["tts_audio_path"],
            "tts_segments": best_product["tts_segments"],
            "rounds": rounds,
            "round_products": round_products,
            "final_round": best_i + 1,
        }

    def _maybe_tempo_align(
        self, *, audio_path: str, audio_duration: float, video_duration: float,
        task_dir: str, variant: str, round_record: dict, task_id: str,
    ) -> str:
        """误差在 ±5% 内时跑一次 ffmpeg atempo 兜底，把音频精确对齐 video_duration。
        把变速过程的 ratio / pre / post / new_delta 写进 round_record，前端 TTS 卡片
        读这些字段渲染日志行。失败 / 不需要时返回原 audio_path。"""
        from appcore.runtime._helpers import _apply_audio_tempo_fallback as _do_tempo
        import os
        out_path = os.path.join(task_dir, f"tts_full.tempo.{variant}.mp3")
        info = _do_tempo(
            audio_path=audio_path, audio_duration=audio_duration,
            video_duration=video_duration, output_path=out_path,
        )
        if info is None:
            log.info(
                "[task %s] tempo fallback skipped (audio=%.3fs video=%.3fs delta=%.3fs)",
                task_id, audio_duration, video_duration, audio_duration - video_duration,
            )
            round_record["tempo_applied"] = False
            return audio_path
        round_record["tempo_applied"] = True
        round_record["tempo_ratio"] = info["ratio"]
        round_record["tempo_pre_duration"] = info["pre_duration"]
        round_record["tempo_post_duration"] = info["post_duration"]
        round_record["tempo_new_delta"] = info["new_delta"]
        log.info(
            "[task %s] tempo fallback applied: ratio=%.4f, %.3fs → %.3fs (target %.3fs, new_delta=%+.3fs)",
            task_id, info["ratio"], info["pre_duration"], info["post_duration"],
            video_duration, info["new_delta"],
        )
        return info["new_audio_path"]

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
        """Drop trailing blocks to land in [video-1, video+2].

        Policy:
          - If total audio is within the final upper bound, skip.
          - Otherwise, drop blocks from the tail one at a time:
              * If a drop lands duration in [video-1, video+2] → stop, adopt it.
              * If a drop overshoots below video-1 → stop, pick the candidate
                (any drop state with duration <= video+2) whose duration is
                closest to [video-1, video+2]. Never return a state above the
                final upper bound.
              * Else (still > video) → keep dropping.
          - If we run out of blocks, raise.

        Returns dict with keys:
          - skipped: True if total is within the final upper bound
          - audio_path, tts_script, localized_translation, tts_segments
          - removed_count, removed_duration, final_duration
        """
        import subprocess

        total = sum(float(s.get("tts_duration", 0.0) or 0.0) for s in tts_segments)
        final_target_lo, final_target_hi = _tts_final_target_range(video_duration)
        if total <= final_target_hi:
            return {"skipped": True}

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

        if _is_av_pipeline_task(task):
            voice_id = str(task.get("selected_voice_id") or task.get("voice_id") or "").strip()
            if voice_id:
                return {
                    "id": None,
                    "elevenlabs_voice_id": voice_id,
                    "name": task.get("selected_voice_name") or voice_id,
                }
            from appcore.video_translate_defaults import resolve_default_voice

            default_voice_id = resolve_default_voice(_av_target_lang(task), user_id=self.user_id)
            if default_voice_id:
                return {"id": None, "elevenlabs_voice_id": default_voice_id, "name": "Default"}

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
        if _is_av_pipeline_task(task_state.get(task_id)):
            out = []
            for name, fn in steps:
                out.append((name, fn))
                if name == "asr":
                    out.append(("asr_normalize", lambda: self._step_av_asr_normalize(task_id)))
                    out.append(("voice_match", lambda: self._step_av_voice_match(task_id)))
            return out
        return steps

    def _step_av_asr_normalize(self, task_id: str) -> None:
        task = task_state.get(task_id) or {}
        if self._skip_original_video_passthrough_step(task_id, "asr_normalize", task=task):
            return
        task_state.update(
            task_id,
            utterances_en=None,
            asr_normalize_artifact=None,
            detected_source_language=task.get("source_language") or None,
        )
        self._set_step(task_id, "asr_normalize", "done", "AV Sync 保留原 ASR 分段，直接进入音色匹配")

    def _step_av_voice_match(self, task_id: str) -> None:
        task = task_state.get(task_id) or {}
        if self._skip_original_video_passthrough_step(task_id, "voice_match", task=task):
            return

        lang = _av_target_lang(task)
        utterances = task.get("utterances") or []
        video_path = task.get("video_path")
        default_voice_id = None
        try:
            from appcore.video_translate_defaults import resolve_default_voice

            default_voice_id = resolve_default_voice(lang, user_id=self.user_id)
        except Exception:
            log.exception("resolve default voice failed for AV task %s", task_id)

        self._set_step(task_id, "voice_match", "running", f"{lang.upper()} 音色库加载中...")

        candidates: list = []
        query_embedding_b64 = None
        if utterances and video_path:
            try:
                import base64
                from pipeline.voice_embedding import embed_audio_file, serialize_embedding
                from pipeline.voice_match import extract_sample_from_utterances, match_candidates

                sample_out_dir = task.get("task_dir") or os.path.dirname(os.path.abspath(video_path)) or "."
                clip = extract_sample_from_utterances(
                    video_path,
                    utterances,
                    out_dir=sample_out_dir,
                    min_duration=8.0,
                )
                vec = embed_audio_file(clip)
                candidates = match_candidates(
                    vec,
                    language=lang,
                    top_k=10,
                    exclude_voice_ids={default_voice_id} if default_voice_id else None,
                ) or []
                for candidate in candidates:
                    candidate["similarity"] = float(candidate.get("similarity", 0.0))
                query_embedding_b64 = base64.b64encode(serialize_embedding(vec)).decode("ascii")
            except Exception as exc:
                log.exception("AV voice match failed for %s: %s", task_id, exc)
                candidates = []
                query_embedding_b64 = None

        fallback = None if candidates else default_voice_id
        task_state.update(
            task_id,
            target_lang=lang,
            voice_match_candidates=candidates,
            voice_match_fallback_voice_id=fallback,
            voice_match_query_embedding=query_embedding_b64,
        )
        task_state.set_current_review_step(task_id, "voice_match")
        self._set_step(task_id, "voice_match", "waiting", f"{lang.upper()} 音色库已就绪，请选择 TTS 音色")
        self._emit(
            task_id,
            EVT_VOICE_MATCH_READY,
            {"candidates": candidates, "fallback_voice_id": fallback, "target_lang": lang},
        )

    def _run(self, task_id: str, start_step: str = "extract") -> None:
        # Make sure the source video is present locally before any step runs.
        # Missing sources must be materialized by migration or re-uploaded.
        try:
            from appcore.source_video import ensure_local_source_video
            ensure_local_source_video(task_id)
        except Exception as exc:
            logger.exception("[task %s] source video ensure failed: %s", task_id, exc)
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
                # Cooperative cancellation: graceful-shutdown checkpoint
                # before each step so the worker can drop everything when
                # systemd / Gunicorn hands us SIGTERM.
                throw_if_cancel_requested(f"pipeline step={step_name}")
                step_fn()
                current = task_state.get(task_id) or {}
                # 每个 step 成功完成都把 retry 计数清零，让下一个 step 的失败有
                # 完整的 retry budget，避免不同 step 各失败 1 次就累加触顶。
                if int(current.get("_failure_count") or 0) > 0:
                    task_state.update(task_id, _failure_count=0)
                if current.get("steps", {}).get(step_name) == "waiting":
                    return
                if current.get("status") in {"failed", "error", "done"}:
                    return
        except OperationCancelled as exc:
            current_step = (task_state.get(task_id) or {}).get("current_step") or "?"
            log.warning(
                "[task %s] pipeline cancelled at step=%s reason=%s",
                task_id, current_step, exc,
            )
            self._mark_pipeline_interrupted(task_id, str(exc))
            # Re-raise so start_tracked_thread's outer handler logs and
            # cleans up _active_tasks; it will not show a traceback.
            raise
        except Exception as exc:
            current_step = (task_state.get(task_id) or {}).get("current_step") or start_step or "?"
            logger.exception(
                "[task %s] pipeline failed at step=%s: %s", task_id, current_step, exc,
            )
            # Auto-retry budget：单点失败不直接阻塞整条流水线，先指数退避自愈几次。
            # 已失败次数累加到 task_state，超过 _TASK_AUTO_RETRY_MAX 才标 error。
            current_task = task_state.get(task_id) or {}
            failure_count = int(current_task.get("_failure_count") or 0) + 1
            task_state.update(task_id, _failure_count=failure_count)
            if failure_count < _TASK_AUTO_RETRY_MAX:
                delay_idx = min(failure_count - 1, len(_TASK_AUTO_RETRY_DELAYS) - 1)
                delay = _TASK_AUTO_RETRY_DELAYS[delay_idx]
                resume_from = current_step if current_step in _ALL_STEP_NAMES else start_step
                log.warning(
                    "[task %s] auto-retry %d/%d after %ds (resume_from=%s): %s",
                    task_id, failure_count, _TASK_AUTO_RETRY_MAX,
                    delay, resume_from, exc,
                )
                try:
                    time.sleep(delay)
                except Exception:
                    pass
                # 重置 step 状态让 _run 主循环知道要从 resume_from 重新跑
                try:
                    started_marker = False
                    for step_name, _fn in steps:
                        if step_name == resume_from:
                            started_marker = True
                        if started_marker:
                            self._set_step(task_id, step_name, "pending")
                except Exception:
                    pass
                return self._run(task_id, start_step=resume_from)
            log.error(
                "[task %s] auto-retry exhausted (%d/%d), marking task failed",
                task_id, failure_count, _TASK_AUTO_RETRY_MAX,
            )
            task_state.update(task_id, _failure_count=0, status="error", error=str(exc))
            task_state.set_expires_at(task_id, self.project_type)
            self._emit(task_id, EVT_PIPELINE_ERROR, {"error": str(exc)})

    def _mark_pipeline_interrupted(self, task_id: str, reason: str) -> None:
        """Record cooperative cancellation in task_state.

        Mark the in-flight step (running / queued) AND any not-yet-started
        step (pending) as ``interrupted``; flip the task status so the UI
        surfaces a "service restart" explanation instead of a generic
        error. Steps already in a terminal state (done / failed / error)
        are left alone.
        """
        task = task_state.get(task_id) or {}
        steps = dict(task.get("steps") or {})
        step_messages = dict(task.get("step_messages") or {})
        changed = False
        for step, status in list(steps.items()):
            if status in {"queued", "running", "pending"}:
                steps[step] = "interrupted"
                step_messages[step] = "service restart in progress, please retry"
                changed = True
        update_kwargs: dict = {
            "status": "interrupted",
            "error": "service restart in progress, please retry",
        }
        if changed:
            update_kwargs["steps"] = steps
            update_kwargs["step_messages"] = step_messages
        task_state.update(task_id, **update_kwargs)
        try:
            self._emit(task_id, EVT_PIPELINE_ERROR, {
                "error": f"cancelled: {reason}",
                "cancelled": True,
            })
        except Exception:
            log.warning("emit pipeline_error during cancellation failed", exc_info=True)

    def _skip_original_video_passthrough_step(
        self,
        task_id: str,
        step_name: str,
        *,
        task: dict | None = None,
        lang_display: str = "",
    ) -> bool:
        task = task or task_state.get(task_id) or {}
        if not _is_original_video_passthrough(task):
            return False

        source_chars = int(task.get("media_passthrough_source_chars") or 0)
        reason = str(task.get("media_passthrough_reason") or "short_asr")
        source_hint = f"源 ASR {source_chars} 字符" if source_chars else "源 ASR 过短"
        lang_prefix = f"{lang_display}配音" if lang_display else "配音"
        message_map = {
            "alignment": f"{source_hint}，已跳过分段处理并保留原视频",
            "voice_match": f"{source_hint}，已跳过音色选择并保留原视频",
            "translate": f"{source_hint}，已跳过翻译文案生成并保留原视频",
            "tts": f"{source_hint}，已跳过{lang_prefix}并保留原视频",
            "subtitle": f"{source_hint}，已跳过字幕生成并保留原视频",
            "analysis": f"{source_hint}，已跳过 AI 分析并保留原视频",
        }
        update_kwargs: dict = {}
        if step_name == "voice_match":
            task_state.set_current_review_step(task_id, "")
            update_kwargs.update(
                voice_match_candidates=[],
                voice_match_fallback_voice_id=None,
                voice_match_query_embedding=None,
            )
        elif step_name == "alignment":
            task_state.set_current_review_step(task_id, "")
            update_kwargs["_alignment_confirmed"] = True
        elif step_name == "translate":
            task_state.set_current_review_step(task_id, "")
        elif step_name == "tts":
            update_kwargs.update(
                tts_duration_rounds=[],
                tts_duration_status="source_video_passthrough",
                tts_final_round=0,
                tts_final_reason="source_video_passthrough",
                tts_skip_reason=reason,
                tts_skip_source_chars=source_chars,
            )
        elif step_name == "subtitle":
            update_kwargs.update(
                english_asr_result={},
                corrected_subtitle={"chunks": [], "srt_content": ""},
                srt_path="",
            )
        if update_kwargs:
            task_state.update(task_id, **update_kwargs)
        self._set_step(task_id, step_name, "done", message_map.get(step_name, f"{source_hint}，已跳过该步骤"))
        return True

    def _complete_original_video_passthrough(self, task_id: str, video_path: str, task_dir: str) -> dict:
        import shutil

        task = task_state.get(task_id) or {}
        if not _is_original_video_passthrough(task):
            return {}

        skip_steps = ["alignment", "translate", "tts", "subtitle"]
        if self.project_type in {"multi_translate", "ja_translate"} or "voice_match" in (task.get("steps") or {}):
            skip_steps.insert(1, "voice_match")
        if self.include_analysis_in_main_flow:
            skip_steps.append("analysis")
        for step_name in skip_steps:
            self._skip_original_video_passthrough_step(task_id, step_name, task=task_state.get(task_id) or task)

        self._set_step(task_id, "compose", "running", "识别结果过短，正在直接复用原视频...")
        variant = "av" if _is_av_pipeline_task(task) else "normal"
        task = task_state.get(task_id) or task
        variants = dict(task.get("variants", {}))
        variant_state = dict(variants.get(variant, {}))
        base_name = os.path.splitext(os.path.basename(video_path))[0] or "source"
        hard_output = os.path.join(task_dir, f"{base_name}_hard.{variant}.mp4")
        soft_output = os.path.join(task_dir, f"{base_name}_soft.{variant}.mp4")
        shutil.copy2(video_path, hard_output)
        if self.include_soft_video:
            shutil.copy2(video_path, soft_output)
        else:
            soft_output = None

        result = {"soft_video": soft_output, "hard_video": hard_output, "srt": ""}
        exports: dict = {}
        variant_state["result"] = result
        variant_state["exports"] = exports
        variants[variant] = variant_state
        task_state.update(task_id, variants=variants, result=result, exports=exports, status="done", error="")
        if soft_output:
            task_state.set_preview_file(task_id, "soft_video", soft_output)
        task_state.set_preview_file(task_id, "hard_video", hard_output)
        task_state.set_artifact(task_id, "compose", build_compose_artifact())
        self._set_step(task_id, "compose", "done", "识别结果过短，已直接复用原视频")
        task_state.set_expires_at(task_id, self.project_type)
        task_state.set_artifact(task_id, "export", build_export_artifact("", archive_url=""))
        self._set_step(task_id, "export", "done", "音乐视频直通完成，已跳过 CapCut 导出")
        self._emit(task_id, EVT_PIPELINE_DONE, {"task_id": task_id, "exports": {variant: exports}})
        _skip_legacy_artifact_upload(task_state.get(task_id) or {}, task_id)
        return result

    def _step_extract(self, task_id: str, video_path: str, task_dir: str) -> None:
        self._set_step(task_id, "extract", "running", "正在提取音频...")
        from pipeline.extract import extract_audio

        audio_path = extract_audio(video_path, task_dir)
        task_state.update(task_id, audio_path=audio_path)
        task_state.set_preview_file(task_id, "audio_extract", audio_path)
        task_state.set_artifact(task_id, "extract", build_extract_artifact())
        self._set_step(task_id, "extract", "done", "音频提取完成")

    def _step_separate(self, task_id: str, task_dir: str) -> None:
        """人声分离 + 基准响度测量。

        - 总开关关掉 / API URL 为空 → 立刻 done，``task["separation"].status="disabled"``，
          后续 step 走旧逻辑（不做响度匹配、不混背景音）。
        - 调用同步阻塞，可能持续 10s ~ 5min（API 端 GPU 排队 + 推理）。
          调用前先写 placeholder ``status="running"`` + ``started_at_epoch`` 到
          task_state，让前端轮询能立刻拿到时间戳，UI 据此 setInterval 刷新 elapsed。
        - 任何失败（API 不可达 / 超时 / ZIP 损坏 / vocals 几乎静音）都标记降级
          状态但不让本 step 失败，后续主任务继续走旧逻辑。
        """
        import time as _time

        from pipeline import audio_separation as sep

        task = task_state.get(task_id) or {}
        audio_path = task.get("audio_path")
        if not audio_path:
            self._set_step(task_id, "separate", "done", "未检测到 audio_path，跳过")
            return

        settings = sep.load_settings()
        if not settings.is_runnable:
            placeholder = sep.disabled_result("总开关未启用 / API URL 为空")
            task_state.update(task_id, separation=placeholder)
            self._set_step(task_id, "separate", "done", "人声分离未启用（保持旧逻辑）")
            return

        # 写 placeholder running 状态，让前端立即看到 started_at_epoch / timeout_seconds
        # 并据此 setInterval(10s) 刷新 "已等待 X s"。
        started = _time.time()
        placeholder = {
            "status": "running",
            "model": settings.preset,
            "api_url": settings.api_url,
            "started_at_epoch": started,
            "finished_at_epoch": None,
            "elapsed_seconds": None,
            "timeout_seconds": settings.task_timeout,
            "vocals_path": None,
            "accompaniment_path": None,
            "vocals_lufs": None,
            "error": None,
            "error_kind": None,
        }
        task_state.update(task_id, separation=placeholder)
        self._set_step(
            task_id, "separate", "running",
            f"调上游 GPU 分离（preset={settings.preset}，最长 {settings.task_timeout:.0f}s）...",
            model_tag=f"audio-separator · {settings.preset}",
        )

        out_dir = os.path.join(task_dir, "separation")
        result = sep.run_separation(
            audio_path=audio_path,
            output_dir=out_dir,
            api_url=settings.api_url,
            preset=settings.preset,
            task_timeout=settings.task_timeout,
        )
        task_state.update(task_id, separation=result)

        status = result["status"]
        if status == "done":
            msg = (
                f"分离完成（耗时 {result['elapsed_seconds']:.1f}s，"
                f"L₀={result['vocals_lufs']:.1f} LUFS）"
            )
        elif status == "timeout":
            msg = (
                f"已超时（>{result['timeout_seconds']:.0f}s 未完成），"
                "自动降级走旧逻辑"
            )
        elif status == "unavailable":
            msg = "分离 API 不可达，已降级走旧逻辑"
        elif status == "silence":
            msg = "分离结果几乎静音（可能纯人声/纯音乐），已降级走旧逻辑"
        else:  # failed
            err = (result.get("error") or "")[:80]
            msg = f"分离失败已降级：{err}"

        # status 始终用 "done"——分离失败不阻塞主任务，message 标降级原因。
        self._set_step(task_id, "separate", "done", msg)

    def _step_loudness_match(self, task_id: str, task_dir: str) -> None:
        """把每个 variant 的 TTS 音频用 EBU R128 二阶段归一化到 vocals_lufs L₀。

        完整链路：``vocals_lufs (L₀)`` 来自 :meth:`_step_separate` 测得的原视频
        人声基准，目标偏差 ≤ ±3% LUFS（约 ±0.7 LU）。直接 in-place 替换原 mp3
        文件，下游 :meth:`_step_compose` / :meth:`_step_subtitle` 不感知这层。

        分离未启用 / 失败时直接 skipped；归一化失败不阻塞主任务。
        """
        from pipeline import audio_separation as sep

        task = task_state.get(task_id) or {}
        separation = task.get("separation") or {}

        if not sep.is_usable(separation):
            status_word = separation.get("status") or "disabled"
            msg = {
                "disabled":    "人声分离未启用，跳过响度匹配",
                "unavailable": "分离 API 不可达，跳过响度匹配",
                "timeout":     "分离超时，跳过响度匹配",
                "failed":      "分离失败，跳过响度匹配",
                "silence":     "分离结果几乎静音，跳过响度匹配",
            }.get(status_word, "无可用分离结果，跳过响度匹配")
            self._set_step(task_id, "loudness_match", "done", msg)
            return

        target = float(separation["vocals_lufs"])
        self._set_step(
            task_id, "loudness_match", "running",
            f"匹配 TTS 响度到 L₀={target:.1f} LUFS（EBU R128 二阶段）...",
        )

        import shutil

        from appcore.audio_loudness import normalize_to_lufs

        variants = dict(task.get("variants") or {})
        summaries: list[dict] = []
        for variant_name, variant_state in list(variants.items()):
            if not isinstance(variant_state, dict):
                continue
            audio_path = variant_state.get("tts_audio_path")
            if not audio_path or not os.path.isfile(audio_path):
                continue
            tmp_path = audio_path + ".loudnorm.mp3"
            try:
                result = normalize_to_lufs(
                    audio_path, tmp_path, target_lufs=target,
                )
            except Exception as exc:  # noqa: BLE001 — 不阻塞主流程
                log.warning(
                    "[loudness_match] task=%s variant=%s failed: %s",
                    task_id, variant_name, exc,
                )
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                continue
            shutil.move(tmp_path, audio_path)
            summaries.append({
                "variant": variant_name,
                "input_lufs": result.input_lufs,
                "output_lufs": result.output_lufs,
                "deviation_lu": result.deviation_lu,
                "deviation_pct": result.deviation_pct,
                "converged": result.converged,
            })

        separation = dict(separation)
        separation["tts_loudness"] = {
            "target_lufs": target,
            "variants": summaries,
        }
        task_state.update(task_id, separation=separation)

        if summaries:
            primary = summaries[0]
            msg = (
                f"响度匹配完成：{primary['output_lufs']:.1f} LUFS，"
                f"偏差 {primary['deviation_pct']:.2f}%（"
                f"{'✓ 在 ±3% 内' if primary['converged'] else '⚠ 超出 ±3%'}）"
            )
        else:
            msg = "无 TTS 音频，跳过响度匹配"
        self._set_step(task_id, "loudness_match", "done", msg)

    def _step_asr(self, task_id: str, task_dir: str) -> None:
        task = task_state.get(task_id)
        audio_path = task["audio_path"]
        from pipeline.extract import get_video_duration
        from appcore import asr_router
        from pipeline.lang_labels import lang_label

        source_language = task.get("source_language") or "zh"
        # 先解析 adapter 拿元数据生成 model_tag，让 step 卡片在 running 状态就能
        # 显示当前用的是哪个 ASR provider；transcribe 内部会再次解析（廉价，instance 级）。
        _adapter, _ = asr_router.resolve_adapter("asr_main", source_language)
        _asr_model_tag = f"{_adapter.display_name} · {_adapter.model_id}"
        self._set_step(
            task_id,
            "asr",
            "running",
            f"正在识别{lang_label(source_language, in_chinese=True)}语音...",
            model_tag=_asr_model_tag,
        )

        result = asr_router.transcribe(
            audio_path, source_language=source_language, stage="asr_main",
        )
        utterances = result["utterances"]
        asr_provider = result["provider_code"]
        asr_model = result["model_id"]

        passthrough = _resolve_original_video_passthrough(utterances)
        source_full_text = passthrough["source_full_text"]
        task_state.update(task_id, utterances=utterances, source_full_text=source_full_text)
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
            provider=asr_provider,
            model=asr_model,
            request_units=_seconds_to_request_units(audio_duration_seconds),
            units_type="seconds",
            audio_duration_seconds=audio_duration_seconds,
            success=True,
            request_payload={
                "type": "asr",
                "provider": asr_provider,
                "audio_url": "",
                "audio_path": audio_path,
            },
            response_payload={
                "utterances": utterances,
                "source_full_text": source_full_text,
                "audio_duration_seconds": audio_duration_seconds,
            },
        )

        if passthrough["enabled"]:
            task_state.update(
                task_id,
                source_full_text_zh=source_full_text,
                media_passthrough_mode="original_video",
                media_passthrough_reason=passthrough["reason"],
                media_passthrough_source_chars=passthrough["source_chars"],
            )
            if passthrough["reason"] == "no_asr":
                message = "未检测到有效语音，已按音乐视频直通处理"
            else:
                message = "识别结果少于 50 个字符，已按音乐视频直通处理"
            self._set_step(task_id, "asr", "done", message)
            self._emit(task_id, EVT_ASR_RESULT, {"segments": utterances})
            self._complete_original_video_passthrough(task_id, task["video_path"], task_dir)
            return

        # 这一轮 ASR 不再触发 passthrough（utterances 够长），清掉之前留下的
        # passthrough flag。否则下游 voice_match / translate / tts / subtitle
        # 仍按"音乐视频直通"短路，整个翻译流程跑空。
        task_state.update(
            task_id,
            media_passthrough_mode=None,
            media_passthrough_reason=None,
            media_passthrough_source_chars=None,
        )

        if not utterances:
            self._set_step(task_id, "asr", "done", "未检测到语音内容，可能是纯音乐/音效视频")
            self._emit(task_id, EVT_ASR_RESULT, {"segments": []})
            raise RuntimeError("未检测到语音内容。该视频可能是纯音乐或音效背景视频，无法进行语音翻译。")

        self._set_step(task_id, "asr", "done", f"识别完成，共 {len(utterances)} 段")
        self._emit(task_id, EVT_ASR_RESULT, {"segments": utterances})

    def _step_alignment(self, task_id: str, video_path: str, task_dir: str) -> None:
        task = task_state.get(task_id)
        if self._skip_original_video_passthrough_step(task_id, "alignment", task=task):
            return
        self._set_step(task_id, "alignment", "running", "正在分析镜头并生成分段建议...")
        from pipeline.alignment import compile_alignment, detect_scene_cuts
        from pipeline.voice_library import get_voice_library

        scene_cuts = detect_scene_cuts(video_path)
        utterances_for_alignment = task.get("utterances_en") or task.get("utterances", [])
        alignment = compile_alignment(utterances_for_alignment, scene_cuts=scene_cuts)
        suggested_voice = get_voice_library().recommend_voice(
            self.user_id,
            " ".join(item.get("text", "") for item in utterances_for_alignment)
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
        if _is_av_pipeline_task(task):
            run_av_localize(task_id, runner=self, variant="av")
            return
        if self._skip_original_video_passthrough_step(task_id, "translate", task=task):
            return
        task_dir = task["task_dir"]
        from pipeline.localization import build_source_full_text_zh
        from pipeline.translate import generate_localized_translation

        provider = _resolve_task_translate_provider(self.user_id, task)
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
            use_case="video_translate.localize",
            project_id=task_id,
            checkpoint_key="translate.initial",
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
            request_payload=_llm_request_payload(
                localized_translation,
                provider,
                "video_translate.localize",
                messages=initial_messages,
            ),
            response_payload=_llm_response_payload(localized_translation),
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
        import appcore.task_state as task_state

        task = task_state.get(task_id)
        if _is_av_pipeline_task(task):
            if (task.get("steps") or {}).get("tts") == "done":
                return
            run_av_localize(task_id, runner=self, variant="av")
            return
        if self._skip_original_video_passthrough_step(
            task_id,
            "tts",
            task=task,
            lang_display=_lang_display(self._get_tts_target_language_label(task)),
        ):
            return
        loc_mod = self._get_localization_module(task)
        target_language_label = self._get_tts_target_language_label(task)
        tts_model_id = self._get_tts_model_id(task)
        tts_language_code = self._get_tts_language_code(task)

        lang_display = _lang_display(target_language_label)

        from appcore.api_keys import resolve_key
        from pipeline.extract import get_video_duration

        provider = _resolve_task_translate_provider(self.user_id, task)
        from pipeline.translate import get_model_display_name as _get_model_name
        _tts_model_tag = f"{provider} · {_get_model_name(provider, self.user_id)}"
        self._set_step(task_id, "tts", "running", f"正在生成{lang_display}配音...", model_tag=_tts_model_tag)
        self._emit_substep_msg(task_id, "tts",
            f"正在生成{lang_display}配音 · 加载配音模板")
        variants = dict(task.get("variants", {}))
        source_full_text = task.get("source_full_text_zh") or task.get("source_full_text", "")
        source_language = task.get("source_language", "zh")
        video_duration = get_video_duration(task["video_path"])
        final_target_lo, final_target_hi = _tts_final_target_range(video_duration)
        variant_order = [name for name in variants.keys() if name != "normal"]
        if "normal" in variants:
            variant_order.append("normal")
        elif not variant_order:
            variant_order = ["normal"]
            variants["normal"] = {}

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

        from pipeline.tts import _get_audio_duration
        from pipeline.timeline import build_timeline_manifest
        import shutil

        elevenlabs_api_key = resolve_key(self.user_id, "elevenlabs", "ELEVENLABS_API_KEY")
        voice = self._resolve_voice(task, loc_mod)

        variant_results: dict[str, dict] = {}
        for variant in variant_order:
            variant_state = dict(variants.get(variant, {}))
            initial_localized = variant_state.get("localized_translation", {}) or (
                task.get("localized_translation", {}) if variant == "normal" else {}
            )
            if not initial_localized:
                continue

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
                target_language_label=target_language_label,
                tts_model_id=tts_model_id,
                tts_language_code=tts_language_code,
            )

            final_round = loop_result["final_round"]
            pre_trim_duration = _get_audio_duration(loop_result["tts_audio_path"])
            final_audio_path = os.path.join(task_dir, f"tts_full.{variant}.mp3")
            if pre_trim_duration > final_target_hi:
                trim_record = {
                    "pre_trim_duration": pre_trim_duration,
                    "video_duration": video_duration,
                    "duration_lo": final_target_lo,
                    "duration_hi": final_target_hi,
                    "message": (
                        f"音频 {pre_trim_duration:.1f}s 超过目标上限 {final_target_hi:.1f}s，"
                        "正在直接截断到目标上限..."
                    ),
                }
                self._emit_duration_round(task_id, final_round, "truncate_audio", trim_record)
                trim_result = self._truncate_audio_to_duration(
                    input_audio_path=loop_result["tts_audio_path"],
                    output_audio_path=final_audio_path,
                    duration=final_target_hi,
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
                        "duration_lo": final_target_lo,
                        "duration_hi": final_target_hi,
                        "message": (
                            f"截断完成：最终音频 {trim_result['final_duration']:.1f}s，"
                            f"目标上限 {final_target_hi:.1f}s"
                        ),
                    }
                    self._emit_duration_round(task_id, final_round, "truncated", trimmed_record)

            if os.path.abspath(loop_result["tts_audio_path"]) != os.path.abspath(final_audio_path):
                shutil.copy2(loop_result["tts_audio_path"], final_audio_path)

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
            variant_state.setdefault("preview_files", {})["tts_full_audio"] = final_audio_path
            variant_state.setdefault("artifacts", {})["tts"] = build_tts_artifact(
                loop_result["tts_script"],
                loop_result["tts_segments"],
                duration_rounds=loop_result["rounds"],
            )
            variants[variant] = variant_state

            _save_json(task_dir, f"tts_script.{variant}.json", loop_result["tts_script"])
            _save_json(task_dir, f"tts_result.{variant}.json", loop_result["tts_segments"])
            _save_json(task_dir, f"timeline_manifest.{variant}.json", timeline_manifest)
            _save_json(task_dir, f"localized_translation.{variant}.json", loop_result["localized_translation"])

            variant_results[variant] = {
                "loop_result": loop_result,
                "timeline_manifest": timeline_manifest,
                "final_audio_path": final_audio_path,
            }

        if not variant_results:
            raise ValueError("No localized translation available for TTS generation")

        primary_variant = "normal" if "normal" in variant_results else next(iter(variant_results))
        primary_result = variant_results[primary_variant]
        primary_loop_result = primary_result["loop_result"]
        primary_timeline_manifest = primary_result["timeline_manifest"]
        primary_audio_path = primary_result["final_audio_path"]

        task_state.set_preview_file(task_id, "tts_full_audio", primary_audio_path)
        _save_json(task_dir, "tts_duration_rounds.json", primary_loop_result["rounds"])

        task_state.update(
            task_id,
            variants=variants,
            segments=primary_loop_result["tts_segments"],
            tts_script=primary_loop_result["tts_script"],
            tts_audio_path=primary_audio_path,
            voice_id=voice.get("id"),
            timeline_manifest=primary_timeline_manifest,
            localized_translation=primary_loop_result["localized_translation"],
            tts_duration_rounds=primary_loop_result["rounds"],
        )

        task_state.set_artifact(
            task_id,
            "tts",
            build_tts_artifact(
                primary_loop_result["tts_script"],
                primary_loop_result["tts_segments"],
                duration_rounds=primary_loop_result["rounds"],
            ),
        )

        from appcore.events import EVT_TTS_SCRIPT_READY
        self._emit(task_id, EVT_TTS_SCRIPT_READY, {"tts_script": primary_loop_result["tts_script"]})
        self._set_step(
            task_id, "tts", "done",
            f"{lang_display}配音生成完成（{primary_loop_result['final_round']} 轮收敛）",
        )

        for result in variant_results.values():
            loop_result = result["loop_result"]
            for round_record in loop_result["rounds"]:
                round_idx = round_record["round"]
                round_products = loop_result.get("round_products") or []
                _candidate = (
                    round_products[round_idx - 1]
                    if 0 <= round_idx - 1 < len(round_products)
                    else None
                )
                # round_products 列表里某些 round 可能塞 None（例如该轮 LLM
                # 调用失败但被 catch 后只 append None 占位）；下面要 .get()，
                # 必须先确保是 dict。
                round_product = _candidate if isinstance(_candidate, dict) else {}
                round_translation = round_product.get("localized_translation") or {}
                round_tts_script = round_product.get("tts_script") or {}
                if round_idx >= 2:
                    _log_translate_billing(
                        user_id=self.user_id,
                        project_id=task_id,
                        use_case_code="video_translate.rewrite",
                        provider=provider,
                        input_tokens=round_record.get("translate_tokens_in"),
                        output_tokens=round_record.get("translate_tokens_out"),
                        success=True,
                        request_payload=_llm_request_payload(
                            round_translation, provider, "video_translate.rewrite"
                        ),
                        response_payload=_llm_response_payload(round_translation),
                    )
                _log_translate_billing(
                    user_id=self.user_id,
                    project_id=task_id,
                    use_case_code="video_translate.tts_script",
                    provider=provider,
                    input_tokens=round_record.get("tts_script_tokens_in"),
                    output_tokens=round_record.get("tts_script_tokens_out"),
                    success=True,
                    request_payload=_llm_request_payload(
                        round_tts_script, provider, "video_translate.tts_script"
                    ),
                    response_payload=_llm_response_payload(round_tts_script),
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
                    model=tts_model_id,
                    request_units=tts_char_count,
                    units_type="chars",
                    success=True,
                    request_payload={
                        "type": "tts",
                        "provider": "elevenlabs",
                        "model": tts_model_id,
                        "voice_id": voice.get("elevenlabs_voice_id"),
                        "text": (round_tts_script.get("full_text") or ""),
                        "segments": round_product.get("tts_segments") or [],
                    },
                    response_payload={
                        "audio_path": round_product.get("tts_audio_path"),
                        "chars": tts_char_count,
                    },
                )
        return

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
            round_products = loop_result.get("round_products") or []
            round_product = (
                round_products[round_idx - 1]
                if 0 <= round_idx - 1 < len(round_products)
                else {}
            )
            round_translation = round_product.get("localized_translation") or {}
            round_tts_script = round_product.get("tts_script") or {}
            if round_idx >= 2:
                _log_translate_billing(
                    user_id=self.user_id,
                    project_id=task_id,
                    use_case_code="video_translate.rewrite",
                    provider=provider,
                    input_tokens=round_record.get("translate_tokens_in"),
                    output_tokens=round_record.get("translate_tokens_out"),
                    success=True,
                    request_payload=_llm_request_payload(
                        round_translation, provider, "video_translate.rewrite"
                    ),
                    response_payload=_llm_response_payload(round_translation),
                )
            _log_translate_billing(
                user_id=self.user_id,
                project_id=task_id,
                use_case_code="video_translate.tts_script",
                provider=provider,
                input_tokens=round_record.get("tts_script_tokens_in"),
                output_tokens=round_record.get("tts_script_tokens_out"),
                success=True,
                request_payload=_llm_request_payload(
                    round_tts_script, provider, "video_translate.tts_script"
                ),
                response_payload=_llm_response_payload(round_tts_script),
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
                request_payload={
                    "type": "tts",
                    "provider": "elevenlabs",
                    "model": self.tts_model_id,
                    "voice_id": voice.get("elevenlabs_voice_id"),
                    "text": (round_tts_script.get("full_text") or ""),
                    "segments": round_product.get("tts_segments") or [],
                },
                response_payload={
                    "audio_path": round_product.get("tts_audio_path"),
                    "chars": tts_char_count,
                },
            )

    def _step_subtitle(self, task_id: str, task_dir: str) -> None:
        task = task_state.get(task_id)
        if _is_av_pipeline_task(task):
            if (task.get("steps") or {}).get("subtitle") == "done":
                return
            run_av_localize(task_id, runner=self, variant="av")
            return
        if self._skip_original_video_passthrough_step(task_id, "subtitle", task=task):
            return
        from appcore import asr_router
        from pipeline.subtitle import build_srt_from_chunks, save_srt
        from pipeline.subtitle_alignment import align_subtitle_chunks_to_asr

        # 字幕用 ASR：在 TTS 合成的英语音频上跑一次，拿词级时间戳给字幕对齐。
        # 先解析 adapter 给 step 卡片显示当前 provider。
        _sub_adapter, _ = asr_router.resolve_adapter("subtitle_asr", "en")
        _sub_model_tag = f"{_sub_adapter.display_name} · {_sub_adapter.model_id}"
        self._set_step(
            task_id, "subtitle", "running",
            "正在根据英文音频校正字幕...", model_tag=_sub_model_tag,
        )

        variant = "normal"
        variants = dict(task.get("variants", {}))
        variant_state = dict(variants.get(variant, {}))
        tts_audio_path = variant_state.get("tts_audio_path", "")

        _sub_result = asr_router.transcribe(
            tts_audio_path, source_language="en", stage="subtitle_asr",
        )
        english_utterances = _sub_result["utterances"]
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

        # Fire-and-forget translation-quality assessment. Failures don't block compose.
        try:
            from appcore import quality_assessment as _qa
            _qa.trigger_assessment(
                task_id=task_id, project_type=self.project_type,
                triggered_by="auto", user_id=self.user_id,
            )
        except Exception:  # noqa: BLE001 — assessment failures must not break pipeline
            log.warning("[%s] failed to trigger quality assessment for task %s",
                        self.project_type, task_id, exc_info=True)

    def _maybe_mix_background_for_compose(
        self,
        task_id: str,
        tts_audio_path: str,
        task_dir: str,
        variant: str,
    ) -> str:
        """硬字幕视频合成前的混音：TTS 主轨 + 分离出来的 accompaniment。

        如果 ``task["separation"]`` 可用，用 :func:`audio_loudness.mix_with_background`
        amix 两轨成单音轨 wav 给硬字幕 mp4 用；否则原 TTS 路径返回（旧行为）。

        CapCut 工程包导出走 :meth:`_step_export`，那里**不**走这里——工程包要保留
        独立 accompaniment 音轨让用户能在编辑器里单独调音量。
        """
        from pipeline import audio_separation as sep
        from appcore.audio_loudness import mix_with_background

        task = task_state.get(task_id) or {}
        separation = task.get("separation") or {}
        if not sep.is_usable(separation):
            return tts_audio_path

        settings = sep.load_settings()
        mixed_path = os.path.join(
            task_dir, f"final_audio_mixed.{variant}.wav",
        )
        try:
            mix_with_background(
                main_path=tts_audio_path,
                background_path=separation["accompaniment_path"],
                output_path=mixed_path,
                background_volume=settings.background_volume,
                duration="longest",
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "[compose] background mix failed, fall back to TTS-only audio: %s",
                exc,
            )
            return tts_audio_path

        log.info(
            "[compose] mixed TTS + accompaniment (bg_volume=%.2f) -> %s",
            settings.background_volume, mixed_path,
        )
        # 回写元数据供 UI 显示 / debug
        separation = dict(separation)
        separation["composite_audio_path"] = mixed_path
        separation["background_volume"] = settings.background_volume
        task_state.update(task_id, separation=separation)
        return mixed_path

    def _step_compose(self, task_id: str, video_path: str, task_dir: str) -> None:
        task = task_state.get(task_id)
        if _is_original_video_passthrough(task):
            self._complete_original_video_passthrough(task_id, video_path, task_dir)
            return
        self._set_step(task_id, "compose", "running", "正在合成视频...")
        from pipeline.compose import compose_video

        variant = "av" if _is_av_pipeline_task(task) else "normal"
        variants = dict(task.get("variants", {}))
        variant_state = dict(variants.get(variant, {}))
        audio_for_compose = self._maybe_mix_background_for_compose(
            task_id, variant_state["tts_audio_path"], task_dir, variant,
        )
        result = compose_video(
            video_path=video_path,
            tts_audio_path=audio_for_compose,
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
        from appcore import llm_bindings
        from appcore.llm_models import model_display_name

        task = task_state.get(task_id) or {}
        if self._skip_original_video_passthrough_step(task_id, "analysis", task=task):
            return
        variants = task.get("variants") or {}
        variant_state = variants.get("normal") or {}
        hard_video = (variant_state.get("result") or {}).get("hard_video")

        # 直接读 binding（USE_CASES 默认 model 与 video_score.SCORE_MODEL 一致），
        # 不再走 appcore.gemini.resolve_config。
        resolved_model = (
            llm_bindings.resolve("video_score.run").get("model")
            or video_score.SCORE_MODEL
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
        if _is_original_video_passthrough(task):
            self._complete_original_video_passthrough(task_id, video_path, task_dir)
            return
        self._set_step(task_id, "export", "running", "正在导出 CapCut 项目...")
        from pipeline.capcut import export_capcut_project
        from pipeline import audio_separation as sep

        variant = "av" if _is_av_pipeline_task(task) else "normal"
        variants = dict(task.get("variants", {}))
        variant_state = dict(variants.get(variant, {}))
        jianying_project_root = resolve_jianying_project_root(self.user_id)
        draft_title = (
            task.get("display_name")
            or task.get("original_filename")
            or os.path.basename(video_path)
        )
        # 给工程包带一条独立的环境音音轨（如果分离结果可用）。CapCut 工程包**不**走
        # _maybe_mix_background_for_compose（那是给硬字幕 mp4 单音轨用的），这里
        # 保留 TTS + accompaniment 两条独立轨道，让用户在剪映里能分别调音。
        separation = task.get("separation") or {}
        accompaniment_for_capcut = (
            separation.get("accompaniment_path")
            if sep.is_usable(separation) else None
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
            accompaniment_audio_path=accompaniment_for_capcut,
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
        archive_url = f"/api/tasks/{task_id}/download/capcut?variant={variant}"

        task_state.update(task_id, variants=variants, exports=exports, status="done", error="")
        task_state.set_expires_at(task_id, self.project_type)
        task_state.set_artifact(task_id, "export", build_export_artifact(manifest_text, archive_url=archive_url))
        self._set_step(task_id, "export", "done", "CapCut 项目已导出")
        self._emit(task_id, EVT_CAPCUT_READY, {"variants": [variant]})
        self._emit(task_id, EVT_PIPELINE_DONE, {
            "task_id": task_id,
            "exports": {variant: exports},
        })
        _skip_legacy_artifact_upload(task_state.get(task_id) or {}, task_id)



# Re-export AV helpers + dispatchers from sub-modules so existing
# callers (web routes, runtime_de/fr/ja/multi/omni/v2 subclasses,
# tools, tests) keep working.
from ._av_helpers import (
    _default_av_variant_state,
    _ensure_variant_state,
    _join_source_full_text,
    _load_json_if_exists,
    _restore_av_localize_outputs_from_files,
    _normalize_av_sentences,
    _build_av_localized_translation,
    _build_av_tts_segments,
    _rebuild_tts_full_audio_from_segments,
    _build_av_debug_state,
    _fail_localize,
    _new_silent_runner,
)
