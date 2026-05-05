import atexit
import logging
import os
import ssl
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, Future
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


# ===== 进程级单例 TTS 线程池：跨任务共享 ElevenLabs 并发上限 =====
#
# ElevenLabs Business 套餐并发硬上限 15。默认 12 留 3 路 buffer 给声音库同步等
# 其他子系统。所有翻译任务都向同一个 pool submit segment，自然 FIFO 排队，
# 物理上不可能超过 max_workers，避免集体 429。

_TTS_POOL: ThreadPoolExecutor | None = None
_TTS_POOL_LOCK = threading.Lock()
_DEFAULT_TTS_MAX_CONCURRENCY = 12
_HARD_CAP_TTS_MAX_CONCURRENCY = 15  # ElevenLabs Business tier hard limit


def _resolve_tts_max_concurrency() -> int:
    """从 system settings 读 tts_max_concurrency，默认 12，硬上限 15。"""
    from appcore.settings import get_setting
    raw = get_setting("tts_max_concurrency")
    try:
        n = int(raw) if raw is not None else _DEFAULT_TTS_MAX_CONCURRENCY
    except (TypeError, ValueError):
        n = _DEFAULT_TTS_MAX_CONCURRENCY
    return max(1, min(n, _HARD_CAP_TTS_MAX_CONCURRENCY))


def _get_tts_pool() -> ThreadPoolExecutor:
    global _TTS_POOL
    if _TTS_POOL is None:
        with _TTS_POOL_LOCK:
            if _TTS_POOL is None:
                max_workers = _resolve_tts_max_concurrency()
                _TTS_POOL = ThreadPoolExecutor(
                    max_workers=max_workers,
                    thread_name_prefix="tts-elevenlabs",
                )
                atexit.register(_TTS_POOL.shutdown, wait=True)
    return _TTS_POOL


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

_THROTTLE_RETRY_DELAYS: tuple[float, ...] = (0.5, 1.0, 2.0, 4.0)


def _is_concurrent_limit_429(exc: BaseException) -> bool:
    """识别 ElevenLabs 的 HTTP 429（concurrent_limit_exceeded / rate_limit_exceeded）。"""
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if status != 429:
        return False
    body = getattr(exc, "body", None) or getattr(exc, "response", None)
    text = str(body or exc).lower()
    return (
        "concurrent_limit_exceeded" in text
        or "rate_limit_exceeded" in text
        or status == 429  # 拿不到 body 时按 429 直接当节流
    )


