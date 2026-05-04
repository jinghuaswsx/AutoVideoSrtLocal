import logging
import os
import ssl
import subprocess
import threading
import time
from typing import Callable, List, Dict, Optional

import httpx

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


_NETWORK_RETRY_EXCEPTIONS: tuple[type[BaseException], ...] = (
    httpx.RemoteProtocolError,
    httpx.ConnectError,
    httpx.ReadTimeout,
    httpx.WriteError,
    httpx.PoolTimeout,
    ssl.SSLError,
)


def _call_with_network_retry(
    fn,
    *,
    attempts: int = 3,
    base_delay: float = 2.0,
    label: str = "elevenlabs",
):
    """Wrap a single ElevenLabs SDK call so transient SSL / connection errors
    get exponential-backoff retries (long videos do 100+ TTS requests; if any
    one hits an SSL EOF on TCP handshake the whole pipeline blows up otherwise).
    Same shape as the OpenRouter adapter helper."""
    total = max(1, attempts)
    for attempt in range(total):
        try:
            return fn()
        except _NETWORK_RETRY_EXCEPTIONS as exc:
            if attempt >= total - 1:
                log.exception(
                    "%s network retry exhausted (%d/%d): %s",
                    label, attempt + 1, total, exc,
                )
                raise
            delay = base_delay * (2 ** attempt)
            log.warning(
                "%s network error (%d/%d), retrying in %.1fs: %s",
                label, attempt + 1, total, delay, exc,
            )
            time.sleep(delay)

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


def _audio_file_already_valid(output_path: str, *, min_bytes: int = 1024) -> bool:
    """已经在 output_path 落盘的 mp3 是否可直接复用——文件存在 + 体积合理 +
    ffprobe 能读到 > 0 的时长。任务重跑时跳过已经成功生成的 ElevenLabs 调用，
    既省额度又把 130 段 audio 的重跑时间从分钟级降到秒级。"""
    try:
        if not os.path.isfile(output_path):
            return False
        if os.path.getsize(output_path) < min_bytes:
            return False
        if _get_audio_duration(output_path) <= 0:
            return False
        return True
    except Exception:
        return False


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
    if _audio_file_already_valid(output_path):
        log.info("tts segment cache hit, skipping ElevenLabs call: %s", output_path)
        return output_path
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
    # ElevenLabs SDK convert() 返回的是一个 generator/iterator —— 真正的 HTTP
    # 请求在迭代它时才发出。如果只把 convert() 调用放进 retry 包装，generator
    # 拿出来后在 retry 之外迭代时 SSL/连接异常就抓不到了。所以把整段 drain 都
    # 放进 lambda 里，让 retry 能看到所有网络层异常。
    def _do_tts_call() -> bytes:
        chunks = client.text_to_speech.convert(**kwargs)
        return b"".join(chunks)

    audio_bytes = _call_with_network_retry(
        _do_tts_call,
        label="elevenlabs.text_to_speech",
    )
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "wb") as f:
        f.write(audio_bytes)
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


def regenerate_full_audio_with_speed(
    segments: List[Dict],
    voice_id: str,
    output_dir: str,
    *,
    variant: str,
    speed: float,
    elevenlabs_api_key: str | None = None,
    model_id: str = "eleven_turbo_v2_5",
    language_code: str | None = None,
    on_segment_done: Optional[Callable[[int, int, dict], None]] = None,
) -> Dict:
    """以指定 speed 重新合成 segments 并 concat。

    用于 TTS Duration Loop 的"变速短路"分支：当某轮原始音频落入 ±10% 但不在
    final range，通过 voice_settings.speed 一击直接收敛到 [v-1, v+2]。

    Args:
        segments: 与 generate_full_audio 相同的输入（含 tts_text）
        variant: 用于命名 segment 子目录和 concat 产物，例如 "round_2"
        speed: ElevenLabs voice_settings.speed，合法范围 [0.7, 1.2]，调用方须先 clamp
        on_segment_done: 同 generate_full_audio

    Returns:
        {"full_audio_path": str, "segments": [...]}  # 每段含 tts_path / tts_duration

    Raises:
        透出 ElevenLabs SDK 的网络异常（已通过 _call_with_network_retry 重试），
        让 _run_tts_duration_loop 走原始音频 atempo fallback。
    """
    if not (0.7 <= speed <= 1.2):
        raise ValueError(f"speed must be in [0.7, 1.2], got {speed}")
    seg_dir = os.path.join(output_dir, "tts_segments", f"{variant}_speedup")
    os.makedirs(seg_dir, exist_ok=True)

    updated_segments = []
    concat_list_path = os.path.join(seg_dir, "concat.txt")
    total = len(segments)

    with open(concat_list_path, "w", encoding="utf-8") as concat_f:
        for i, seg in enumerate(segments):
            text = seg.get("tts_text") or seg.get("translated") or seg.get("text", "")
            seg_path = os.path.join(seg_dir, f"seg_{i:04d}.mp3")

            generate_segment_audio(
                text, voice_id, seg_path,
                elevenlabs_api_key=elevenlabs_api_key,
                model_id=model_id, language_code=language_code,
                speed=speed,
            )
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
                        "speed": speed,
                    })
                except Exception:
                    log.exception("on_segment_done callback raised; ignoring")

    full_audio_path = os.path.join(output_dir, f"tts_full.{variant}.speedup.mp3")
    result = subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_list_path,
         "-c", "copy", full_audio_path],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"音频拼接失败 (speedup): {result.stderr}")

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
