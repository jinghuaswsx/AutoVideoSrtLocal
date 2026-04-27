"""Dedicated Japanese video translation runner.

The generic multi-language runner is word-count oriented. This runner keeps
Japanese on a character-budget path from localization through subtitles.
"""
from __future__ import annotations

import base64
import logging
import os
import shutil

import appcore.task_state as task_state
from appcore import ai_billing
from appcore.api_keys import resolve_key
from appcore.events import EVT_SUBTITLE_READY, EVT_TRANSLATE_RESULT, EVT_TTS_SCRIPT_READY
from appcore.llm_bindings import resolve
from appcore.runtime import (
    PipelineRunner,
    _build_review_segments,
    _compute_next_target,
    _distance_to_duration_range,
    _llm_response_payload,
    _save_json,
    _tts_final_target_range,
)
from appcore.video_translate_defaults import resolve_default_voice
from pipeline import ja_translate, speech_rate_model
from pipeline.voice_embedding import embed_audio_file, serialize_embedding
from pipeline.voice_match import extract_sample_from_utterances, match_candidates
from pipeline.extract import get_video_duration
from pipeline.languages import ja as ja_rules
from pipeline.subtitle import build_srt_from_chunks, save_srt
from pipeline.timeline import build_timeline_manifest
from pipeline.tts import _get_audio_duration, generate_full_audio
from web.preview_artifacts import (
    build_asr_artifact,
    build_subtitle_artifact,
    build_translate_artifact,
    build_tts_artifact,
)

log = logging.getLogger(__name__)

_EMERGENCY_MULTILINGUAL_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"


def _voice_public_id(voice: dict | None) -> str:
    voice = voice or {}
    return str(voice.get("elevenlabs_voice_id") or voice.get("voice_id") or "")


