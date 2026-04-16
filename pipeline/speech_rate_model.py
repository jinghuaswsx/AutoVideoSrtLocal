"""
语速模型：存储/读取各音色的字符/秒速率；支持基准初始化与增量更新
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Optional, Tuple

from appcore.db import execute, query_one

BENCHMARK_TEXT = {
    "en": "The quick brown fox jumps over the lazy dog. Bright sunlight filtered through the autumn leaves as she walked along the quiet riverside path, lost in thought.",
    "de": "Der Computer ist ein wichtiges Werkzeug im modernen Leben. Die Sonne scheint heute hell, und der Wind weht sanft durch die Bäume im Garten.",
    "fr": "Le soleil brille aujourd'hui sur la petite ville. Elle marcha lentement vers la place centrale, regardant les enfants qui jouaient près de la fontaine.",
    "ja": "今日はとてもいい天気ですね。彼女は静かに公園を歩きながら、子供のころの思い出を振り返っていました。遠くの山々が夕日に染まっています。",
    "es": "Hoy hace un día maravilloso. Ella caminaba despacio por el parque, recordando su infancia mientras los niños jugaban alegremente cerca de la fuente.",
    "pt": "Hoje está um dia maravilhoso. Ela caminhava devagar pelo parque, lembrando-se da infância enquanto as crianças brincavam perto da fonte.",
}


def _query_rate(voice_id: str, language: str):
    return query_one(
        "SELECT chars_per_second, sample_count FROM voice_speech_rate "
        "WHERE voice_id=%s AND language=%s",
        (voice_id, language),
    )


def _upsert_rate(voice_id: str, language: str, cps: float, count: int) -> None:
    execute(
        """
        INSERT INTO voice_speech_rate (voice_id, language, chars_per_second,
                                        sample_count, updated_at)
        VALUES (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
          chars_per_second=VALUES(chars_per_second),
          sample_count=VALUES(sample_count),
          updated_at=VALUES(updated_at)
        """,
        (voice_id, language, cps, count, datetime.utcnow()),
    )


def get_rate(voice_id: str, language: str) -> Optional[float]:
    row = _query_rate(voice_id, language)
    if not row:
        return None
    return float(row["chars_per_second"])


def update_rate(voice_id: str, language: str, *,
                chars: int, duration_seconds: float) -> None:
    """根据一条新样本（字符数/时长）增量更新模型。非法输入直接跳过。"""
    if chars <= 0 or duration_seconds <= 0:
        return
    new_cps = chars / duration_seconds
    existing = _query_rate(voice_id, language)
    if existing is None:
        _upsert_rate(voice_id, language, new_cps, 1)
        return
    old_cps = float(existing["chars_per_second"])
    old_count = int(existing["sample_count"])
    merged_cps = (old_cps * old_count + new_cps) / (old_count + 1)
    _upsert_rate(voice_id, language, merged_cps, old_count + 1)


def _generate_tts(text: str, voice_id: str, api_key: str,
                  out_dir: str) -> Tuple[str, float]:
    """生成基准 TTS 并返回 (音频路径, 时长秒)。

    延迟导入 pipeline.tts，避免在模块加载时连锁触发 elevenlabs 客户端。
    测试中通过 patch("pipeline.speech_rate_model._generate_tts") 替换，
    不会执行真实 TTS 调用。
    """
    from pipeline.tts import generate_segment_audio, _get_audio_duration
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"baseline_{voice_id}.mp3")
    generate_segment_audio(
        text=text, voice_id=voice_id,
        output_path=out_path, elevenlabs_api_key=api_key,
    )
    return out_path, _get_audio_duration(out_path)


def initialize_baseline(voice_id: str, language: str, *,
                        api_key: str, work_dir: str) -> float:
    """使用标准基准文本生成 TTS、测量时长、初始化语速模型。

    未知语言时回退到英文基准。返回初始 chars_per_second。
    """
    text = BENCHMARK_TEXT.get(language, BENCHMARK_TEXT["en"])
    _out_path, duration = _generate_tts(
        text=text, voice_id=voice_id,
        api_key=api_key, out_dir=work_dir,
    )
    cps = len(text) / duration if duration > 0 else 0.0
    update_rate(voice_id, language, chars=len(text),
                duration_seconds=duration)
    return cps
