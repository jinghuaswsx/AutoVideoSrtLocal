"""
分镜级 TTS 生成 + 时长校验 + 文案微调循环
"""
from __future__ import annotations

import os
from typing import Any, Dict

from appcore.gemini import generate as gemini_generate
from pipeline.speech_rate_model import get_rate, update_rate

TOLERANCE = 1.10  # 允许实际音频时长 ≤ 分镜时长 × 1.10

REFINE_SCHEMA = {
    "type": "object",
    "properties": {"translated_text": {"type": "string"}},
    "required": ["translated_text"],
}

REFINE_PROMPT = (
    "上一版译文「{previous}」生成的音频比分镜长 {over_pct}%。\n"
    "请缩写为约 {target_chars} 字符，保留核心语义，只删修饰性内容。\n"
    "以 JSON 输出：{{\"translated_text\": \"...\"}}"
)


def _tts_generate(text: str, voice_id: str, output_path: str,
                  api_key: str) -> str:
    """调用 ElevenLabs 生成 MP3，返回路径。"""
    from pipeline.tts import generate_segment_audio
    generate_segment_audio(
        text=text, voice_id=voice_id,
        output_path=output_path, elevenlabs_api_key=api_key,
    )
    return output_path


def _get_duration(path: str) -> float:
    from pipeline.tts import get_audio_duration
    return get_audio_duration(path)


def _refine_text(previous_text: str, over_ratio: float,
                 target_chars: int, user_id: int) -> str:
    prompt = REFINE_PROMPT.format(
        previous=previous_text,
        over_pct=int(round(over_ratio * 100)),
        target_chars=target_chars,
    )
    resp = gemini_generate(
        prompt,
        user_id=user_id,
        response_schema=REFINE_SCHEMA,
        service="gemini_tts_refine",
    )
    return (resp or {}).get("translated_text", "").strip()


def generate_and_verify_shot(
    shot: Dict[str, Any],
    *,
    translated_text: str,
    voice_id: str,
    api_key: str,
    language: str,
    user_id: int,
    out_dir: str,
    max_retries: int = 3,
) -> Dict[str, Any]:
    """生成 TTS → 校验时长 → 超限则微调文案重试，最多 max_retries 轮。"""
    os.makedirs(out_dir, exist_ok=True)
    shot_duration = float(shot.get("duration", 0.0))
    limit_seconds = shot_duration * TOLERANCE
    current_text = translated_text
    audio_path = os.path.join(out_dir, f"shot_{shot['index']}.mp3")

    retry_count = 0
    final_duration = 0.0
    over_tolerance = False

    for attempt in range(max_retries + 1):
        _tts_generate(current_text, voice_id, audio_path, api_key)
        final_duration = _get_duration(audio_path)

        # 增量更新语速模型（含所有尝试样本）
        update_rate(voice_id, language,
                    chars=len(current_text),
                    duration_seconds=final_duration)

        if final_duration <= limit_seconds:
            break
        if attempt >= max_retries:
            over_tolerance = True
            break

        over_ratio = ((final_duration - shot_duration) / shot_duration
                      if shot_duration > 0 else 0.0)
        cps = get_rate(voice_id, language) or (
            len(current_text) / final_duration if final_duration > 0 else 15.0
        )
        target_chars = max(1, int(shot_duration * 0.9 * cps))
        current_text = _refine_text(current_text, over_ratio, target_chars,
                                     user_id)
        retry_count += 1

    return {
        "shot_index": shot["index"],
        "final_text": current_text,
        "final_char_count": len(current_text),
        "final_duration": final_duration,
        "audio_path": audio_path,
        "retry_count": retry_count,
        "over_tolerance": over_tolerance,
    }
