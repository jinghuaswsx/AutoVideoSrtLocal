"""English-in/English-out redub runner.

This module keeps the new workflow isolated from ``omni_translate`` while
reusing Omni's step implementations and shared detail workbench contracts.
"""
from __future__ import annotations

import base64
import logging
from typing import Any

from appcore import task_state
from appcore.events import EVT_VOICE_MATCH_READY
from appcore.omni_plugin_config import validate_plugin_config
from appcore.preview_artifacts import build_translate_artifact
from appcore.runtime_omni import OmniTranslateRunner
from appcore.video_translate_defaults import resolve_default_voice
from pipeline import speech_rate_model
from pipeline.voice_embedding import embed_audio_file, serialize_embedding
from pipeline.voice_match import extract_sample_from_utterances

log = logging.getLogger(__name__)

SCRIPT_MODE_ORIGINAL = "original"
SCRIPT_MODE_REWRITE = "rewrite"
VALID_SCRIPT_MODES = frozenset({SCRIPT_MODE_ORIGINAL, SCRIPT_MODE_REWRITE})

ENGLISH_REDUB_DEFAULT_PLUGIN_CONFIG = {
    "asr_post": "asr_clean",
    "shot_decompose": True,
    "translate_algo": "shot_char_limit",
    "source_anchored": True,
    "tts_strategy": "sentence_reconcile",
    "subtitle": "sentence_units",
    "voice_separation": True,
    "loudness_match": True,
    "av_sync_audit": "report_only",
}


