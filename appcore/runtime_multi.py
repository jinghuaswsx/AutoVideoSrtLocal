"""多语种视频翻译 pipeline runner。

单一 Runner 处理 de/fr/es/it/ja/pt 所有目标语言：
- 翻译步骤走 llm_prompt_configs resolver
- 字幕/TTS 走 pipeline.languages.<lang> 规则
- 音色走现有 voice_match + elevenlabs_voices
"""
from __future__ import annotations

import logging
import os

import appcore.task_state as task_state
from appcore.api_keys import resolve_key
from appcore.events import EVT_ENGLISH_ASR_RESULT, EVT_SUBTITLE_READY, EVT_TRANSLATE_RESULT
from pipeline.asr import transcribe_local_audio
from pipeline.subtitle import build_srt_from_chunks, save_srt
from pipeline.subtitle_alignment import align_subtitle_chunks_to_asr
from pipeline.tts import _get_audio_duration
from appcore.llm_prompt_configs import resolve_prompt_config
from appcore.runtime import (
    PipelineRunner,
    _build_review_segments,
    _save_json,
    _resolve_translate_provider,
)
from appcore.usage_log import record as _log_usage
from appcore.video_translate_defaults import resolve_default_voice
from pipeline.voice_embedding import embed_audio_file
from pipeline.voice_match import extract_sample_from_utterances, match_candidates
from pipeline.localization import build_source_full_text_zh
from pipeline.translate import generate_localized_translation, get_model_display_name
from web.preview_artifacts import (
    build_asr_artifact,
    build_subtitle_artifact,
    build_translate_artifact,
    build_tts_artifact,
)

log = logging.getLogger(__name__)


