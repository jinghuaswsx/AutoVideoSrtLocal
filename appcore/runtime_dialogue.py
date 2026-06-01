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


def _voice_id_from(voice: object) -> str:
    if isinstance(voice, dict):
        for key in ("voice_id", "elevenlabs_voice_id", "id"):
            value = str(voice.get(key) or "").strip()
            if value:
                return value
    elif voice:
        return str(voice).strip()
    return ""


def _voice_name_from(voice: object, voice_id: str) -> str:
    if isinstance(voice, dict):
        for key in ("name", "voice_name", "label"):
            value = str(voice.get(key) or "").strip()
            if value:
                return value
    return voice_id


def _selected_voice_from_existing(voice: object) -> dict | None:
    voice_id = _voice_id_from(voice)
    if not voice_id:
        return None
    selected = dict(voice) if isinstance(voice, dict) else {}
    selected["voice_id"] = voice_id
    selected.setdefault("name", _voice_name_from(voice, voice_id))
    return selected


def _selected_voice_from_candidate(candidate: object) -> dict | None:
    voice_id = _voice_id_from(candidate)
    if not voice_id:
        return None
    selected = {
        "voice_id": voice_id,
        "name": _voice_name_from(candidate, voice_id),
    }
    if isinstance(candidate, dict) and candidate.get("voice_name"):
        selected["voice_name"] = candidate["voice_name"]
    return selected


def _initialize_selected_speaker_voices(
    profiles: dict,
    selected_voice_by_speaker: object,
) -> tuple[dict, dict]:
    selected = (
        dict(selected_voice_by_speaker)
        if isinstance(selected_voice_by_speaker, dict)
        else {}
    )
    normalized_profiles = {
        speaker: dict(profile) if isinstance(profile, dict) else profile
        for speaker, profile in (profiles or {}).items()
    }

    for speaker in ("A", "B"):
        profile = normalized_profiles.get(speaker)
        if not isinstance(profile, dict):
            continue

        selected_voice = _selected_voice_from_existing(selected.get(speaker))
        if selected_voice is None:
            for candidate in profile.get("candidates") or []:
                selected_voice = _selected_voice_from_candidate(candidate)
                if selected_voice is not None:
                    break

        if selected_voice is None:
            continue
        selected[speaker] = selected_voice
        profile["selected_voice"] = selected_voice

    return normalized_profiles, selected


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

    def _base_pipeline_step_names_for_task(
        self,
        task_id: str,
        *,
        include_analysis: bool | None = None,
    ) -> list[str]:
        if include_analysis is None:
            include_analysis = self.include_analysis_in_main_flow
        return OmniV2TranslateRunner.pipeline_step_names_for_config(
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
            "translate": lambda: self.profile.translate(self, task_id),
            "tts": lambda: self.profile.tts(self, task_id, task_dir),
            "av_sync_audit": lambda: self._step_av_sync_audit(task_id, video_path, task_dir),
            "loudness_match": lambda: self._step_loudness_match(task_id, task_dir),
            "subtitle": lambda: self.profile.subtitle(self, task_id, task_dir),
            "compose": lambda: self._step_compose(task_id, video_path, task_dir),
            "analysis": lambda: self._step_analysis(task_id),
            "export": lambda: self._step_export(task_id, video_path, task_dir),
        }
        steps = [
            (name, step_fns[name])
            for name in self._base_pipeline_step_names_for_task(task_id)
        ]
        out = []
        for name, fn in steps:
            if name == "voice_match":
                out.append(("speaker_detect", lambda: self._step_speaker_detect(task_id)))
                out.append(("voice_match_ab", lambda: self._step_voice_match_ab(task_id)))
            else:
                out.append((name, fn))
        return out

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
            auto_select_speaker_voices_with_ai,
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
        profiles, selected = auto_select_speaker_voices_with_ai(
            task_id=task_id,
            task=task,
            profiles=profiles,
            task_dir=str(task.get("task_dir") or ""),
            dialogue_segments=dialogue_segments,
            user_id=self.user_id,
        )
        required_speakers = ("A", "B")
        missing_speakers = [
            speaker for speaker in required_speakers
            if not _voice_id_from(selected.get(speaker))
        ]
        task_state.update(
            task_id,
            speaker_sample_specs=sample_specs,
            speaker_profiles=profiles,
            selected_voice_by_speaker=selected,
        )
        if missing_speakers:
            message = (
                "A/B voice auto-selection failed for speaker(s): "
                + ", ".join(missing_speakers)
            )
            task_state.update(task_id, status="error", error=message)
            task_state.set_current_review_step(task_id, "")
            self._set_step(task_id, "voice_match_ab", "failed", message)
            return

        task_state.update(task_id, status="running", error="")
        task_state.set_current_review_step(task_id, "")
        self._set_step(task_id, "voice_match_ab", "done", "A/B speaker voices auto-selected")

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
