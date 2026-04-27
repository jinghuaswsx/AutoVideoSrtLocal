import logging
import os
import subprocess
import threading
from typing import Callable, List, Dict, Optional

log = logging.getLogger(__name__)

try:
    from elevenlabs import VoiceSettings
except ImportError:  # pragma: no cover - older SDK fallback
    VoiceSettings = None
from elevenlabs.client import ElevenLabs
from appcore.llm_provider_configs import (
    ProviderConfigError,
    require_provider_api_key,
)
from pipeline.voice_library import get_voice_library

_client: ElevenLabs | None = None
_client_lock = threading.Lock()


def _resolve_elevenlabs_api_key() -> str:
    try:
        return require_provider_api_key("elevenlabs_tts")
    except ProviderConfigError as exc:
        raise RuntimeError(str(exc)) from exc


def _get_client(api_key: str | None = None) -> ElevenLabs:
    """每次 admin 改了 DB key 后要立即生效，所以当前实现总是新建 client。

    如需复用 client，可改为在 key 变化时失效缓存；但本项目的调用频率很低，
    重新 new 一个 ElevenLabs 客户端的成本可以忽略，换回的是"改完 DB 立即生效"。
    """
    global _client
    if api_key:
        return ElevenLabs(api_key=api_key)
    resolved_key = _resolve_elevenlabs_api_key()
    with _client_lock:
        _client = ElevenLabs(api_key=resolved_key)
        return _client


def load_voices(user_id: int) -> List[Dict]:
    return get_voice_library().list_voices(user_id)


def get_default_voice(user_id: int, gender: str = "male") -> Dict:
    return get_voice_library().get_default_voice(user_id, gender)


def get_voice_by_id(voice_id: int, user_id: int) -> Dict | None:
    return get_voice_library().get_voice(voice_id, user_id)


def generate_segment_audio(
    text: str,
    voice_id: str,
    output_path: str,
    elevenlabs_api_key: str | None = None,
    model_id: str = "eleven_turbo_v2_5",
    language_code: str | None = None,
    speed: float | None = None,
) -> str:
    """生成单段音频，返回文件路径（mp3）"""
    client = _get_client(api_key=elevenlabs_api_key)
    kwargs = dict(
        text=text,
        voice_id=voice_id,
        model_id=model_id,
        output_format="mp3_44100_128",
    )
    if language_code:
        kwargs["language_code"] = language_code
    if speed is not None and abs(speed - 1.0) > 0.001:
        if VoiceSettings is not None:
            try:
                kwargs["voice_settings"] = VoiceSettings(speed=float(speed))
            except Exception:
                kwargs["voice_settings"] = {"speed": float(speed)}
        else:
            kwargs["voice_settings"] = {"speed": float(speed)}
    audio = client.text_to_speech.convert(**kwargs)
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "wb") as f:
        for chunk in audio:
            f.write(chunk)
    return output_path


def generate_full_audio(
    segments: List[Dict],
    voice_id: str,
    output_dir: str,
    *,
    variant: str | None = None,
    elevenlabs_api_key: str | None = None,
    model_id: str = "eleven_turbo_v2_5",
    language_code: str | None = None,
    on_segment_done: Optional[Callable[[int, int, dict], None]] = None,
) -> Dict:
    """
    为所有翻译段落生成音频并拼接成完整音轨

    Args:
        on_segment_done: 每段完成后调用，签名 (done: int, total: int, info: dict)。
                         info 包含 segment_index / tts_duration / tts_text_preview。
                         回调抛出的异常会被吞掉，不影响主流程。

    Returns:
        {"full_audio_path": str, "segments": [...]}  # 每段新增 tts_path, tts_duration
    """
    seg_dir = os.path.join(output_dir, "tts_segments", variant) if variant else os.path.join(output_dir, "tts_segments")
    os.makedirs(seg_dir, exist_ok=True)

    updated_segments = []
    concat_list_path = os.path.join(seg_dir, "concat.txt")
    total = len(segments)

    with open(concat_list_path, "w", encoding="utf-8") as concat_f:
        for i, seg in enumerate(segments):
            text = seg.get("tts_text") or seg.get("translated") or seg.get("text", "")
            seg_path = os.path.join(seg_dir, f"seg_{i:04d}.mp3")

            generate_segment_audio(text, voice_id, seg_path, elevenlabs_api_key=elevenlabs_api_key,
                                   model_id=model_id, language_code=language_code)
            duration = _get_audio_duration(seg_path)

            seg_copy = dict(seg)
            seg_copy["tts_path"] = seg_path
            seg_copy["tts_duration"] = duration
            updated_segments.append(seg_copy)

            concat_f.write(f"file '{os.path.abspath(seg_path)}'\n")

            if on_segment_done is not None:
                try:
                    on_segment_done(i + 1, total, {
                        "segment_index": i,
                        "tts_duration": duration,
                        "tts_text_preview": (text or "")[:60],
                    })
                except Exception:
                    log.exception("on_segment_done callback raised; ignoring")

    full_audio_name = f"tts_full.{variant}.mp3" if variant else "tts_full.mp3"
    full_audio_path = os.path.join(output_dir, full_audio_name)
    result = subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_list_path, "-c", "copy", full_audio_path],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(f"音频拼接失败: {result.stderr}")

    return {"full_audio_path": full_audio_path, "segments": updated_segments}


def _get_audio_duration(audio_path: str) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
        capture_output=True, text=True
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


# Public alias — prefer this over the underscored name in new code.
get_audio_duration = _get_audio_duration
