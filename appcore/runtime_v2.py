"""视频翻译（测试）流水线 V2。

PipelineRunnerV2 编排 9 步流水线（复刻多语种大逻辑 + 分镜精确翻译）：
  extract -> asr -> shot_decompose -> voice_match -> translate
    -> tts -> subtitle -> compose -> export

翻译步骤用分镜级字符上限约束，确保初始译文尽可能贴合分镜时长，
后续 TTS 时长迭代循环（继承自多语种基类）可以更快收敛。

模块级只依赖 appcore，pipeline.* 在步骤函数内部延迟导入，
避免测试时连锁加载 ElevenLabs / Gemini 客户端。
"""
from __future__ import annotations

import logging
import os
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

from appcore import task_state
from appcore.api_keys import resolve_key
from appcore.cancellation import (
    OperationCancelled,
    cancellable_sleep,
    throw_if_cancel_requested,
)
from appcore.events import (
    EVT_LAB_PIPELINE_DONE,
    EVT_LAB_PIPELINE_ERROR,
    EVT_LAB_SHOT_DECOMPOSE_RESULT,
    EVT_LAB_TRANSLATE_PROGRESS,
    EVT_LAB_VOICE_CONFIRMED,
    EVT_LAB_VOICE_MATCH_CANDIDATES,
)
from appcore.runtime import PipelineRunner

log = logging.getLogger(__name__)


