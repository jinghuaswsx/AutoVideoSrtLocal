"""Framework-agnostic pipeline runner.

No Flask, no socketio, no web imports.
Uses EventBus to publish status events consumed by any adapter (web, desktop).
"""
from __future__ import annotations

import json
import os
import uuid

import appcore.task_state as task_state
from appcore.api_keys import resolve_jianying_project_root
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
    build_asr_artifact,
    build_compose_artifact,
    build_export_artifact,
    build_extract_artifact,
    build_subtitle_artifact,
    build_translate_artifact,
    build_tts_artifact,
    build_variant_compare_artifact,
)


def _upload_artifacts_to_tos(task: dict, task_id: str) -> None:
    """Upload final video/srt artifacts to TOS. Errors are silently ignored."""
    try:
        import tos as tos_sdk
        import config
        if not config.TOS_ACCESS_KEY or not config.TOS_SECRET_KEY:
            return
        client = tos_sdk.TosClientV2(
            ak=config.TOS_ACCESS_KEY, sk=config.TOS_SECRET_KEY,
            endpoint=config.TOS_ENDPOINT, region=config.TOS_REGION,
        )
        user_id = task.get("_user_id", "anon")
        tos_uploads = {}

        for variant, variant_state in (task.get("variants") or {}).items():
            result = variant_state.get("result", {})
            for key in ("soft_video", "hard_video"):
                path = result.get(key)
                if path and os.path.exists(path):
                    tos_key = f"{user_id}/{task_id}/{variant}/{os.path.basename(path)}"
                    client.put_object_from_file(config.TOS_BUCKET, tos_key, path)
                    tos_uploads[tos_key] = key
            srt_path = variant_state.get("srt_path")
            if srt_path and os.path.exists(srt_path):
                tos_key = f"{user_id}/{task_id}/{variant}/{os.path.basename(srt_path)}"
                client.put_object_from_file(config.TOS_BUCKET, tos_key, srt_path)
                tos_uploads[tos_key] = "srt"

        if tos_uploads:
            import appcore.task_state as _ts
            _ts.update(task_id, tos_uploads=tos_uploads)
    except Exception:
        pass  # TOS upload never blocks pipeline completion


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


