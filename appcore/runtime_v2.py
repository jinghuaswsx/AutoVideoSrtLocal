"""视频翻译（测试）流水线 V2。

PipelineRunnerV2 编排 7 步流水线：
  extract -> shot_decompose -> voice_match -> translate
    -> tts_verify -> subtitle -> compose

模块级只依赖 appcore，pipeline.* 在步骤函数内部延迟导入，
避免测试时连锁加载 ElevenLabs / Gemini 客户端。
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

from appcore import task_state
from appcore.api_keys import resolve_key
from appcore.events import (
    EVT_LAB_PIPELINE_DONE,
    EVT_LAB_PIPELINE_ERROR,
    EVT_LAB_SHOT_DECOMPOSE_RESULT,
    EVT_LAB_SUBTITLE_READY,
    EVT_LAB_TRANSLATE_PROGRESS,
    EVT_LAB_TTS_PROGRESS,
    EVT_LAB_VOICE_CONFIRMED,
    EVT_LAB_VOICE_MATCH_CANDIDATES,
)
from appcore.runtime import PipelineRunner

log = logging.getLogger(__name__)


class PipelineRunnerV2(PipelineRunner):
    """视频翻译（测试）模块的流水线 runner。"""

    # v2 流水线保持原行为：生成软字幕视频 + 主流程自动跑 analysis
    include_soft_video: bool = True
    include_analysis_in_main_flow: bool = True

    project_type: str = "translate_lab"

    # ------------------------------------------------------------------
    # 步骤清单与主循环
    # ------------------------------------------------------------------

    def _build_steps(
        self, task_id: str, video_path: str, task_dir: str,
    ) -> List[Tuple[str, Callable[[], None]]]:
        """返回 (step_name, step_callable) 列表，供 _run 或测试使用。"""
        return [
            ("extract",        lambda: self._step_extract(task_id, video_path, task_dir)),
            ("shot_decompose", lambda: self._step_shot_decompose(task_id, video_path, task_dir)),
            ("voice_match",    lambda: self._step_voice_match(task_id, video_path, task_dir)),
            ("translate",      lambda: self._step_translate(task_id)),
            ("tts_verify",     lambda: self._step_tts_verify(task_id, task_dir)),
            ("subtitle",       lambda: self._step_subtitle(task_id, task_dir)),
            ("compose",        lambda: self._step_compose(task_id, video_path, task_dir)),
        ]

    def _run(self, task_id: str, start_step: str = "extract") -> None:
        task = task_state.get(task_id) or {}
        video_path = task.get("video_path", "")
        task_dir = task.get("task_dir", "")
        steps = self._build_steps(task_id, video_path, task_dir)

        try:
            should_run = False
            for step_name, step_fn in steps:
                if step_name == start_step:
                    should_run = True
                if not should_run:
                    continue
                step_fn()
                current = task_state.get(task_id) or {}
                if (current.get("steps") or {}).get(step_name) == "waiting":
                    return
        except Exception as exc:
            log.warning("[runtime_v2] pipeline failed task=%s error=%s",
                        task_id, exc, exc_info=True)
            task_state.update(task_id, status="error", error=str(exc))
            task_state.set_expires_at(task_id, self.project_type)
            self._emit(task_id, EVT_LAB_PIPELINE_ERROR, {"error": str(exc)})

    # ------------------------------------------------------------------
    # Step 1: extract
    # ------------------------------------------------------------------

    def _step_extract(self, task_id: str, video_path: str, task_dir: str) -> None:
        from pipeline.extract import extract_audio
        from pipeline.ffutil import probe_media_info

        self._set_step(task_id, "extract", "running", "正在提取音频...")
        audio_path = extract_audio(video_path, task_dir)
        info = probe_media_info(video_path) or {}
        task_state.update(
            task_id,
            audio_path=audio_path,
            video_duration=float(info.get("duration") or 0.0),
            video_width=int(info.get("width") or 0),
            video_height=int(info.get("height") or 0),
        )
        self._set_step(task_id, "extract", "done", "音频提取完成")

    # ------------------------------------------------------------------
    # Step 2: shot_decompose
    # ------------------------------------------------------------------

    def _step_shot_decompose(
        self, task_id: str, video_path: str, task_dir: str,
    ) -> None:
        from pipeline.asr import transcribe
        from pipeline.shot_decompose import align_asr_to_shots, decompose_shots
        from pipeline.storage import delete_file, upload_file

        self._set_step(task_id, "shot_decompose", "running", "Gemini 分镜分析中...")
        task = task_state.get(task_id) or {}
        duration = float(task.get("video_duration") or 0.0)
        audio_path = task.get("audio_path", "")

        # Gemini 分镜
        shots = decompose_shots(
            video_path,
            user_id=self.user_id,
            duration_seconds=duration,
        )

        # ASR：先上传到 TOS 再识别
        volc_api_key = resolve_key(self.user_id, "volc", "VOLC_API_KEY")
        tos_key = f"asr-audio/{task_id}_{uuid.uuid4().hex[:8]}.wav"
        asr_segments: List[Dict[str, Any]] = []
        if audio_path:
            audio_url = upload_file(audio_path, tos_key)
            try:
                utterances = transcribe(audio_url, volc_api_key=volc_api_key) or []
            finally:
                try:
                    delete_file(tos_key)
                except Exception:
                    pass
            # 把豆包返回的 start_time/end_time 归一成 start/end
            for utt in utterances:
                asr_segments.append({
                    "start": float(utt.get("start_time") or utt.get("start") or 0.0),
                    "end": float(utt.get("end_time") or utt.get("end") or 0.0),
                    "text": utt.get("text", ""),
                })

        aligned = align_asr_to_shots(shots, asr_segments)
        task_state.update(task_id, shots=aligned)
        self._emit(task_id, EVT_LAB_SHOT_DECOMPOSE_RESULT, {"shots": aligned})
        self._set_step(task_id, "shot_decompose", "done",
                       f"分镜完成，共 {len(aligned)} 段")

    # ------------------------------------------------------------------
    # Step 3: voice_match
    # ------------------------------------------------------------------

    def _step_voice_match(
        self, task_id: str, video_path: str, task_dir: str,
    ) -> None:
        from pipeline.speech_rate_model import get_rate, initialize_baseline
        from pipeline.voice_match import match_for_video

        self._set_step(task_id, "voice_match", "running", "正在匹配音色...")
        task = task_state.get(task_id) or {}
        mode = task.get("voice_match_mode", "auto")
        target_lang = task.get("target_language", "en")
        gender = task.get("voice_gender")

        candidates = match_for_video(
            video_path=video_path,
            language=target_lang,
            gender=gender,
            top_k=3,
            out_dir=os.path.join(task_dir, "voice_match"),
        )
        task_state.update(task_id, voice_candidates=candidates)
        self._emit(task_id, EVT_LAB_VOICE_MATCH_CANDIDATES,
                   {"candidates": candidates})

        if mode == "auto":
            chosen = candidates[0] if candidates else None
        else:
            chosen = self._await_voice_confirmation(task_id, candidates)

        if not chosen:
            raise RuntimeError("未确定音色")

        # 初始化语速基准（若尚未存在）
        api_key = resolve_key(self.user_id, "elevenlabs", "ELEVENLABS_API_KEY")
        if get_rate(chosen["voice_id"], target_lang) is None and api_key:
            try:
                initialize_baseline(
                    chosen["voice_id"], target_lang,
                    api_key=api_key,
                    work_dir=os.path.join(task_dir, "voice_match"),
                )
            except Exception as exc:
                log.warning("[runtime_v2] 语速基准初始化失败 voice=%s lang=%s err=%s",
                            chosen.get("voice_id"), target_lang, exc)

        task_state.update(task_id, chosen_voice=chosen, pending_voice_choice=None)
        self._emit(task_id, EVT_LAB_VOICE_CONFIRMED, {"voice": chosen})
        self._set_step(task_id, "voice_match", "done", "音色已确定")

    def _await_voice_confirmation(
        self,
        task_id: str,
        candidates: List[Dict[str, Any]],
        *,
        poll_interval: float = 1.0,
        timeout_seconds: float = 1800,
    ) -> Optional[Dict[str, Any]]:
        """阻塞等待前端确认音色；返回 chosen dict 或 None（超时）。

        poll_interval == 0 时视作"零延迟轮询"，用 timeout_seconds 作为
        最大迭代次数上限，便于单元测试驱动。
        """
        task_state.update(
            task_id,
            pending_voice_choice=candidates,
            status="awaiting_voice",
        )
        if poll_interval <= 0:
            max_iters = int(timeout_seconds)
            for _ in range(max_iters):
                t = task_state.get(task_id) or {}
                chosen = t.get("chosen_voice")
                if chosen:
                    return chosen
                time.sleep(0)
            return None

        waited = 0.0
        while waited <= timeout_seconds:
            t = task_state.get(task_id) or {}
            chosen = t.get("chosen_voice")
            if chosen:
                return chosen
            time.sleep(poll_interval)
            waited += poll_interval
        return None

    # ------------------------------------------------------------------
    # Step 4: translate
    # ------------------------------------------------------------------

    def _step_translate(self, task_id: str) -> None:
        from pipeline.speech_rate_model import get_rate
        from pipeline.translate_v2 import compute_char_limit, translate_shot

        self._set_step(task_id, "translate", "running", "正在翻译分镜...")
        task = task_state.get(task_id) or {}
        shots: List[Dict[str, Any]] = task.get("shots") or []
        voice = task.get("chosen_voice") or {}
        target_lang = task.get("target_language", "en")
        default_cps = 15.0
        cps = get_rate(voice.get("voice_id"), target_lang) or default_cps

        translations: List[Dict[str, Any]] = []
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
            limit = compute_char_limit(
                float(shot.get("duration") or 0.0), cps,
            )
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
                user_id=self.user_id,
            )
            translations.append(result)
            self._emit(task_id, EVT_LAB_TRANSLATE_PROGRESS, {
                "index": shot.get("index"),
                "result": result,
            })

        task_state.update(task_id, translations=translations)
        self._set_step(task_id, "translate", "done", "分镜翻译完成")

    # ------------------------------------------------------------------
    # Step 5: tts_verify
    # ------------------------------------------------------------------

    def _step_tts_verify(self, task_id: str, task_dir: str) -> None:
        from pipeline.tts_v2 import generate_and_verify_shot

        self._set_step(task_id, "tts_verify", "running", "正在生成配音并校验时长...")
        task = task_state.get(task_id) or {}
        translations_by_idx = {
            t.get("shot_index"): t for t in (task.get("translations") or [])
        }
        shots: List[Dict[str, Any]] = task.get("shots") or []
        voice = task.get("chosen_voice") or {}
        target_lang = task.get("target_language", "en")
        api_key = resolve_key(self.user_id, "elevenlabs", "ELEVENLABS_API_KEY")
        tts_dir = os.path.join(task_dir, "tts_v2")

        results: List[Dict[str, Any]] = []
        for shot in shots:
            tr = translations_by_idx.get(shot.get("index"))
            if not tr or not (tr.get("translated_text") or "").strip():
                continue
            verified = generate_and_verify_shot(
                shot=shot,
                translated_text=tr["translated_text"],
                voice_id=voice.get("voice_id", ""),
                api_key=api_key or "",
                language=target_lang,
                user_id=self.user_id,
                out_dir=tts_dir,
            )
            results.append(verified)
            self._emit(task_id, EVT_LAB_TTS_PROGRESS, {
                "index": shot.get("index"),
                "result": verified,
            })

        task_state.update(task_id, tts_results=results)
        self._set_step(task_id, "tts_verify", "done", "配音生成完成")

    # ------------------------------------------------------------------
    # Step 6: subtitle
    # ------------------------------------------------------------------

    def _step_subtitle(self, task_id: str, task_dir: str) -> None:
        from pipeline.subtitle_v2 import (
            compute_unified_font_size,
            generate_srt,
        )

        self._set_step(task_id, "subtitle", "running", "正在生成字幕...")
        task = task_state.get(task_id) or {}
        shots: List[Dict[str, Any]] = task.get("shots") or []
        tts_by_idx = {
            r.get("shot_index"): r for r in (task.get("tts_results") or [])
        }

        # 把 TTS 结果合并到分镜（final_text / final_duration / audio_path）
        final_shots: List[Dict[str, Any]] = []
        for shot in shots:
            merged = dict(shot)
            r = tts_by_idx.get(shot.get("index"))
            if r:
                merged.update({
                    "final_text": r.get("final_text", ""),
                    "final_duration": float(r.get("final_duration") or 0.0),
                    "audio_path": r.get("audio_path", ""),
                })
            else:
                merged.setdefault("final_text", "")
                merged.setdefault("final_duration", 0.0)
            final_shots.append(merged)

        width = int(task.get("video_width") or 1920)
        height = int(task.get("video_height") or 1080)
        font_size = compute_unified_font_size(
            final_shots, video_width=width, video_height=height,
        )
        avg_char_width = max(1.0, font_size * 0.55)
        max_chars = max(1, int((width * 0.8) / avg_char_width))
        srt_text = generate_srt(
            final_shots,
            font_size=font_size,
            max_chars_per_line=max_chars,
        )
        srt_path = os.path.join(task_dir, "subtitles.srt")
        with open(srt_path, "w", encoding="utf-8") as fh:
            fh.write(srt_text)

        task_state.update(
            task_id,
            subtitle_path=srt_path,
            font_size=font_size,
            max_chars_per_line=max_chars,
            final_shots=final_shots,
        )
        task_state.set_preview_file(task_id, "srt", srt_path)
        self._emit(task_id, EVT_LAB_SUBTITLE_READY, {
            "srt_path": srt_path,
            "font_size": font_size,
        })
        self._set_step(task_id, "subtitle", "done", "字幕生成完成")

    # ------------------------------------------------------------------
    # Step 7: compose
    # ------------------------------------------------------------------

    def _step_compose(
        self, task_id: str, video_path: str, task_dir: str,
    ) -> None:
        """合成最终视频。

        V2 流水线输出的是"每个分镜一段独立 MP3"；本步骤先把它们按分镜时间轴
        拼成一段整体音轨（中间空白处自然为静音），再复用
        ``pipeline.compose.compose_video`` 生成软/硬字幕版视频。
        """
        from pipeline.audio_stitch import (
            build_stitched_audio,
            build_timeline_manifest,
        )
        from pipeline.compose import compose_video

        self._set_step(task_id, "compose", "running", "正在合成最终视频...")
        task = task_state.get(task_id) or {}
        tts_results = task.get("tts_results") or []
        if not tts_results:
            raise RuntimeError("没有可合成的 TTS 分段")

        shots_by_idx = {
            s.get("index"): s for s in (task.get("shots") or [])
        }
        segments: List[Dict[str, Any]] = []
        for tts in tts_results:
            shot = shots_by_idx.get(tts.get("shot_index"))
            if not shot:
                continue
            audio_path = tts.get("audio_path")
            if not audio_path:
                continue
            segments.append({
                "shot_index": tts.get("shot_index"),
                "shot_start": float(shot.get("start") or 0.0),
                "shot_duration": float(shot.get("duration") or 0.0),
                "actual_duration": float(tts.get("final_duration") or 0.0),
                "audio_path": audio_path,
            })

        if not segments:
            raise RuntimeError("没有可合成的分镜音频")

        stitched_path = os.path.join(task_dir, "stitched_audio.mp3")
        build_stitched_audio(
            segments,
            total_duration=float(task.get("video_duration") or 0.0),
            output_path=stitched_path,
        )
        timeline = build_timeline_manifest(segments)

        subtitle_path = task.get("subtitle_path")
        result = compose_video(
            video_path=video_path,
            tts_audio_path=stitched_path,
            srt_path=subtitle_path,
            output_dir=task_dir,
            subtitle_position=task.get("subtitle_position", "bottom"),
            timeline_manifest=timeline,
            variant=task.get("variant"),
            font_name=task.get("font_name", "Impact"),
            font_size_preset=task.get("font_size_preset", "medium"),
        )
        final_path = (
            (result or {}).get("hard_video")
            or (result or {}).get("soft_video")
        )
        task_state.update(
            task_id,
            final_video=final_path,
            compose_result=result,
            stitched_audio_path=stitched_path,
            status="completed",
        )
        task_state.set_expires_at(task_id, self.project_type)
        if result:
            if result.get("soft_video"):
                task_state.set_preview_file(task_id, "soft_video",
                                            result["soft_video"])
            if result.get("hard_video"):
                task_state.set_preview_file(task_id, "hard_video",
                                            result["hard_video"])
        self._emit(task_id, EVT_LAB_PIPELINE_DONE, {
            "video": final_path,
            "compose_result": result,
        })
        self._set_step(task_id, "compose", "done", "合成完成")