class JapaneseTranslateRunner(PipelineRunner):
    project_type = "ja_translate"
    target_language_label = "ja"
    tts_language_code = "ja"
    tts_model_id = "eleven_multilingual_v2"
    tts_default_voice_language = "ja"

    def _get_localization_module(self, task: dict):
        del task
        return ja_translate

    def _resolve_voice(self, task: dict, loc_mod) -> dict:
        selected_voice_id = task.get("selected_voice_id")
        if selected_voice_id:
            return {
                "id": None,
                "elevenlabs_voice_id": selected_voice_id,
                "name": task.get("selected_voice_name") or selected_voice_id,
            }

        fallback = resolve_default_voice("ja", user_id=self.user_id)
        if fallback:
            return {"id": None, "elevenlabs_voice_id": fallback, "name": "Default Japanese"}

        return {
            "id": None,
            "elevenlabs_voice_id": _EMERGENCY_MULTILINGUAL_VOICE_ID,
            "name": "Multilingual fallback",
        }

    def _step_voice_match(self, task_id: str) -> None:
        from appcore.events import EVT_VOICE_MATCH_READY

        task = task_state.get(task_id)
        utterances = task.get("utterances") or []
        video_path = task.get("video_path")
        default_voice_id = resolve_default_voice("ja", user_id=self.user_id)

        self._set_step(task_id, "voice_match", "running", "正在匹配日语音色候选...")

        candidates: list[dict] = []
        query_embedding_b64 = None
        if utterances and video_path:
            try:
                clip = extract_sample_from_utterances(
                    video_path,
                    utterances,
                    out_dir=task["task_dir"],
                    min_duration=8.0,
                )
                vec = embed_audio_file(clip)
                candidates = match_candidates(
                    vec,
                    language="ja",
                    top_k=10,
                    exclude_voice_ids={default_voice_id} if default_voice_id else None,
                ) or []
                for candidate in candidates:
                    candidate["similarity"] = float(candidate.get("similarity", 0.0))
                query_embedding_b64 = base64.b64encode(serialize_embedding(vec)).decode("ascii")
            except Exception as exc:
                log.exception("ja voice match failed for %s: %s", task_id, exc)
                candidates = []
                query_embedding_b64 = None

        fallback = None if candidates else default_voice_id
        task_state.update(
            task_id,
            voice_match_candidates=candidates,
            voice_match_fallback_voice_id=fallback,
            voice_match_query_embedding=query_embedding_b64,
        )
        task_state.set_current_review_step(task_id, "voice_match")
        self._set_step(task_id, "voice_match", "waiting", "日语音色候选已就绪，请先确认音色")
        self._emit(
            task_id,
            EVT_VOICE_MATCH_READY,
            {
                "candidates": candidates,
                "fallback_voice_id": fallback,
                "target_lang": "ja",
            },
        )

    def _get_pipeline_steps(self, task_id: str, video_path: str, task_dir: str) -> list:
        steps = super()._get_pipeline_steps(task_id, video_path, task_dir)
        out = []
        for name, fn in steps:
            out.append((name, fn))
            if name == "asr":
                out.append(("voice_match", lambda: self._step_voice_match(task_id)))
        return out

    def _step_translate(self, task_id: str) -> None:
        task = task_state.get(task_id)
        task_dir = task["task_dir"]
        script_segments = task.get("script_segments", [])
        voice = self._resolve_voice(task, ja_translate)
        voice_id = _voice_public_id(voice)

        self._set_step(
            task_id,
            "translate",
            "running",
            "正在按日语字符预算逐句本土化...",
            model_tag="ja_translate.localize",
        )

        source_full_text = ja_translate.build_source_full_text(script_segments)
        localized_translation = ja_translate.generate_ja_localized_translation(
            script_segments=script_segments,
            voice_id=voice_id,
            user_id=self.user_id,
            project_id=task_id,
        )
        initial_messages = localized_translation.pop("_messages", None)
        if initial_messages:
            _save_json(
                task_dir,
                "ja_localized_translate_messages.json",
                {
                    "phase": "ja_initial_translate",
                    "target_language": "ja",
                    "messages": initial_messages,
                },
            )

        variants = dict(task.get("variants", {}))
        variant_state = dict(variants.get("normal", {}))
        variant_state["localized_translation"] = localized_translation
        variants["normal"] = variant_state

        review_segments = _build_review_segments(script_segments, localized_translation)
        requires_confirmation = bool(task.get("interactive_review"))

        _save_json(task_dir, "source_full_text.json", {"full_text": source_full_text})
        _save_json(task_dir, "localized_translation.normal.json", localized_translation)
        _save_json(task_dir, "localized_translation.json", localized_translation)

        task_state.update(
            task_id,
            source_full_text=source_full_text,
            source_full_text_zh=source_full_text,
            source_language=task.get("source_language", "en"),
            target_lang="ja",
            localized_translation=localized_translation,
            variants=variants,
            segments=review_segments,
            selected_voice_id=voice_id,
            _segments_confirmed=not requires_confirmation,
        )
        task_state.set_artifact(
            task_id,
            "asr",
            build_asr_artifact(task.get("utterances", []), source_full_text, source_language=task.get("source_language", "en")),
        )
        task_state.set_artifact(
            task_id,
            "translate",
            build_translate_artifact(source_full_text, localized_translation, source_language=task.get("source_language", "en"), target_language="ja"),
        )

        if requires_confirmation:
            task_state.set_current_review_step(task_id, "translate")
            self._set_step(task_id, "translate", "waiting", "日语译文已生成，等待人工确认")
        else:
            task_state.set_current_review_step(task_id, "")
            self._set_step(task_id, "translate", "done", "日语本土化翻译完成")

        self._emit(
            task_id,
            EVT_TRANSLATE_RESULT,
            {
                "source_full_text_zh": source_full_text,
                "localized_translation": localized_translation,
                "segments": review_segments,
                "requires_confirmation": requires_confirmation,
            },
        )

    def _step_tts(self, task_id: str, task_dir: str) -> None:
        task = task_state.get(task_id)
        voice = self._resolve_voice(task, ja_translate)
        voice_id = _voice_public_id(voice)
        if not voice_id:
            raise ValueError("No ElevenLabs voice_id available for Japanese TTS")

        self._set_step(task_id, "tts", "running", "正在生成日语配音并执行时长收敛...", model_tag="ElevenLabs · ja")
        self._emit_substep_msg(task_id, "tts", "正在生成日语配音 · 加载配音模板")
        elevenlabs_api_key = resolve_key(self.user_id, "elevenlabs", "ELEVENLABS_API_KEY")

        try:
            binding = resolve("ja_translate.localize")
            translate_provider = binding.get("provider") or "ja_translate.localize"
            translate_model = binding.get("model") or "ja_translate.localize"
        except Exception:
            translate_provider = "ja_translate.localize"
            translate_model = "ja_translate.localize"
        translate_channel = {
            "openrouter": "OpenRouter",
            "doubao": "豆包（火山）",
            "gemini_vertex": "Vertex AI",
            "gemini_aistudio": "Google AI Studio",
        }.get(translate_provider, translate_provider)

        variants = dict(task.get("variants", {}))
        variant_state = dict(variants.get("normal", {}))
        video_duration = get_video_duration(task["video_path"])
        duration_lo, duration_hi = _tts_final_target_range(video_duration)
        current_localized = variant_state.get("localized_translation") or task.get("localized_translation") or {}
        rounds: list[dict] = []
        round_products: list[dict] = []
        selected: dict | None = None
        translation_message_paths: dict[int, str] = {}
        if os.path.exists(os.path.join(task_dir, "ja_localized_translate_messages.json")):
            translation_message_paths[1] = "ja_localized_translate_messages.json"

        task_state.update(
            task_id,
            tts_duration_rounds=[],
            tts_duration_status="running",
            tts_translate_provider=translate_provider,
            tts_translate_model=translate_model,
            tts_translate_channel=translate_channel,
        )

        for round_index in range(1, 6):
            artifact_paths: dict[str, str] = {}
            if round_index == 1 and translation_message_paths.get(1):
                artifact_paths["initial_translate_messages"] = translation_message_paths[1]
            elif round_index > 1 and translation_message_paths.get(round_index):
                artifact_paths["localized_rewrite_messages"] = translation_message_paths[round_index]

            tts_script = ja_translate.build_ja_tts_script(current_localized)
            self._emit_substep_msg(task_id, "tts",
                f"正在生成日语配音 · 第 {round_index} 轮 · 切分朗读文案完成")
            tts_segments = ja_translate.build_ja_tts_segments(tts_script, task.get("script_segments", []))
            round_variant = f"ja_round_{round_index}"

            def _on_seg_done(done, total, info, _round=round_index):
                self._emit_substep_msg(
                    task_id, "tts",
                    f"正在生成日语配音 · 第 {_round} 轮 · 生成 ElevenLabs 音频 {done}/{total}",
                )

            self._emit_substep_msg(task_id, "tts",
                f"正在生成日语配音 · 第 {round_index} 轮 · 生成 ElevenLabs 音频 0/{len(tts_segments)}")
            tts_output = generate_full_audio(
                tts_segments,
                voice_id=voice_id,
                output_dir=task_dir,
                variant=round_variant,
                elevenlabs_api_key=elevenlabs_api_key,
                model_id=self.tts_model_id,
                language_code=self.tts_language_code,
                on_segment_done=_on_seg_done,
            )
            round_audio_path = tts_output["full_audio_path"]
            round_segments = tts_output["segments"]
            audio_duration = _get_audio_duration(round_audio_path)
            ja_char_count = ja_translate.count_visible_japanese_chars(tts_script.get("full_text", ""))
            in_range = duration_lo <= audio_duration <= duration_hi

            localized_translation_filename = f"localized_translation.round_{round_index}.json"
            tts_script_filename = f"tts_script.round_{round_index}.json"
            round_audio_filename = f"tts_full.ja_round_{round_index}.mp3"
            _save_json(task_dir, tts_script_filename, tts_script)
            _save_json(task_dir, f"tts_result.round_{round_index}.json", round_segments)
            _save_json(task_dir, localized_translation_filename, current_localized)
            artifact_paths["localized_translation"] = localized_translation_filename
            artifact_paths["tts_script"] = tts_script_filename
            artifact_paths["tts_full_audio"] = round_audio_filename

            record = {
                "round": round_index,
                "target_language": "ja",
                "tts_char_count": ja_char_count,
                "ja_char_count": ja_char_count,
                "audio_duration": audio_duration,
                "video_duration": video_duration,
                "duration_lo": duration_lo,
                "duration_hi": duration_hi,
                "direction": "initial" if round_index == 1 else "rewrite",
                "artifact_paths": artifact_paths,
                "message": (
                    f"第 {round_index} 轮：日语 {ja_char_count} 字，音频 {audio_duration:.1f}s，"
                    f"目标区间 {duration_lo:.1f}-{duration_hi:.1f}s。"
                ),
            }

            rounds.append(record)
            round_products.append(
                {
                    "round": round_index,
                    "tts_script": tts_script,
                    "segments": round_segments,
                    "audio_path": round_audio_path,
                    "localized_translation": current_localized,
                    "audio_duration": audio_duration,
                    "ja_char_count": ja_char_count,
                }
            )
            task_state.update(task_id, tts_duration_rounds=rounds)
            self._emit_duration_round(task_id, round_index, "measure", record)

            if in_range:
                record["is_final"] = True
                record["final_reason"] = "converged"
                record["final_distance"] = 0.0
                rounds[-1] = record
                selected = round_products[-1]
                task_state.update(
                    task_id,
                    tts_duration_rounds=rounds,
                    tts_duration_status="converged",
                    tts_final_round=round_index,
                    tts_final_reason="converged",
                    tts_final_distance=0.0,
                )
                self._emit_duration_round(task_id, round_index, "converged", record)
                break

            observed_cps = ja_char_count / audio_duration if audio_duration > 0 else None
            if observed_cps is None or observed_cps <= 0:
                observed_cps = speech_rate_model.get_rate(voice_id, "ja") or ja_translate.FALLBACK_JA_CPS
            next_target_duration, next_target_chars, direction = _compute_next_target(
                round_index + 1,
                audio_duration,
                observed_cps,
                video_duration,
            )
            record.update(
                {
                    "selected": False,
                    "next_target_duration": next_target_duration,
                    "next_target_chars": next_target_chars,
                    "direction": direction,
                    "message": (
                        f"第 {round_index} 轮未收敛：音频 {audio_duration:.1f}s，"
                        f"下一轮按 {next_target_chars} 个日语可见字符 {direction}。"
                    ),
                }
            )
            rounds[-1] = record
            task_state.update(task_id, tts_duration_rounds=rounds)

            if round_index == 5:
                continue

            next_localized = ja_translate.rewrite_ja_localized_translation(
                localized_translation=current_localized,
                script_segments=task.get("script_segments", []),
                target_total_chars=next_target_chars,
                direction=direction,
                last_audio_duration=audio_duration,
                video_duration=video_duration,
                user_id=self.user_id,
                project_id=task_id,
            )
            rewrite_messages = next_localized.pop("_messages", None)
            if rewrite_messages:
                rewrite_filename = f"ja_localized_rewrite_messages.round_{round_index + 1}.json"
                _save_json(
                    task_dir,
                    rewrite_filename,
                    {
                        "phase": "ja_duration_rewrite",
                        "round": round_index + 1,
                        "direction": direction,
                        "target_total_chars": next_target_chars,
                        "messages": rewrite_messages,
                    },
                )
                translation_message_paths[round_index + 1] = rewrite_filename
            current_localized = next_localized

        if selected is None:
            best_i = min(
                range(len(rounds)),
                key=lambda index: _distance_to_duration_range(rounds[index]["audio_duration"], duration_lo, duration_hi),
            )
            best_record = rounds[best_i]
            best_distance = round(_distance_to_duration_range(best_record["audio_duration"], duration_lo, duration_hi), 3)
            best_record["is_final"] = True
            best_record["final_reason"] = "best_pick"
            best_record["final_distance"] = best_distance
            rounds[best_i] = best_record
            selected = round_products[best_i]
            task_state.update(
                task_id,
                tts_duration_rounds=rounds,
                tts_duration_status="converged",
                tts_final_round=best_i + 1,
                tts_final_reason="best_pick",
                tts_final_distance=best_distance,
            )
            self._emit_duration_round(task_id, best_i + 1, "best_pick", best_record)

        tts_script = selected["tts_script"]
        final_segments = selected["segments"]
        ja_char_count = selected["ja_char_count"]
        final_audio_path = os.path.join(task_dir, "tts_full.normal.mp3")
        if os.path.abspath(selected["audio_path"]) != os.path.abspath(final_audio_path):
            shutil.copy2(selected["audio_path"], final_audio_path)

        try:
            speech_rate_model.update_rate(
                voice_id,
                "ja",
                chars=ja_char_count,
                duration_seconds=selected["audio_duration"],
            )
        except Exception:
            log.warning("[ja_translate] failed to update speech rate for task %s", task_id, exc_info=True)

        timeline_manifest = build_timeline_manifest(final_segments, video_duration=video_duration)
        duration_rounds = rounds
        localized_translation = selected["localized_translation"]
        final_reason = rounds[selected["round"] - 1].get("final_reason") or "best_pick"
        final_distance = rounds[selected["round"] - 1].get("final_distance")
        if final_distance is None:
            final_distance = round(_distance_to_duration_range(selected["audio_duration"], duration_lo, duration_hi), 3)

        variant_state.update(
            {
                "segments": final_segments,
                "tts_script": tts_script,
                "tts_audio_path": final_audio_path,
                "timeline_manifest": timeline_manifest,
                "voice_id": voice.get("id"),
                "selected_voice_id": voice_id,
                "localized_translation": localized_translation,
            }
        )
        variant_state.setdefault("preview_files", {})["tts_full_audio"] = final_audio_path
        variant_state.setdefault("artifacts", {})["tts"] = build_tts_artifact(
            tts_script,
            final_segments,
            duration_rounds=duration_rounds,
        )
        variants["normal"] = variant_state

        _save_json(task_dir, "tts_script.normal.json", tts_script)
        _save_json(task_dir, "tts_result.normal.json", final_segments)
        _save_json(task_dir, "timeline_manifest.normal.json", timeline_manifest)
        _save_json(task_dir, "localized_translation.normal.json", localized_translation)
        _save_json(task_dir, "tts_duration_rounds.json", duration_rounds)

        task_state.set_preview_file(task_id, "tts_full_audio", final_audio_path)
        task_state.update(
            task_id,
            variants=variants,
            segments=final_segments,
            tts_script=tts_script,
            tts_audio_path=final_audio_path,
            voice_id=voice.get("id") or task.get("voice_id"),
            selected_voice_id=voice_id,
            timeline_manifest=timeline_manifest,
            localized_translation=localized_translation,
            tts_duration_rounds=duration_rounds,
            tts_duration_status="converged",
            tts_final_round=selected["round"],
            tts_final_reason=final_reason,
            tts_final_distance=final_distance,
        )
        task_state.set_artifact(
            task_id,
            "tts",
            build_tts_artifact(tts_script, final_segments, duration_rounds=duration_rounds),
        )
        ai_billing.log_request(
            use_case_code="video_translate.tts",
            user_id=self.user_id,
            project_id=task_id,
            provider="elevenlabs",
            model=self.tts_model_id,
            request_units=ja_char_count,
            units_type="chars",
            success=True,
            request_payload={
                "type": "tts",
                "provider": "elevenlabs",
                "model": self.tts_model_id,
                "voice_id": voice_id,
                "text": tts_script.get("full_text") or "",
                "segments": final_segments,
            },
            response_payload={
                "audio_path": final_audio_path,
                "chars": ja_char_count,
                "tts_script": _llm_response_payload(tts_script),
            },
        )
        self._emit(task_id, EVT_TTS_SCRIPT_READY, {"tts_script": tts_script})
        self._set_step(task_id, "tts", "done", "日语配音生成完成并完成时长收敛")

    def _step_subtitle(self, task_id: str, task_dir: str) -> None:
        task = task_state.get(task_id)
        self._set_step(task_id, "subtitle", "running", "正在生成日语字幕...")

        variants = dict(task.get("variants", {}))
        variant_state = dict(variants.get("normal", {}))
        tts_script = variant_state.get("tts_script") or task.get("tts_script") or {}
        tts_segments = variant_state.get("segments") or task.get("segments") or []
        corrected_chunks = ja_translate.build_timed_subtitle_chunks(tts_script, tts_segments)
        srt_content = build_srt_from_chunks(corrected_chunks, weak_boundary_words=ja_rules.WEAK_STARTERS)
        srt_content = ja_rules.post_process_srt(srt_content)
        srt_path = save_srt(srt_content, os.path.join(task_dir, "subtitle.normal.srt"))

        variant_state.update(
            {
                "corrected_subtitle": {"chunks": corrected_chunks, "srt_content": srt_content},
                "srt_path": srt_path,
            }
        )
        variants["normal"] = variant_state

        task_state.set_preview_file(task_id, "srt", srt_path)
        task_state.update(
            task_id,
            variants=variants,
            corrected_subtitle={"chunks": corrected_chunks, "srt_content": srt_content},
            srt_path=srt_path,
        )
        task_state.set_artifact(task_id, "subtitle", build_subtitle_artifact(srt_content, target_language="ja"))
        _save_json(task_dir, "corrected_subtitle.normal.json", {"chunks": corrected_chunks, "srt_content": srt_content})

        self._emit(task_id, EVT_SUBTITLE_READY, {"srt": srt_content})
        self._set_step(task_id, "subtitle", "done", "日语字幕生成完成")
