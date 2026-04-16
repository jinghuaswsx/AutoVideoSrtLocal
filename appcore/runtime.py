"""Framework-agnostic pipeline runner.

No Flask, no socketio, no web imports.
Uses EventBus to publish status events consumed by any adapter (web, desktop).
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime

log = logging.getLogger(__name__)

import appcore.task_state as task_state
from appcore.api_keys import resolve_jianying_project_root
from appcore import tos_clients
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


def _resolve_translate_provider(user_id: int | None) -> str:
    """Return the user's preferred translate provider, default 'openrouter'."""
    from appcore.api_keys import get_key
    if user_id is None:
        return "openrouter"
    pref = get_key(user_id, "translate_pref")
    return pref if pref in ("openrouter", "doubao") else "openrouter"


def _lang_display(label: str) -> str:
    """Convert language label (en/de/fr) to Chinese display name for step messages."""
    return {"en": "英语", "de": "德语", "fr": "法语"}.get(label, label)


def _compute_next_target(
    round_index: int,
    last_audio_duration: float,
    cps: float,
    video_duration: float,
) -> tuple[float, int, str]:
    """Compute (target_duration, target_chars, direction) for round 2 or 3.

    Round 2 uses fixed offsets; round 3 uses adaptive over-correction
    (reverse half of the error, clamped to the interior of the target range
    with a 0.3s safety margin).

    Args:
        round_index: 2 or 3.
        last_audio_duration: audio length from the previous round (seconds).
        cps: characters-per-second rate for this voice×language.
        video_duration: original video duration (seconds).

    Returns:
        (target_duration_seconds, target_char_count, direction)
        direction ∈ {"shrink", "expand"}
    """
    duration_lo = max(0.0, video_duration - 3.0)
    duration_hi = video_duration
    center = video_duration - 1.5

    if round_index == 2:
        if last_audio_duration > duration_hi:
            target_duration = max(0.0, video_duration - 2.0)
            direction = "shrink"
        else:
            # below lo
            target_duration = max(0.0, video_duration - 1.0)
            direction = "expand"
    else:  # round_index == 3 (and any fallback)
        raw = center - 0.5 * (last_audio_duration - center)
        target_duration = max(duration_lo + 0.3, min(duration_hi - 0.3, raw))
        direction = "shrink" if last_audio_duration > center else "expand"

    target_chars = max(10, round(target_duration * cps))
    return target_duration, target_chars, direction


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

    def _set_step(self, task_id: str, step: str, status: str, message: str = "") -> None:
        task_state.set_step(task_id, step, status)
        task_state.set_step_message(task_id, step, message)
        self._emit(task_id, EVT_STEP_UPDATE, {"step": step, "status": status, "message": message})

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
        up to 3 rounds until audio duration lands in [video-3, video].

        Returns dict with: localized_translation, tts_script, tts_audio_path,
        tts_segments, rounds, final_round.
        """
        import importlib
        from pipeline.speech_rate_model import get_rate, update_rate
        from pipeline.tts import generate_full_audio, _get_audio_duration
        from pipeline.translate import generate_tts_script, generate_localized_rewrite

        MAX_ROUNDS = 5
        duration_lo = max(0.0, video_duration - 3.0)
        duration_hi = video_duration

        rounds: list[dict] = []
        prev_localized = initial_localized_translation
        last_audio_duration = 0.0
        last_char_count = 0

        from functools import partial
        validator = partial(
            getattr(loc_mod, "validate_tts_script", None)
            or importlib.import_module("pipeline.localization").validate_tts_script,
            max_words=14 if self.target_language_label in ("de", "fr") else 10,
        )

        for round_index in range(1, MAX_ROUNDS + 1):
            round_record: dict = {
                "round": round_index,
                "video_duration": video_duration,
                "duration_lo": duration_lo,
                "duration_hi": duration_hi,
                "artifact_paths": {},
            }

            # Phase 1: translate_rewrite (skipped on round 1)
            if round_index == 1:
                localized_translation = prev_localized
                round_record["message"] = "初始译文（来自 translate 步骤）"
            else:
                voice_el_id = voice["elevenlabs_voice_id"]
                lang_code = self.tts_language_code or "en"
                cps = get_rate(voice_el_id, lang_code)
                if not cps or cps <= 0:
                    cps = (last_char_count / last_audio_duration) if last_audio_duration > 0 else 15.0
                target_duration, target_chars, direction = _compute_next_target(
                    round_index, last_audio_duration, cps, video_duration,
                )
                round_record["target_duration"] = target_duration
                round_record["target_chars"] = target_chars
                round_record["direction"] = direction
                round_record["message"] = (
                    f"第 {round_index} 轮：重写{_lang_display(self.target_language_label)}译文"
                    f"（目标 {target_chars} 字符，{direction}）"
                )
                self._emit_duration_round(task_id, round_index, "translate_rewrite", round_record)

                localized_translation = generate_localized_rewrite(
                    source_full_text=source_full_text,
                    prev_localized_translation=prev_localized,
                    target_chars=target_chars,
                    direction=direction,
                    source_language=source_language,
                    messages_builder=loc_mod.build_localized_rewrite_messages,
                    provider=provider,
                    user_id=self.user_id,
                )
                _save_json(task_dir, f"localized_translation.round_{round_index}.json", localized_translation)
                round_record["artifact_paths"]["localized_translation"] = f"localized_translation.round_{round_index}.json"
                round_record["char_count_prev"] = last_char_count

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
            audio_duration = _get_audio_duration(result["full_audio_path"])
            char_count = len(tts_script.get("full_text", ""))
            update_rate(voice["elevenlabs_voice_id"],
                        self.tts_language_code or "en",
                        chars=char_count, duration_seconds=audio_duration)
            round_record["audio_duration"] = audio_duration
            round_record["char_count"] = char_count

            # persist rounds incrementally so UI survives page refresh
            import appcore.task_state as task_state
            rounds.append(round_record)
            task_state.update(task_id, tts_duration_rounds=rounds)

            self._emit_duration_round(task_id, round_index, "measure", round_record)

            if duration_lo <= audio_duration <= duration_hi:
                self._emit_duration_round(task_id, round_index, "converged", round_record)
                task_state.update(task_id, tts_duration_status="converged")
                return {
                    "localized_translation": localized_translation,
                    "tts_script": tts_script,
                    "tts_audio_path": result["full_audio_path"],
                    "tts_segments": result["segments"],
                    "rounds": rounds,
                    "final_round": round_index,
                }

            prev_localized = localized_translation
            last_audio_duration = audio_duration
            last_char_count = char_count

        # 3 rounds exhausted, not converged
        self._emit_duration_round(task_id, MAX_ROUNDS, "failed", round_record)
        import appcore.task_state as task_state
        task_state.update(task_id, tts_duration_status="failed")
        raise RuntimeError(
            f"TTS 音频时长 {MAX_ROUNDS} 轮内未收敛到 [{duration_lo:.1f}, {duration_hi:.1f}] 区间，"
            f"最后一次为 {last_audio_duration:.1f}s。请调整 voice 或翻译 prompt 后重试。"
        )

    def _promote_final_artifacts(self, task_dir: str, final_round: int, variant: str) -> None:
        """Copy tts_full.round_{N}.mp3 to tts_full.{variant}.mp3 for downstream compatibility."""
        import shutil
        src = os.path.join(task_dir, f"tts_full.round_{final_round}.mp3")
        dst = os.path.join(task_dir, f"tts_full.{variant}.mp3")
        if os.path.exists(src):
            shutil.copy2(src, dst)

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

    def _run(self, task_id: str, start_step: str = "extract") -> None:
        task = task_state.get(task_id)
        video_path = task["video_path"]
        task_dir = task["task_dir"]
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
        from appcore.usage_log import record as _log_usage
        _log_usage(self.user_id, task_id, "doubao_asr", success=True)

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
        self._set_step(task_id, "translate", "running", "正在生成整段本土化翻译...")
        from pipeline.localization import build_source_full_text_zh
        from pipeline.translate import generate_localized_translation

        provider = _resolve_translate_provider(self.user_id)
        script_segments = task.get("script_segments", [])
        source_full_text_zh = build_source_full_text_zh(script_segments)

        variant = "normal"
        custom_prompt = task.get("custom_translate_prompt")
        localized_translation = generate_localized_translation(
            source_full_text_zh, script_segments, variant=variant,
            custom_system_prompt=custom_prompt,
            provider=provider, user_id=self.user_id,
        )

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

        from appcore.usage_log import record as _log_usage
        from pipeline.translate import get_model_display_name
        _translate_usage = localized_translation.get("_usage") or {}
        _log_usage(self.user_id, task_id, provider,
                   model_name=get_model_display_name(provider, self.user_id),
                   success=True,
                   input_tokens=_translate_usage.get("input_tokens"),
                   output_tokens=_translate_usage.get("output_tokens"))

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
        self._set_step(task_id, "tts", "running", f"正在生成{lang_display}配音...")

        from appcore.api_keys import resolve_key
        from pipeline.extract import get_video_duration

        provider = _resolve_translate_provider(self.user_id)
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

        # reset duration tracking for a fresh run (e.g. resume)
        task_state.update(task_id, tts_duration_rounds=[], tts_duration_status="running")

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

        # Promote final to standard file names
        self._promote_final_artifacts(task_dir, loop_result["final_round"], variant)
        final_audio_path = os.path.join(task_dir, f"tts_full.{variant}.mp3")

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
        from appcore.usage_log import record as _log_usage
        from pipeline.translate import get_model_display_name
        for round_record in loop_result["rounds"]:
            round_idx = round_record["round"]
            if round_idx >= 2:
                # rewrite LLM call
                _log_usage(self.user_id, task_id, provider,
                           model_name=get_model_display_name(provider, self.user_id),
                           success=True)
            # tts_script LLM call every round
            _log_usage(self.user_id, task_id, provider,
                       model_name=get_model_display_name(provider, self.user_id),
                       success=True)
        # ElevenLabs call every round
        for _ in loop_result["rounds"]:
            _log_usage(self.user_id, task_id, "elevenlabs", success=True)

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

        self._set_step(task_id, "analysis", "running", "AI 分析中（评分 + CSK）...")
        task = task_state.get(task_id) or {}
        variants = task.get("variants") or {}
        variant_state = variants.get("normal") or {}
        hard_video = (variant_state.get("result") or {}).get("hard_video")

        _, resolved_model = resolve_config(
            self.user_id, service="gemini_video_analysis",
            default_model=video_score.SCORE_MODEL,
        )
        model_label = model_display_name(resolved_model)

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
