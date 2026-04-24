"""appcore/copywriting_runtime.py
文案创作管线编排器。

管线步骤：keyframe → copywrite → [人工确认] → tts → compose
"""

from __future__ import annotations

import json
import logging
import os

from appcore.events import (
    EventBus, Event,
    EVT_CW_STEP_UPDATE, EVT_CW_KEYFRAMES_READY, EVT_CW_COPY_READY,
    EVT_CW_TTS_READY, EVT_CW_COMPOSE_READY, EVT_CW_DONE, EVT_CW_ERROR,
)
from appcore import ai_billing, task_state
from appcore.api_keys import resolve_key, resolve_extra
from appcore.db import get_conn as get_connection

log = logging.getLogger(__name__)


def _billing_provider(provider: str) -> str:
    return "doubao" if provider == "doubao" else "openrouter"


class CopywritingRunner:
    """文案创作管线运行器。"""

    def __init__(self, bus: EventBus, user_id: int | None = None):
        self._bus = bus
        self._user_id = user_id

    # ── 事件辅助 ─────────────────────────────────────

    def _emit(self, task_id: str, event_type: str, payload: dict | None = None):
        self._bus.publish(Event(type=event_type, task_id=task_id,
                                payload=payload or {}))

    def _set_step(self, task_id: str, step: str, status: str, message: str = "", *, model_tag: str = ""):
        task_state.set_step(task_id, step, status)
        if message:
            task_state.set_step_message(task_id, step, message)
        if model_tag:
            task_state.set_step_model_tag(task_id, step, model_tag)
        payload = {"step": step, "status": status, "message": message}
        existing_tag = model_tag or (task_state.get(task_id) or {}).get("step_model_tags", {}).get(step, "")
        if existing_tag:
            payload["model_tag"] = existing_tag
        self._emit(task_id, EVT_CW_STEP_UPDATE, payload)

    # ── 公开接口 ─────────────────────────────────────

    def start(self, task_id: str):
        """启动管线：仅抽帧，等待用户确认配置后再生成文案。"""
        log.info("[CW] start() 被调用, task_id=%s", task_id)
        task = task_state.get(task_id)
        if not task:
            log.warning("[CW] task_id=%s 不存在，跳过", task_id)
            return
        task_state.update(task_id, status="running")
        try:
            self._step_keyframe(task_id)
        except Exception:
            log.exception("[CW] 文案管线异常: %s", task_id)
            task_state.update(task_id, status="error")
            task_state.set_expires_at(task_id, "copywriting")
            self._emit(task_id, EVT_CW_ERROR, {"message": "抽帧失败"})

    def generate_copy(self, task_id: str):
        """单独触发文案生成（重新生成）。"""
        log.info("[CW] generate_copy() 被调用, task_id=%s", task_id)
        try:
            self._step_copywrite(task_id)
        except Exception:
            log.exception("[CW] 文案生成异常: %s", task_id)
            self._set_step(task_id, "copywrite", "error", "文案生成失败")
            self._emit(task_id, EVT_CW_ERROR, {"message": "文案生成失败"})

    def start_tts_compose(self, task_id: str):
        """用户确认文案后，触发 TTS → 合成。"""
        task = task_state.get(task_id)
        if not task:
            return
        try:
            self._step_tts(task_id)
            self._step_compose(task_id)
            task_state.update(task_id, status="done")
            task_state.set_expires_at(task_id, "copywriting")
            self._emit(task_id, EVT_CW_DONE, {})
        except Exception:
            log.exception("TTS/合成异常: %s", task_id)
            task_state.update(task_id, status="error")
            task_state.set_expires_at(task_id, "copywriting")
            self._emit(task_id, EVT_CW_ERROR, {"message": "TTS/合成失败"})

    # ── 管线步骤 ─────────────────────────────────────

    def _step_keyframe(self, task_id: str):
        from pipeline.keyframe import extract_keyframes

        self._set_step(task_id, "keyframe", "running", "正在抽取关键帧...")
        task = task_state.get(task_id)
        video_path = task["video_path"]
        task_dir = task["task_dir"]
        keyframe_dir = os.path.join(task_dir, "keyframes")

        frame_paths = extract_keyframes(video_path, keyframe_dir)
        task_state.set_keyframes(task_id, frame_paths)
        self._set_step(task_id, "keyframe", "done",
                       f"已抽取 {len(frame_paths)} 帧关键帧")
        self._emit(task_id, EVT_CW_KEYFRAMES_READY, {
            "keyframes": frame_paths,
            "count": len(frame_paths),
        })

    def _step_copywrite(self, task_id: str):
        from pipeline.copywriting import generate_copy

        task = task_state.get(task_id)
        keyframes = task.get("keyframes", [])
        video_path = task.get("video_path")

        # 从数据库读取商品信息
        product_inputs = self._load_product_inputs(task_id)
        language = product_inputs.get("language", "en")

        # 解析 provider（优先用任务级别的选择）
        provider = task.get("cw_provider") or self._resolve_provider()

        # 解析用户自定义提示词
        custom_prompt = self._load_user_prompt(task_id, language)

        # 模型覆盖（如 Gemini）
        model_override = task.get("cw_model")
        _cw_model_label = model_override or provider
        self._set_step(task_id, "copywrite", "running", "正在生成文案...",
                       model_tag=f"{provider} · {_cw_model_label}")

        result = generate_copy(
            keyframe_paths=keyframes,
            product_inputs=product_inputs,
            provider=provider,
            user_id=self._user_id,
            custom_system_prompt=custom_prompt,
            language=language,
            video_path=video_path,
            model_override=model_override,
        )

        task_state.set_copy(task_id, result)
        # 用实际返回的 model 更新 tag
        _actual_model = (result.get("_debug") or {}).get("model") or model_override or provider
        self._set_step(task_id, "copywrite", "done",
                       f"文案生成完成: {len(result.get('segments', []))} 段",
                       model_tag=f"{provider} · {_actual_model}")
        _cw_usage = result.get("_usage") or {}
        ai_billing.log_request(
            use_case_code="copywriting.generate",
            user_id=self._user_id,
            project_id=task_id,
            provider=_billing_provider(provider),
            model=_actual_model,
            input_tokens=_cw_usage.get("input_tokens"),
            output_tokens=_cw_usage.get("output_tokens"),
            units_type="tokens",
            response_cost_cny=_cw_usage.get("cost_cny"),
            success=True,
            request_payload=(result.get("_debug") or {}).get("full_request"),
            response_payload={k: v for k, v in result.items() if not str(k).startswith("_")},
        )
        self._emit(task_id, EVT_CW_COPY_READY, {"copy": result})

    def _step_tts(self, task_id: str):
        from pipeline.tts import generate_full_audio, get_voice_by_id, get_default_voice

        self._set_step(task_id, "tts", "running", "正在生成语音...")
        task = task_state.get(task_id)
        task_dir = task["task_dir"]
        copy_data = task.get("copy", {})

        # 构建 TTS segments
        tts_segments = []
        for seg in copy_data.get("segments", []):
            tts_segments.append({
                "index": seg.get("index", 0),
                "tts_text": seg["text"],
            })

        # 获取 voice
        voice_id = task.get("voice_id")
        if voice_id:
            voice = get_voice_by_id(voice_id, self._user_id)
        else:
            voice = get_default_voice(self._user_id)

        elevenlabs_key = resolve_key(self._user_id, "elevenlabs", "ELEVENLABS_API_KEY")

        result = generate_full_audio(
            segments=tts_segments,
            voice_id=voice["elevenlabs_voice_id"],
            output_dir=task_dir,
            variant="copywriting",
            elevenlabs_api_key=elevenlabs_key,
        )

        task_state.update(task_id, tts_audio_path=result["full_audio_path"])
        task_state.set_artifact(task_id, "tts", {
            "audio_path": result["full_audio_path"],
            "segments": result["segments"],
        })
        self._set_step(task_id, "tts", "done", "语音生成完成")
        self._emit(task_id, EVT_CW_TTS_READY, {
            "audio_path": result["full_audio_path"],
        })

    def _step_compose(self, task_id: str):
        from pipeline.compose import compose_video

        self._set_step(task_id, "compose", "running", "正在合成视频...")
        task = task_state.get(task_id)
        video_path = task["video_path"]
        task_dir = task["task_dir"]
        tts_audio_path = task.get("tts_audio_path", "")

        result = compose_video(
            video_path=video_path,
            tts_audio_path=tts_audio_path,
            srt_path=None,
            output_dir=task_dir,
            subtitle_position="bottom",
            timeline_manifest=None,
            variant="copywriting",
        )

        task_state.update(task_id, result=result)
        self._set_step(task_id, "compose", "done", "视频合成完成")
        self._emit(task_id, EVT_CW_COMPOSE_READY, {"result": result})

    # ── 内部辅助 ─────────────────────────────────────

    def _load_product_inputs(self, task_id: str) -> dict:
        """从 copywriting_inputs 表加载商品信息。"""
        # get_connection imported at module level
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT product_title, product_image_url, price, "
                    "selling_points, target_audience, extra_info, language "
                    "FROM copywriting_inputs WHERE project_id = %s",
                    (task_id,),
                )
                row = cur.fetchone()
                if not row:
                    return {"language": "en"}
                return dict(row)
        finally:
            conn.close()

    def _resolve_provider(self) -> str:
        """解析用户偏好的 LLM provider。"""
        try:
            extra = resolve_extra(self._user_id, "translate_preference")
            if extra and extra.get("provider"):
                return extra["provider"]
        except Exception:
            pass
        return "openrouter"

    def _load_user_prompt(self, task_id: str, language: str) -> str | None:
        """加载用户选择的文案提示词，返回 None 则用默认。"""
        # 优先从任务状态中读取 prompt_id
        task = task_state.get(task_id)
        prompt_id = task.get("prompt_id") if task else None
        if not prompt_id:
            return None

        # get_connection imported at module level
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT prompt_text, prompt_text_zh FROM user_prompts "
                    "WHERE id = %s AND user_id = %s AND type = 'copywriting'",
                    (prompt_id, self._user_id),
                )
                row = cur.fetchone()
                if not row:
                    return None
                if language == "zh" and row.get("prompt_text_zh"):
                    return row["prompt_text_zh"]
                return row.get("prompt_text")
        finally:
            conn.close()