def normalize_script_mode(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in VALID_SCRIPT_MODES:
        return normalized
    return SCRIPT_MODE_ORIGINAL


def _segment_text(segment: dict) -> str:
    return str(
        segment.get("text")
        or segment.get("source_text")
        or segment.get("transcript")
        or ""
    ).strip()


def _float_value(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_script_segments(task: dict) -> list[dict[str, Any]]:
    alignment_segments = (task.get("alignment") or {}).get("script_segments") or []
    source_segments = (
        alignment_segments
        or task.get("script_segments")
        or task.get("utterances")
        or []
    )
    normalized: list[dict[str, Any]] = []
    for fallback_index, segment in enumerate(source_segments):
        if not isinstance(segment, dict):
            continue
        text = _segment_text(segment)
        if not text:
            continue
        start_time = _float_value(
            segment.get("start_time", segment.get("start")),
            0.0,
        )
        end_time = _float_value(
            segment.get("end_time", segment.get("end")),
            start_time,
        )
        if end_time < start_time:
            end_time = start_time
        try:
            index = int(segment.get("index", segment.get("asr_index", fallback_index)))
        except (TypeError, ValueError):
            index = fallback_index
        normalized.append({
            "index": index,
            "asr_index": index,
            "text": text,
            "start_time": start_time,
            "end_time": end_time,
            "shot_context": list(segment.get("shot_context") or []),
        })
    return normalized


def _target_chars_range(
    text: str,
    duration: float,
    *,
    voice_id: str | None = None,
    language: str = "en",
) -> list[int]:
    text_len = len(text)
    if text_len <= 0:
        return [0, 0]
    if duration > 0 and voice_id:
        cps = speech_rate_model.get_effective_rate(voice_id, language, fallback=None)
        if cps and cps > 0:
            lower = max(1, int(cps * duration * 0.92))
            upper = max(lower + 1, int(cps * duration * 1.08 + 0.5))
            return [lower, upper]
    lower = max(1, int(text_len * 0.95))
    upper = max(lower, int(text_len * 1.05 + 0.5))
    if duration > 0:
        # Keep a realistic lower bound for the duration reconcile diagnostics,
        # while preserving the original text as the initial TTS input.
        lower = min(lower, max(1, int(duration * 8)))
        upper = max(upper, int(duration * 20 + 0.5))
    return [lower, upper]


class EnglishRedubRunner(OmniTranslateRunner):
    """Fixed-English redub runner with isolated behavior switches."""

    project_type: str = "english_redub"
    profile_code: str = "omni"

    def _resolve_script_mode(self, task_id: str) -> str:
        task = task_state.get(task_id) or {}
        return normalize_script_mode(task.get("script_mode"))

    def _resolve_plugin_config(self, task_id: str) -> dict:
        task = task_state.get(task_id) or {}
        cfg = task.get("plugin_config") or ENGLISH_REDUB_DEFAULT_PLUGIN_CONFIG
        try:
            return validate_plugin_config(cfg)
        except ValueError:
            log.warning(
                "[english_redub] invalid plugin_config task=%s; using default",
                task_id,
                exc_info=True,
            )
            return validate_plugin_config(ENGLISH_REDUB_DEFAULT_PLUGIN_CONFIG)

    def pipeline_step_names_for_task(
        self,
        task_id: str,
        *,
        include_analysis: bool | None = None,
    ) -> list[str]:
        if include_analysis is None:
            include_analysis = self.include_analysis_in_main_flow
        return self.pipeline_step_names_for_config(
            self._resolve_plugin_config(task_id),
            include_analysis=include_analysis,
        )

    def _get_pipeline_steps(self, task_id: str, video_path: str, task_dir: str) -> list:
        step_fns = {
            "extract": lambda: self._step_extract(task_id, video_path, task_dir),
            "asr": lambda: self._step_asr(task_id, task_dir),
            "separate": lambda: self._step_separate(task_id, task_dir),
            "shot_decompose": lambda: self._step_shot_decompose(task_id, video_path, task_dir),
            "asr_clean": lambda: self.profile.post_asr(self, task_id),
            "asr_normalize": lambda: self.profile.post_asr(self, task_id),
            "voice_match": lambda: self._step_voice_match(task_id),
            "alignment": lambda: self._step_alignment(task_id, video_path, task_dir),
            "translate": lambda: self._step_translate(task_id),
            "tts": lambda: self.profile.tts(self, task_id, task_dir),
            "av_sync_audit": lambda: self._step_av_sync_audit(task_id, video_path, task_dir),
            "loudness_match": lambda: self._step_loudness_match(task_id, task_dir),
            "subtitle": lambda: self.profile.subtitle(self, task_id, task_dir),
            "compose": lambda: self._step_compose(task_id, video_path, task_dir),
            "analysis": lambda: self._step_analysis(task_id),
            "export": lambda: self._step_export(task_id, video_path, task_dir),
        }
        return [
            (name, step_fns[name])
            for name in self.pipeline_step_names_for_task(task_id)
        ]

    def _step_translate(self, task_id: str) -> None:
        if self._resolve_script_mode(task_id) == SCRIPT_MODE_ORIGINAL:
            self._step_translate_original(task_id)
            return
        self.profile.translate(self, task_id)

    def _step_translate_original(self, task_id: str) -> None:
        task = task_state.get(task_id) or {}
        if self._complete_original_video_passthrough(
            task_id,
            task.get("video_path") or "",
            task.get("task_dir") or "",
        ):
            return
        if (task.get("steps") or {}).get("translate") == "done":
            return

        self._set_step(task_id, "translate", "running", "正在按原始英文文案组装 TTS 输入...")
        script_segments = _normalize_script_segments(task)
        if not script_segments:
            raise RuntimeError("缺少英文 ASR 文案，无法重新配音")

        selected_voice_id = str(task.get("selected_voice_id") or task.get("voice_id") or "").strip()
        source_full_text = " ".join(segment["text"] for segment in script_segments).strip()
        sentences: list[dict[str, Any]] = []
        av_sentences: list[dict[str, Any]] = []
        for fallback_index, segment in enumerate(script_segments):
            asr_index = int(segment.get("asr_index", fallback_index))
            text = segment["text"]
            start_time = float(segment.get("start_time") or 0.0)
            end_time = float(segment.get("end_time") or start_time)
            target_duration = max(0.0, end_time - start_time)
            sentences.append({
                "index": fallback_index,
                "asr_index": asr_index,
                "text": text,
                "source_segment_indices": [asr_index],
            })
            av_sentences.append({
                "asr_index": asr_index,
                "start_time": start_time,
                "end_time": end_time,
                "source_start_time": start_time,
                "source_end_time": end_time,
                "target_duration": target_duration,
                "target_chars_range": _target_chars_range(
                    text,
                    target_duration,
                    voice_id=selected_voice_id,
                    language="en",
                ),
                "text": text,
                "est_chars": len(text),
                "source_text": text,
                "shot_context": list(segment.get("shot_context") or []),
            })

        localized_translation = {
            "full_text": source_full_text,
            "sentences": sentences,
            "script_mode": SCRIPT_MODE_ORIGINAL,
        }
        variants = dict(task.get("variants") or {})
        normal_variant = dict(variants.get("normal") or {})
        normal_variant["localized_translation"] = localized_translation
        variants["normal"] = normal_variant
        av_variant = dict(variants.get("av") or {})
        av_variant["sentences"] = av_sentences
        av_variant["localized_translation"] = localized_translation
        variants["av"] = av_variant

        task_state.update(
            task_id,
            script_mode=SCRIPT_MODE_ORIGINAL,
            script_segments=script_segments,
            normalized_script_segments=script_segments,
            localized_translation=localized_translation,
            source_full_text=source_full_text,
            source_full_text_zh=source_full_text,
            variants=variants,
        )
        task_state.set_artifact(
            task_id,
            "translate",
            build_translate_artifact(
                source_full_text,
                localized_translation,
                source_language="en",
                target_language="en",
            ),
        )
        self._set_step(
            task_id,
            "translate",
            "done",
            f"原始英文文案已组装（{len(script_segments)}句）",
        )

    def _step_voice_match(self, task_id: str) -> None:
        from appcore import english_redub_settings
        from pipeline import voice_match_speed
        from pipeline import audio_separation as separation_pkg

        task = task_state.get(task_id) or {}
        if self._skip_original_video_passthrough_step(task_id, "voice_match", task=task):
            return
        lang = "en"
        utterances = task.get("utterances") or []
        video_path = task.get("video_path")
        default_voice_id = resolve_default_voice(lang, user_id=self.user_id)
        self._set_step(task_id, "voice_match", "running", "EN 音色库加载中...")

        candidates: list[dict] = []
        clip = None
        query_embedding_b64 = None
        voice_ai_rankings: list = []
        voice_ai_rank_status = "skipped"
        voice_ai_rank_model = None
        voice_ai_rank_debug = None
        if utterances and video_path:
            try:
                separation = task.get("separation") or {}
                if separation_pkg.is_usable(separation):
                    clip = separation["vocals_path"]
                else:
                    clip = extract_sample_from_utterances(
                        video_path,
                        utterances,
                        out_dir=task["task_dir"],
                        min_duration=8.0,
                    )
                vec = embed_audio_file(clip)
                strategy = english_redub_settings.get_voice_match_strategy()
                if strategy == english_redub_settings.STRATEGY_TIMBRE_SPEED:
                    candidates = voice_match_speed.match_candidates_speed_aware(
                        vec,
                        language=lang,
                        source_utterances=utterances,
                        top_k=10,
                        exclude_voice_ids={default_voice_id} if default_voice_id else None,
                    )
                else:
                    from pipeline.voice_match import match_candidates

                    candidates = match_candidates(
                        vec,
                        language=lang,
                        top_k=10,
                        exclude_voice_ids={default_voice_id} if default_voice_id else None,
                    ) or []
                for candidate in candidates:
                    candidate["similarity"] = float(candidate.get("similarity", 0.0))
                query_embedding_b64 = base64.b64encode(
                    serialize_embedding(vec)
                ).decode("ascii")
            except Exception as exc:
                log.exception("[english_redub] voice match failed for %s: %s", task_id, exc)
                candidates = []
                query_embedding_b64 = None

        if candidates and clip:
            try:
                from appcore.voice_ai_ranking import rank_voice_candidates

                ai_result = rank_voice_candidates(
                    task_id=task_id,
                    task=task,
                    candidates=candidates,
                    source_audio_path=clip,
                    task_dir=task["task_dir"],
                    user_id=self.user_id,
                )
                candidates = ai_result.get("candidates") or candidates
                voice_ai_rankings = ai_result.get("rankings") or []
                voice_ai_rank_status = ai_result.get("status") or "done"
                voice_ai_rank_model = ai_result.get("model")
                voice_ai_rank_debug = ai_result.get("debug")
            except Exception as exc:
                log.exception("[english_redub] voice AI ranking failed for %s: %s", task_id, exc)
                voice_ai_rank_status = "failed"
                voice_ai_rank_debug = {
                    "status": "failed",
                    "provider": "openrouter",
                    "model": "google/gemini-3.5-flash",
                    "use_case": "voice_selection.assess",
                    "request": {"visual": {"media": [], "candidates": candidates[:10]}, "raw": {}},
                    "result": {"visual": {"rankings": []}, "raw": {"error": str(exc)[:500]}},
                }

        fallback = None if candidates else default_voice_id
        task_state.update(
            task_id,
            voice_match_candidates=candidates,
            voice_match_fallback_voice_id=fallback,
            voice_match_query_embedding=query_embedding_b64,
            voice_ai_rankings=voice_ai_rankings,
            voice_ai_rank_status=voice_ai_rank_status,
            voice_ai_rank_model=voice_ai_rank_model,
            voice_ai_rank_debug=voice_ai_rank_debug,
        )
        task_state.set_current_review_step(task_id, "voice_match")
        self._set_step(task_id, "voice_match", "waiting", "EN 音色库已就绪，请选择 TTS 音色")
        self._emit(task_id, EVT_VOICE_MATCH_READY, {
            "candidates": candidates,
            "fallback_voice_id": fallback,
            "target_lang": lang,
        })