class PipelineRunner:
    def __init__(self, bus: EventBus, user_id: int | None = None) -> None:
        self.bus = bus
        self.user_id = user_id

    def _emit(self, task_id: str, event_type: str, payload: dict) -> None:
        self.bus.publish(Event(type=event_type, task_id=task_id, payload=payload))

    def _set_step(self, task_id: str, step: str, status: str, message: str = "") -> None:
        task_state.set_step(task_id, step, status)
        task_state.set_step_message(task_id, step, message)
        self._emit(task_id, EVT_STEP_UPDATE, {"step": step, "status": status, "message": message})

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
            ("export", lambda: self._step_export(task_id, video_path, task_dir)),
        ]

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
        self._set_step(task_id, "asr", "done", f"识别完成，共 {len(utterances)} 段")
        from appcore.usage_log import record as _log_usage
        _log_usage(self.user_id, task_id, "doubao_asr", success=True)
        self._emit(task_id, EVT_ASR_RESULT, {"segments": utterances})

    def _step_alignment(self, task_id: str, video_path: str, task_dir: str) -> None:
        task = task_state.get(task_id)
        self._set_step(task_id, "alignment", "running", "正在分析镜头并生成分段建议...")
        from pipeline.alignment import compile_alignment, detect_scene_cuts
        from pipeline.voice_library import get_voice_library

        scene_cuts = detect_scene_cuts(video_path)
        alignment = compile_alignment(task.get("utterances", []), scene_cuts=scene_cuts)
        suggested_voice = get_voice_library().recommend_voice(
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
        from appcore.api_keys import resolve_key
        from pipeline.localization import VARIANT_KEYS, build_source_full_text_zh
        from pipeline.translate import generate_localized_translation

        openrouter_api_key = resolve_key(self.user_id, "openrouter", "OPENROUTER_API_KEY")
        script_segments = task.get("script_segments", [])
        source_full_text_zh = build_source_full_text_zh(script_segments)
        variants = dict(task.get("variants", {}))
        for variant in VARIANT_KEYS:
            localized_translation = generate_localized_translation(
                source_full_text_zh, script_segments, variant=variant,
                openrouter_api_key=openrouter_api_key,
            )
            variant_state = dict(variants.get(variant, {}))
            variant_state["localized_translation"] = localized_translation
            variants[variant] = variant_state
            _save_json(task_dir, f"localized_translation.{variant}.json", localized_translation)

        normal_lt = variants.get("normal", {}).get("localized_translation", {})
        review_segments = _build_review_segments(script_segments, normal_lt)
        requires_confirmation = bool(task.get("interactive_review"))
        task_state.update(
            task_id,
            source_full_text_zh=source_full_text_zh,
            localized_translation=normal_lt,
            variants=variants,
            segments=review_segments,
            _segments_confirmed=not requires_confirmation,
        )
        task_state.set_artifact(task_id, "asr", build_asr_artifact(task.get("utterances", []), source_full_text_zh))

        compare_variants = {}
        for variant, variant_state in variants.items():
            payload = build_translate_artifact(source_full_text_zh, variant_state.get("localized_translation", {}))
            task_state.set_variant_artifact(task_id, variant, "translate", payload)
            compare_variants[variant] = {
                "label": variant_state.get("label", variant),
                "items": payload.get("items", []),
            }

        task_state.set_artifact(task_id, "translate", build_variant_compare_artifact("翻译本土化", compare_variants))
        _save_json(task_dir, "source_full_text_zh.json", {"full_text": source_full_text_zh})
        _save_json(task_dir, "localized_translation.json", normal_lt)

        from appcore.usage_log import record as _log_usage
        from pipeline.translate import _model_name as _get_model_name
        _log_usage(self.user_id, task_id, "openrouter", model_name=_get_model_name(), success=True)

        if requires_confirmation:
            task_state.set_current_review_step(task_id, "translate")
            self._set_step(task_id, "translate", "waiting", "翻译结果已生成，等待人工确认")
        else:
            task_state.set_current_review_step(task_id, "")
            self._set_step(task_id, "translate", "done", "本土化翻译完成")

        self._emit(task_id, EVT_TRANSLATE_RESULT, {
            "source_full_text_zh": source_full_text_zh,
            "localized_translation": normal_lt,
            "segments": review_segments,
            "variants": variants,
            "requires_confirmation": requires_confirmation,
        })

    def _step_tts(self, task_id: str, task_dir: str) -> None:
        task = task_state.get(task_id)
        self._set_step(task_id, "tts", "running", "正在生成 ElevenLabs 朗读文案与配音...")
        from appcore.api_keys import resolve_key
        from pipeline.extract import get_video_duration
        from pipeline.localization import VARIANT_KEYS, build_tts_segments
        from pipeline.timeline import build_timeline_manifest
        from pipeline.translate import generate_tts_script
        from pipeline.tts import generate_full_audio, get_default_voice, get_voice_by_id

        openrouter_api_key = resolve_key(self.user_id, "openrouter", "OPENROUTER_API_KEY")
        elevenlabs_api_key = resolve_key(self.user_id, "elevenlabs", "ELEVENLABS_API_KEY")

        voice = None
        if task.get("voice_id"):
            voice = get_voice_by_id(task["voice_id"])
        if not voice and task.get("recommended_voice_id"):
            voice = get_voice_by_id(task["recommended_voice_id"])
        if not voice:
            voice = get_default_voice(task.get("voice_gender", "male"))

        variants = dict(task.get("variants", {}))
        video_duration = get_video_duration(task["video_path"])
        for variant in VARIANT_KEYS:
            variant_state = dict(variants.get(variant, {}))
            localized_translation = variant_state.get("localized_translation", {})
            tts_script = generate_tts_script(localized_translation, openrouter_api_key=openrouter_api_key)
            tts_segments = build_tts_segments(tts_script, task.get("script_segments", []))
            result = generate_full_audio(tts_segments, voice["elevenlabs_voice_id"], task_dir, variant=variant, elevenlabs_api_key=elevenlabs_api_key)
            timeline_manifest = build_timeline_manifest(result["segments"], video_duration=video_duration)
            variant_state.update({
                "segments": result["segments"],
                "tts_script": tts_script,
                "tts_audio_path": result["full_audio_path"],
                "timeline_manifest": timeline_manifest,
                "voice_id": voice["id"],
            })
            variants[variant] = variant_state
            task_state.set_variant_preview_file(task_id, variant, "tts_full_audio", result["full_audio_path"])
            _save_json(task_dir, f"tts_script.{variant}.json", tts_script)
            _save_json(task_dir, f"tts_result.{variant}.json", result["segments"])
            _save_json(task_dir, f"timeline_manifest.{variant}.json", timeline_manifest)

        normal_variant = variants.get("normal", {})
        task_state.update(
            task_id,
            variants=variants,
            segments=normal_variant.get("segments", []),
            tts_script=normal_variant.get("tts_script", {}),
            tts_audio_path=normal_variant.get("tts_audio_path"),
            voice_id=voice["id"],
            timeline_manifest=normal_variant.get("timeline_manifest", {}),
        )
        if normal_variant.get("tts_audio_path"):
            task_state.set_preview_file(task_id, "tts_full_audio", normal_variant["tts_audio_path"])

        compare_variants = {}
        for variant, variant_state in variants.items():
            payload = build_tts_artifact(
                variant_state.get("tts_script", {}),
                variant_state.get("segments", []),
            )
            task_state.set_variant_artifact(task_id, variant, "tts", payload)
            compare_variants[variant] = {
                "label": variant_state.get("label", variant),
                "items": payload.get("items", []),
            }

        task_state.set_artifact(task_id, "tts", build_variant_compare_artifact("语音生成", compare_variants))
        self._emit(task_id, EVT_TTS_SCRIPT_READY, {"tts_script": normal_variant.get("tts_script", {}), "variants": variants})
        self._set_step(task_id, "tts", "done", "英文配音生成完成")
        from appcore.usage_log import record as _log_usage
        _log_usage(self.user_id, task_id, "elevenlabs", success=True)

    def _step_subtitle(self, task_id: str, task_dir: str) -> None:
        task = task_state.get(task_id)
        self._set_step(task_id, "subtitle", "running", "正在根据英文音频校正字幕...")
        from appcore.api_keys import resolve_key
        from pipeline.asr import transcribe_local_audio
        from pipeline.localization import VARIANT_KEYS

        volc_api_key = resolve_key(self.user_id, "volc", "VOLC_API_KEY")
        from pipeline.subtitle import build_srt_from_chunks, save_srt
        from pipeline.subtitle_alignment import align_subtitle_chunks_to_asr

        variants = dict(task.get("variants", {}))
        compare_variants = {}
        for variant in VARIANT_KEYS:
            variant_state = dict(variants.get(variant, {}))
            tts_audio_path = variant_state.get("tts_audio_path", "")
            english_utterances = transcribe_local_audio(
                tts_audio_path, prefix=f"tts-asr/{task_id}/{variant}", volc_api_key=volc_api_key
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
            srt_path = save_srt(srt_content, os.path.join(task_dir, f"subtitle.{variant}.srt"))
            variant_state.update({
                "english_asr_result": english_asr_result,
                "corrected_subtitle": {"chunks": corrected_chunks, "srt_content": srt_content},
                "srt_path": srt_path,
            })
            task_state.set_variant_preview_file(task_id, variant, "srt", srt_path)
            variants[variant] = variant_state
            payload = build_subtitle_artifact(english_asr_result, corrected_chunks, srt_content)
            task_state.set_variant_artifact(task_id, variant, "subtitle", payload)
            compare_variants[variant] = {
                "label": variant_state.get("label", variant),
                "items": payload.get("items", []),
            }
            _save_json(task_dir, f"english_asr_result.{variant}.json", english_asr_result)
            _save_json(task_dir, f"corrected_subtitle.{variant}.json", {"chunks": corrected_chunks, "srt_content": srt_content})

        normal_variant = variants.get("normal", {})
        task_state.update(
            task_id,
            variants=variants,
            english_asr_result=normal_variant.get("english_asr_result", {}),
            corrected_subtitle=normal_variant.get("corrected_subtitle", {}),
            srt_path=normal_variant.get("srt_path"),
        )
        task_state.set_artifact(task_id, "subtitle", build_variant_compare_artifact("字幕生成", compare_variants))
        self._emit(task_id, EVT_ENGLISH_ASR_RESULT, {"variants": variants, "english_asr_result": normal_variant.get("english_asr_result", {})})
        self._emit(task_id, EVT_SUBTITLE_READY, {"variants": variants, "srt": normal_variant.get("corrected_subtitle", {}).get("srt_content", "")})
        self._set_step(task_id, "subtitle", "done", "英文字幕生成完成")

    def _step_compose(self, task_id: str, video_path: str, task_dir: str) -> None:
        task = task_state.get(task_id)
        self._set_step(task_id, "compose", "running", "正在合成视频...")
        from pipeline.compose import compose_video
        from pipeline.localization import VARIANT_KEYS

        variants = dict(task.get("variants", {}))
        compare_variants = {}
        for variant in VARIANT_KEYS:
            variant_state = dict(variants.get(variant, {}))
            result = compose_video(
                video_path=video_path,
                tts_audio_path=variant_state["tts_audio_path"],
                srt_path=variant_state["srt_path"],
                output_dir=task_dir,
                subtitle_position=task.get("subtitle_position", "bottom"),
                timeline_manifest=variant_state.get("timeline_manifest"),
                variant=variant,
            )
            variant_state["result"] = result
            variants[variant] = variant_state
            task_state.set_variant_preview_file(task_id, variant, "soft_video", result["soft_video"])
            task_state.set_variant_preview_file(task_id, variant, "hard_video", result["hard_video"])
            payload = build_compose_artifact()
            task_state.set_variant_artifact(task_id, variant, "compose", payload)
            compare_variants[variant] = {
                "label": variant_state.get("label", variant),
                "items": payload.get("items", []),
            }

        normal_variant = variants.get("normal", {})
        task_state.update(task_id, variants=variants, result=normal_variant.get("result", {}), status="composing_done")
        if normal_variant.get("result", {}).get("soft_video"):
            task_state.set_preview_file(task_id, "soft_video", normal_variant["result"]["soft_video"])
        if normal_variant.get("result", {}).get("hard_video"):
            task_state.set_preview_file(task_id, "hard_video", normal_variant["result"]["hard_video"])
        task_state.set_artifact(task_id, "compose", build_variant_compare_artifact("视频合成", compare_variants))
        self._set_step(task_id, "compose", "done", "视频合成完成")

    def _step_export(self, task_id: str, video_path: str, task_dir: str) -> None:
        task = task_state.get(task_id)
        self._set_step(task_id, "export", "running", "正在导出 CapCut 项目...")
        from pipeline.capcut import export_capcut_project
        from pipeline.localization import VARIANT_KEYS

        variants = dict(task.get("variants", {}))
        compare_variants = {}
        jianying_project_root = resolve_jianying_project_root(self.user_id)
        draft_title = (
            task.get("display_name")
            or task.get("original_filename")
            or os.path.basename(video_path)
        )
        for variant in VARIANT_KEYS:
            variant_state = dict(variants.get(variant, {}))
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
            exports = dict(variant_state.get("exports", {}))
            exports.update({
                "capcut_project": export_result["project_dir"],
                "capcut_archive": export_result["archive_path"],
                "capcut_manifest": export_result["manifest_path"],
                "jianying_project_dir": export_result.get("jianying_project_dir", ""),
            })
            variant_state["exports"] = exports
            variants[variant] = variant_state
            manifest_text = ""
            try:
                with open(export_result["manifest_path"], "r", encoding="utf-8") as fh:
                    manifest_text = fh.read()
            except OSError:
                manifest_text = ""
            archive_url = f"/api/tasks/{task_id}/download/capcut?variant={variant}"
            payload = build_export_artifact(manifest_text, archive_url=archive_url)
            task_state.set_variant_artifact(task_id, variant, "export", payload)
            compare_variants[variant] = {
                "label": variant_state.get("label", variant),
                "items": payload.get("items", []),
            }

        normal_variant = variants.get("normal", {})
        task_state.update(task_id, variants=variants, exports=normal_variant.get("exports", {}), status="done")
        task_state.set_artifact(task_id, "export", build_variant_compare_artifact("CapCut 导出", compare_variants))
        self._set_step(task_id, "export", "done", "CapCut 项目已导出")
        self._emit(task_id, EVT_CAPCUT_READY, {"variants": list(variants.keys())})
        self._emit(task_id, EVT_PIPELINE_DONE, {
            "task_id": task_id,
            "exports": {variant: variant_state.get("exports", {}) for variant, variant_state in variants.items()},
        })
        _upload_artifacts_to_tos(task_state.get(task_id) or {}, task_id)