class MultiTranslateRunner(PipelineRunner):
    project_type: str = "multi_translate"
    tts_model_id = "eleven_multilingual_v2"

    def _resolve_target_lang(self, task: dict) -> str:
        lang = task.get("target_lang")
        if not lang:
            raise ValueError("task.target_lang is required for multi_translate")
        return lang

    def _get_lang_rules(self, lang: str):
        from pipeline.languages.registry import get_rules
        return get_rules(lang)

    def _build_system_prompt(self, lang: str) -> str:
        base = resolve_prompt_config("base_translation", lang)
        plugin = resolve_prompt_config("ecommerce_plugin", None)
        return f"{base['content']}\n\n---\n\n{plugin['content']}"

    def _step_translate(self, task_id: str) -> None:
        task = task_state.get(task_id)
        task_dir = task["task_dir"]
        lang = self._resolve_target_lang(task)
        source_language = task.get("source_language", "zh")
        lang_label = "中文" if source_language == "zh" else "英文"

        self._set_step(task_id, "translate", "running",
                       f"正在将{lang_label}翻译为 {lang.upper()}...")

        provider = _resolve_translate_provider(self.user_id)
        script_segments = task.get("script_segments", [])
        source_full_text = build_source_full_text_zh(script_segments)

        system_prompt = self._build_system_prompt(lang)

        localized_translation = generate_localized_translation(
            source_full_text, script_segments, variant="normal",
            custom_system_prompt=system_prompt,
            provider=provider, user_id=self.user_id,
        )

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
        _save_json(task_dir, "source_full_text.json", {"full_text": source_full_text})
        _save_json(task_dir, "localized_translation.json", localized_translation)

        usage = localized_translation.get("_usage") or {}
        _log_usage(self.user_id, task_id, provider,
                    model_name=get_model_display_name(provider, self.user_id),
                    success=True,
                    input_tokens=usage.get("input_tokens"),
                    output_tokens=usage.get("output_tokens"))

        if requires_confirmation:
            task_state.set_current_review_step(task_id, "translate")
            self._set_step(task_id, "translate", "waiting",
                           f"{lang.upper()} 翻译已生成，等待人工确认")
        else:
            task_state.set_current_review_step(task_id, "")
            self._set_step(task_id, "translate", "done",
                           f"{lang.upper()} 本土化翻译完成")

        self._emit(task_id, EVT_TRANSLATE_RESULT, {
            "source_full_text_zh": source_full_text,
            "localized_translation": localized_translation,
            "segments": review_segments,
            "requires_confirmation": requires_confirmation,
        })

    def _step_subtitle(self, task_id: str, task_dir: str) -> None:
        task = task_state.get(task_id)
        lang = self._resolve_target_lang(task)
        rules = self._get_lang_rules(lang)

        self._set_step(task_id, "subtitle", "running",
                       f"正在根据 {lang.upper()} 音频校正字幕...")

        volc_api_key = resolve_key(self.user_id, "volc", "VOLC_API_KEY")

        variants = dict(task.get("variants", {}))
        variant_state = dict(variants.get("normal", {}))
        tts_audio_path = variant_state.get("tts_audio_path", "")

        utterances = transcribe_local_audio(
            tts_audio_path, prefix=f"tts-asr/{task_id}/normal",
            volc_api_key=volc_api_key,
        )
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

        self._emit(task_id, EVT_ENGLISH_ASR_RESULT, {"english_asr_result": asr_result})
        self._emit(task_id, EVT_SUBTITLE_READY, {"srt": srt_content})
        self._set_step(task_id, "subtitle", "done", f"{lang.upper()} 字幕生成完成")

    def _step_voice_match(self, task_id: str) -> None:
        """跑向量匹配写候选到 state，然后暂停 pipeline 等待用户在 UI 上选择音色。"""
        from appcore.events import EVT_VOICE_MATCH_READY

        task = task_state.get(task_id)
        lang = self._resolve_target_lang(task)
        utterances = task.get("utterances") or []
        video_path = task.get("video_path")

        self._set_step(task_id, "voice_match", "running", f"{lang.upper()} 音色库加载中...")

        candidates: list = []
        if utterances and video_path:
            try:
                clip = extract_sample_from_utterances(
                    video_path, utterances, out_dir=task["task_dir"],
                    min_duration=8.0,
                )
                vec = embed_audio_file(clip)
                candidates = match_candidates(vec, language=lang, top_k=3) or []
                for c in candidates:
                    c["similarity"] = float(c.get("similarity", 0.0))
            except Exception as exc:
                log.exception("voice match failed for %s: %s", task_id, exc)
                candidates = []

        fallback = None if candidates else resolve_default_voice(lang)

        task_state.update(
            task_id,
            voice_match_candidates=candidates,
            voice_match_fallback_voice_id=fallback,
        )

        # 暂停 pipeline，等待 /api/multi-translate/<task_id>/confirm-voice
        task_state.set_current_review_step(task_id, "voice_match")
        msg = f"{lang.upper()} 音色库已就绪，请选择 TTS 音色"
        self._set_step(task_id, "voice_match", "waiting", msg)
        self._emit(task_id, EVT_VOICE_MATCH_READY, {
            "candidates": candidates,
            "fallback_voice_id": fallback,
            "target_lang": lang,
        })

    def _resolve_voice(self, task, loc_mod):
        """多语种：优先用户确认的 selected_voice_id → fallback。"""
        voice_id = task.get("selected_voice_id")
        if voice_id:
            return {
                "id": None,
                "elevenlabs_voice_id": voice_id,
                "name": task.get("selected_voice_name") or voice_id,
            }
        lang = self._resolve_target_lang(task)
        fallback = resolve_default_voice(lang)
        if fallback:
            return {"id": None, "elevenlabs_voice_id": fallback, "name": "Default"}
        return super()._resolve_voice(task, loc_mod)

    def _get_pipeline_steps(self, task_id: str, video_path: str, task_dir: str) -> list:
        """覆盖基类：在 asr 后、alignment 前插入 voice_match。"""
        base_steps = super()._get_pipeline_steps(task_id, video_path, task_dir)
        # 在 asr 之后插入 voice_match
        out = []
        for name, fn in base_steps:
            out.append((name, fn))
            if name == "asr":
                out.append(("voice_match", lambda: self._step_voice_match(task_id)))
        return out