def _call_with_throttle_retry(fn, *, label: str = "elevenlabs"):
    """ElevenLabs 429（多任务抢并发）专用退避：0.5/1/2/4s 总计 4 次。
    非 429 错误透传，由 _call_with_network_retry 再处理网络层瞬时故障。"""
    for attempt in range(len(_THROTTLE_RETRY_DELAYS)):
        try:
            return fn()
        except BaseException as exc:
            if not _is_concurrent_limit_429(exc):
                raise
            if attempt >= len(_THROTTLE_RETRY_DELAYS) - 1:
                log.exception("%s throttle retry exhausted: %s", label, exc)
                raise
            delay = _THROTTLE_RETRY_DELAYS[attempt]
            log.warning("%s 429 throttle, retry in %.1fs: %s", label, delay, exc)
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

    # 外层：429（多任务抢并发）退避；内层：网络瞬时抖动退避。
    # 顺序很重要——429 是 HTTP 层错误，网络 retry 不识别它。
    audio_bytes = _call_with_throttle_retry(
        lambda: _call_with_network_retry(
            _do_tts_call,
            label="elevenlabs.text_to_speech",
        ),
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
    on_progress: Optional[Callable[[dict], None]] = None,
    on_segment_done: Optional[Callable[[int, int, dict], None]] = None,
) -> Dict:
    """为所有翻译段落生成音频并拼接成完整音轨（并发提交到全局 TTS 线程池）。

    Args:
        on_progress: 新接口。每次状态变化触发 (state ∈ submitted/started/completed)，
            snapshot = {state, total, done, active, queued, info}。回调抛出的异常会
            被吞掉。
        on_segment_done: 兼容旧接口。每段完成后调用 (done, total, info)。回调抛出的
            异常会被吞掉。两者可同时传，会都被调用。

    Returns:
        {"full_audio_path": str, "segments": [...]}  # 每段新增 tts_path, tts_duration
    """
    seg_dir = (
        os.path.join(output_dir, "tts_segments", variant)
        if variant else os.path.join(output_dir, "tts_segments")
    )
    os.makedirs(seg_dir, exist_ok=True)

    total = len(segments)
    pool = _get_tts_pool()

    state = {"total": total, "active": 0, "queued": total, "done": 0}
    state_lock = threading.Lock()

    def _emit_progress(reason: str, info: dict | None = None) -> None:
        if on_progress is None:
            return
        with state_lock:
            snapshot = {
                "state": reason,
                "total": state["total"],
                "active": state["active"],
                "queued": state["queued"],
                "done": state["done"],
                "info": info or {},
            }
        try:
            on_progress(snapshot)
        except Exception:
            log.exception("on_progress callback raised; ignoring")

    def _segment_wrapper(text: str, seg_path: str) -> tuple[str, float]:
        with state_lock:
            state["active"] += 1
            state["queued"] -= 1
        _emit_progress("started", {"text_preview": (text or "")[:60]})
        try:
            generate_segment_audio(
                text, voice_id, seg_path,
                elevenlabs_api_key=elevenlabs_api_key,
                model_id=model_id, language_code=language_code,
            )
            duration = _get_audio_duration(seg_path)
            return seg_path, duration
        finally:
            with state_lock:
                state["active"] -= 1

    # 1. submit 之前先 emit "submitted"——此时 active=0/queued=total/done=0，前端
    #    立刻显示"排队中"。**必须在 submit 之前**：否则 worker 抢先 emit "started"，
    #    submitted 事件会被淹没（race condition）。
    _emit_progress("submitted")

    # 2. 提交全部 segment 到全局 pool（受 max_workers 限流）
    tasks: list[tuple[int, dict, str, str, Future]] = []
    for i, seg in enumerate(segments):
        text = seg.get("tts_text") or seg.get("translated") or seg.get("text", "")
        seg_path = os.path.join(seg_dir, f"seg_{i:04d}.mp3")
        future = pool.submit(_segment_wrapper, text, seg_path)
        tasks.append((i, seg, text, seg_path, future))

    # 3. as_completed 收回（按完成时间，不按 i 顺序）
    seg_results: dict[int, dict] = {}
    failures: list[tuple[int, BaseException]] = []
    future_to_meta = {t[4]: t for t in tasks}
    for fut in as_completed([t[4] for t in tasks]):
        i, seg, text, seg_path, _ = future_to_meta[fut]
        try:
            _, duration = fut.result()
        except BaseException as exc:
            failures.append((i, exc))
            continue
        seg_copy = dict(seg)
        seg_copy["tts_path"] = seg_path
        seg_copy["tts_duration"] = duration
        seg_results[i] = seg_copy

        with state_lock:
            state["done"] += 1
            done_now = state["done"]
        info = {
            "segment_index": i,
            "tts_duration": duration,
            "tts_text_preview": (text or "")[:60],
        }
        _emit_progress("completed", info)
        if on_segment_done is not None:
            try:
                on_segment_done(done_now, total, info)
            except Exception:
                log.exception("on_segment_done callback raised; ignoring")

    if failures:
        for _, _, _, _, f in tasks:
            f.cancel()
        first_idx, first_exc = failures[0]
        raise RuntimeError(
            f"TTS segment generation failed at index {first_idx} "
            f"({len(failures)}/{total} failed): {first_exc}"
        ) from first_exc

    # 4. 按 i 顺序拼 concat 列表（保持音轨时序）
    updated_segments = [seg_results[i] for i in range(total)]
    concat_list_path = os.path.join(seg_dir, "concat.txt")
    with open(concat_list_path, "w", encoding="utf-8") as concat_f:
        for seg_copy in updated_segments:
            concat_f.write(f"file '{os.path.abspath(seg_copy['tts_path'])}'\n")

    # 5. ffmpeg concat（不变）
    full_audio_name = f"tts_full.{variant}.mp3" if variant else "tts_full.mp3"
    full_audio_path = os.path.join(output_dir, full_audio_name)
    result = subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_list_path,
         "-c", "copy", full_audio_path],
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
    """以指定 speed 重新合成 segments 并 concat（**并发**调用 ElevenLabs）。

    用于 TTS Duration Loop 的"变速短路"分支：当某轮原始音频落入 ±10% 但不在
    final range，通过 voice_settings.speed 一击直接收敛到 [v-1, v+2]。

    跟 :func:`generate_full_audio` 共用 :data:`_TTS_POOL`，并发模式一致：
    `pool.submit` + `as_completed` 收回，按 i 顺序拼 concat 列表。

    Args:
        segments: 与 generate_full_audio 相同的输入（含 tts_text）
        variant: 用于命名 segment 子目录和 concat 产物，例如 "round_2"
        speed: ElevenLabs voice_settings.speed，合法范围 [0.7, 1.2]，调用方须先 clamp
        on_segment_done: 每段完成后回调 (done, total, info)

    Returns:
        {"full_audio_path": str, "segments": [...]}  # 每段含 tts_path / tts_duration

    Raises:
        段失败时 cancel 全部 + raise RuntimeError。caller 自行决定怎么处理
        （主流程当前的策略：变速失败时直接采用变速前的 result["full_audio_path"]
        原样输出，不再 atempo 兜底）。
    """
    if not (0.7 <= speed <= 1.2):
        raise ValueError(f"speed must be in [0.7, 1.2], got {speed}")
    seg_dir = os.path.join(output_dir, "tts_segments", f"{variant}_speedup")
    os.makedirs(seg_dir, exist_ok=True)

    total = len(segments)
    pool = _get_tts_pool()
    state = {"total": total, "active": 0, "queued": total, "done": 0}
    state_lock = threading.Lock()

    def _segment_wrapper(text: str, seg_path: str) -> tuple[str, float]:
        with state_lock:
            state["active"] += 1
            state["queued"] -= 1
        try:
            generate_segment_audio(
                text, voice_id, seg_path,
                elevenlabs_api_key=elevenlabs_api_key,
                model_id=model_id, language_code=language_code,
                speed=speed,
            )
            duration = _get_audio_duration(seg_path)
            return seg_path, duration
        finally:
            with state_lock:
                state["active"] -= 1

    # submit 全部
    tasks: list[tuple[int, dict, str, str, Future]] = []
    for i, seg in enumerate(segments):
        text = seg.get("tts_text") or seg.get("translated") or seg.get("text", "")
        seg_path = os.path.join(seg_dir, f"seg_{i:04d}.mp3")
        future = pool.submit(_segment_wrapper, text, seg_path)
        tasks.append((i, seg, text, seg_path, future))

    # as_completed 收回（按完成时间）
    seg_results: dict[int, dict] = {}
    failures: list[tuple[int, BaseException]] = []
    future_to_meta = {t[4]: t for t in tasks}
    for fut in as_completed([t[4] for t in tasks]):
        i, seg, text, seg_path, _ = future_to_meta[fut]
        try:
            _, duration = fut.result()
        except BaseException as exc:
            failures.append((i, exc))
            continue
        seg_copy = dict(seg)
        seg_copy["tts_path"] = seg_path
        seg_copy["tts_duration"] = duration
        seg_results[i] = seg_copy

        with state_lock:
            state["done"] += 1
            done_now = state["done"]
        info = {
            "segment_index": i,
            "tts_duration": duration,
            "tts_text_preview": (text or "")[:60],
            "speed": speed,
        }
        if on_segment_done is not None:
            try:
                on_segment_done(done_now, total, info)
            except Exception:
                log.exception("on_segment_done callback raised; ignoring")

    if failures:
        for _, _, _, _, f in tasks:
            f.cancel()
        first_idx, first_exc = failures[0]
        raise RuntimeError(
            f"TTS speedup segment generation failed at index {first_idx} "
            f"({len(failures)}/{total} failed): {first_exc}"
        ) from first_exc

    # 按 i 顺序拼 concat 列表
    updated_segments = [seg_results[i] for i in range(total)]
    concat_list_path = os.path.join(seg_dir, "concat.txt")
    with open(concat_list_path, "w", encoding="utf-8") as concat_f:
        for seg_copy in updated_segments:
            concat_f.write(f"file '{os.path.abspath(seg_copy['tts_path'])}'\n")

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