class PipelineRunnerV2(PipelineRunner):
    """视频翻译（测试）模块的流水线 runner。

    继承多语种基类的 TTS 时长迭代、字幕生成、视频合成、CapCut 导出，
    仅在 ASR 之后插入分镜分析 + 分镜级翻译。
    """

    include_soft_video: bool = True
    include_analysis_in_main_flow: bool = False

    project_type: str = "translate_lab"

    # ------------------------------------------------------------------
    # Voice 解析：优先使用 voice_match 步骤匹配到的音色
    # ------------------------------------------------------------------

    def _resolve_voice(self, task: dict, loc_mod) -> dict:
        chosen = task.get("chosen_voice") or {}
        if chosen.get("voice_id"):
            return {
                "id": None,
                "elevenlabs_voice_id": chosen["voice_id"],
                "name": chosen.get("name", "Matched Voice"),
            }
        return super()._resolve_voice(task, loc_mod)

    # ------------------------------------------------------------------
    # 步骤清单与主循环
    # ------------------------------------------------------------------

    def _build_steps(
        self, task_id: str, video_path: str, task_dir: str,
    ) -> List[Tuple[str, Callable[[], None]]]:
        """返回 (step_name, step_callable) 列表。"""
        return [
            ("extract",        lambda: self._step_extract(task_id, video_path, task_dir)),
            ("asr",            lambda: self._step_asr(task_id, task_dir)),
            ("shot_decompose", lambda: self._step_shot_decompose(task_id, video_path, task_dir)),
            ("voice_match",    lambda: self._step_voice_match(task_id, video_path, task_dir)),
            ("translate",      lambda: self._step_translate(task_id)),
            ("tts",            lambda: self._step_tts(task_id, task_dir)),
            ("subtitle",       lambda: self._step_subtitle(task_id, task_dir)),
            ("compose",        lambda: self._step_compose(task_id, video_path, task_dir)),
            ("export",         lambda: self._step_export(task_id, video_path, task_dir)),
        ]

    def _run(self, task_id: str, start_step: str = "extract") -> None:
        # 从任务读取目标语言，动态设置基类属性
        task_init = task_state.get(task_id) or {}
        self.target_language_label = task_init.get("target_language", "en")

        # Guard: fail early if the local source video has not been materialized.
        try:
            from appcore.source_video import ensure_local_source_video
            ensure_local_source_video(task_id)
        except Exception as exc:
            task_state.update(task_id, status="error", error=str(exc))
            task_state.set_expires_at(task_id, self.project_type)
            self._emit(task_id, EVT_LAB_PIPELINE_ERROR, {"error": str(exc)})
            return

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
                # Cooperative cancellation checkpoint -- aligned with
                # PipelineRunner._run; lets graceful shutdown drop the
                # remaining steps when systemd / Gunicorn signals SIGTERM.
                throw_if_cancel_requested(f"runtime_v2 step={step_name}")
                step_fn()
                current = task_state.get(task_id) or {}
                if (current.get("steps") or {}).get(step_name) == "waiting":
                    return

            # 所有步骤完成
            final = task_state.get(task_id) or {}
            self._emit(task_id, EVT_LAB_PIPELINE_DONE, {
                "task_id": task_id,
                "video": final.get("final_video"),
            })
        except OperationCancelled as exc:
            log.warning("[runtime_v2] cancelled task=%s reason=%s", task_id, exc)
            self._mark_pipeline_interrupted_v2(task_id, str(exc))
            raise
        except Exception as exc:
            log.warning("[runtime_v2] pipeline failed task=%s error=%s",
                        task_id, exc, exc_info=True)
            task_state.update(task_id, status="error", error=str(exc))
            task_state.set_expires_at(task_id, self.project_type)
            self._emit(task_id, EVT_LAB_PIPELINE_ERROR, {"error": str(exc)})

    def _mark_pipeline_interrupted_v2(self, task_id: str, reason: str) -> None:
        """Mark V2 task as ``interrupted`` and emit the lab pipeline error event.

        V2 has its own EVT_LAB_PIPELINE_ERROR; otherwise the bookkeeping
        mirrors PipelineRunner._mark_pipeline_interrupted -- queued /
        running / pending step states become ``interrupted`` while
        terminal states (done / failed / error) are preserved.
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
            self._emit(task_id, EVT_LAB_PIPELINE_ERROR, {
                "error": f"cancelled: {reason}",
                "cancelled": True,
            })
        except Exception:
            log.warning("emit lab pipeline_error during cancellation failed", exc_info=True)

    # ------------------------------------------------------------------
    # Step 1: extract（含视频元数据）
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
    # Step 2: asr（继承基类 ASR）
    # ------------------------------------------------------------------
    # 直接使用 PipelineRunner._step_asr

    # ------------------------------------------------------------------
    # Step 3: shot_decompose（Gemini 分镜 + ASR 对齐）
    # ------------------------------------------------------------------

    def _step_shot_decompose(
        self, task_id: str, video_path: str, task_dir: str,
    ) -> None:
        from pipeline.shot_decompose import align_asr_to_shots, decompose_shots

        from appcore.gemini import resolve_config as _resolve_gemini
        _, _sd_model = _resolve_gemini(self.user_id, service="shot_decompose.run",
                                        default_model="gemini-3.1-pro-preview")
        self._set_step(task_id, "shot_decompose", "running", "Gemini 分镜分析中...",
                       model_tag=f"gemini · {_sd_model}")
        task = task_state.get(task_id) or {}
        duration = float(task.get("video_duration") or 0.0)

        # Gemini 分镜
        shots = decompose_shots(
            video_path,
            user_id=self.user_id,
            duration_seconds=duration,
        )

        # 使用 ASR 步骤的结果（utterances）做时间对齐
        utterances = task.get("utterances") or []
        asr_segments: List[Dict[str, Any]] = []
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
    # Step 4: voice_match
    # ------------------------------------------------------------------

    def _step_voice_match(
        self, task_id: str, video_path: str, task_dir: str,
    ) -> None:
        from pipeline.speech_rate_model import get_rate, initialize_baseline
        from pipeline.voice_match import DEFAULT_VOICE_MATCH_TOP_K, match_for_video

        self._set_step(task_id, "voice_match", "running", "正在匹配音色...")
        task = task_state.get(task_id) or {}
        mode = task.get("voice_match_mode", "auto")
        target_lang = task.get("target_language", "en")
        gender = task.get("voice_gender")

        candidates = match_for_video(
            video_path=video_path,
            language=target_lang,
            gender=gender,
            top_k=DEFAULT_VOICE_MATCH_TOP_K,
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
        """阻塞等待前端确认音色；返回 chosen dict 或 None（超时）。"""
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
                cancellable_sleep(0)
            return None

        waited = 0.0
        while waited <= timeout_seconds:
            t = task_state.get(task_id) or {}
            chosen = t.get("chosen_voice")
            if chosen:
                return chosen
            cancellable_sleep(poll_interval)
            waited += poll_interval
        return None

    # ------------------------------------------------------------------
    # Step 5: translate（分镜级翻译 + 构建多语种兼容数据结构）
    # ------------------------------------------------------------------

    def _step_translate(self, task_id: str) -> None:
        from pipeline.speech_rate_model import get_rate
        from pipeline.translate_v2 import compute_char_limit, translate_shot

        from appcore.gemini import resolve_config as _resolve_gemini
        _, _tr_model = _resolve_gemini(self.user_id, service="translate_lab.shot_translate")
        self._set_step(task_id, "translate", "running", "正在翻译分镜...",
                       model_tag=f"gemini · {_tr_model}")
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

        # ── 构建多语种基类 TTS 需要的数据结构 ──

        # localized_translation: 供 TTS 时长迭代循环使用
        sentences = []
        for i, tr in enumerate(translations):
            if tr.get("translated_text"):
                sentences.append({
                    "index": i,
                    "text": tr["translated_text"],
                    "source_segment_indices": [i],
                })
        localized_translation = {
            "full_text": "\n".join(
                tr["translated_text"] for tr in translations
                if tr.get("translated_text")
            ),
            "sentences": sentences,
        }

        # script_segments: 原文分段时间轴
        script_segments = []
        for shot in shots:
            script_segments.append({
                "index": shot.get("index"),
                "text": shot.get("source_text", ""),
                "start_time": float(shot.get("start") or 0.0),
                "end_time": float(shot.get("end") or 0.0),
            })

        # source_full_text: 拼接原文
        source_full_text = "\n".join(
            shot.get("source_text", "") for shot in shots
            if shot.get("source_text")
        )

        # variants: 基类 TTS/subtitle/compose/export 读写的核心结构
        variants = {
            "normal": {
                "localized_translation": localized_translation,
            },
        }

        task_state.update(
            task_id,
            localized_translation=localized_translation,
            script_segments=script_segments,
            source_full_text=source_full_text,
            source_language="zh",
            variants=variants,
        )
        self._set_step(task_id, "translate", "done", "分镜翻译完成")

    # ------------------------------------------------------------------
    # Step 6-9: tts / subtitle / compose / export
    # 直接继承 PipelineRunner 基类的实现
    # ------------------------------------------------------------------
