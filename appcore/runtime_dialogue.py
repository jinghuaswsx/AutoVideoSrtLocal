from __future__ import annotations

import logging

from appcore import task_state
from appcore.dialogue_translate.diarization import DiarizationUnavailable
from appcore.runtime_omni_v2 import OmniV2TranslateRunner

log = logging.getLogger(__name__)


def _replace_voice_match_steps(names: list[str]) -> list[str]:
    out: list[str] = []
    for name in names:
        if name == "voice_match":
            out.extend(["speaker_detect", "voice_match_ab"])
        else:
            out.append(name)
    return out


def _target_lang(task: dict) -> str:
    for key in ("target_lang", "target_language", "target_language_code"):
        value = str(task.get(key) or "").strip()
        if value:
            return value
    return "en"


class DialogueTranslateRunner(OmniV2TranslateRunner):
    """Dialogue translation runner with speaker detection and A/B voice review."""

    project_type: str = "dialogue_translate"
    profile_code: str = "omni_v2"

    @staticmethod
    def pipeline_step_names_for_config(
        plugin_config: dict,
        *,
        include_analysis: bool = False,
    ) -> list[str]:
        names = OmniV2TranslateRunner.pipeline_step_names_for_config(
            plugin_config,
            include_analysis=include_analysis,
        )
        return _replace_voice_match_steps(names)

    def _get_pipeline_steps(self, task_id: str, video_path: str, task_dir: str) -> list:
        step_fns = {
            "extract": lambda: self._step_extract(task_id, video_path, task_dir),
            "asr": lambda: self._step_asr(task_id, task_dir),
            "separate": lambda: self._step_separate(task_id, task_dir),
            "shot_decompose": lambda: self._step_shot_decompose(task_id, video_path, task_dir),
            "asr_clean": lambda: self.profile.post_asr(self, task_id),
            "asr_normalize": lambda: self.profile.post_asr(self, task_id),
            "speaker_detect": lambda: self._step_speaker_detect(task_id),
            "voice_match_ab": lambda: self._step_voice_match_ab(task_id),
            "alignment": lambda: self._step_alignment(task_id, video_path, task_dir),
            "translate": lambda: self.profile.translate(self, task_id),
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

    def _step_speaker_detect(self, task_id: str) -> None:
        from appcore.dialogue_translate.speaker_detection import detect_dialogue_segments

        task = task_state.get(task_id) or {}
        self._set_step(task_id, "speaker_detect", "running", "Detecting A/B speakers...")
        utterances = task.get("utterances_en") or task.get("utterances") or []
        audio_path = task.get("video_path") or task.get("audio_path") or ""
        try:
            result = detect_dialogue_segments(
                utterances=utterances,
                audio_path=audio_path,
                task_id=task_id,
            )
        except DiarizationUnavailable as exc:
            message = str(exc)
            task_state.update(task_id, status="error", error=message)
            self._set_step(task_id, "speaker_detect", "failed", message)
            return

        task_state.update(task_id, **result)
        self._set_step(task_id, "speaker_detect", "done", "A/B speaker detection complete")

    def _step_voice_match_ab(self, task_id: str) -> None:
        from appcore.dialogue_translate.voice_match import (
            build_speaker_sample_windows,
            match_voices_for_speakers,
        )

        task = task_state.get(task_id) or {}
        dialogue_segments = task.get("dialogue_segments") or []
        if not dialogue_segments:
            message = "Missing dialogue_segments; cannot run A/B voice matching"
            task_state.update(task_id, status="error", error=message)
            self._set_step(task_id, "voice_match_ab", "failed", message)
            return

        self._set_step(task_id, "voice_match_ab", "running", "Matching A/B speaker voices...")
        sample_specs = build_speaker_sample_windows(dialogue_segments)
        profiles = match_voices_for_speakers(
            video_path=str(task.get("video_path") or ""),
            task_dir=str(task.get("task_dir") or ""),
            target_lang=_target_lang(task),
            dialogue_segments=dialogue_segments,
            sample_specs=sample_specs,
            user_id=self.user_id,
        )
        selected = task.get("selected_voice_by_speaker") or {}
        task_state.update(
            task_id,
            speaker_sample_specs=sample_specs,
            speaker_profiles=profiles,
            selected_voice_by_speaker=selected if isinstance(selected, dict) else {},
        )
        task_state.set_current_review_step(task_id, "voice_match_ab")
        self._set_step(task_id, "voice_match_ab", "waiting", "A/B voice candidates are ready for review")

    def _prepare_tts_segments_for_audio_gen(self, task: dict, tts_segments: list[dict]) -> list[dict]:
        from appcore.dialogue_translate.tts import apply_speaker_voices_to_tts_segments

        return apply_speaker_voices_to_tts_segments(
            tts_segments,
            task.get("dialogue_segments") or [],
            task.get("selected_voice_by_speaker") or {},
        )

    def _resolve_voice(self, task: dict, loc_mod) -> dict:
        selected_by_speaker = task.get("selected_voice_by_speaker") or {}
        if isinstance(selected_by_speaker, dict):
            for speaker in ("A", "B"):
                voice = selected_by_speaker.get(speaker)
                if not isinstance(voice, dict):
                    continue
                voice_id = (
                    voice.get("elevenlabs_voice_id")
                    or voice.get("voice_id")
                    or voice.get("id")
                )
                if voice_id:
                    return {
                        "id": voice.get("id"),
                        "elevenlabs_voice_id": voice_id,
                        "name": voice.get("voice_name") or voice.get("name") or voice_id,
                    }
        return super()._resolve_voice(task, loc_mod)
