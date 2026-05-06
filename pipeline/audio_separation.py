"""人声分离业务层：上游 GPU 分离 + 基准响度测量。

封装四件事：
1. 调用 :class:`appcore.audio_separation_client.SeparationClient`
2. 用 :func:`appcore.audio_loudness.measure_integrated_lufs` 测 vocals 的 L₀
3. 用 :func:`appcore.audio_loudness.is_likely_silence` 判定分离是否失败（vocals 几乎全静音）
4. 把所有故障情况都折叠成结构化 dict 返回，runtime 层不再 try/except

返回的 dict schema 与 ``task["separation"]`` 持久化字段一致，
runtime 层 :meth:`_step_separate` 直接 ``task_state.update(separation=result)``
即可。同时这个 dict 也是 UI 详情页"人声分离"卡片读取的数据源。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from appcore.audio_loudness import (
    is_likely_silence,
    measure_integrated_lufs,
)
from appcore.audio_separation_client import (
    DEFAULT_BASE_URL,
    DEFAULT_PRESET,
    DEFAULT_TASK_TIMEOUT,
    SeparationApiUnavailable,
    SeparationClient,
    SeparationFailed,
    SeparationTimeout,
)

log = logging.getLogger(__name__)


# system_settings 表里的 key（详见 db/migrations/2026_05_05_audio_separation_settings.sql）
SETTING_ENABLED = "audio_separation_enabled"
SETTING_API_URL = "audio_separation_api_url"
SETTING_PRESET = "audio_separation_preset"
SETTING_TASK_TIMEOUT = "audio_separation_task_timeout"
SETTING_BACKGROUND_VOLUME = "audio_separation_background_volume"


@dataclass(frozen=True)
class SeparationSettings:
    """audio_separation 总配置，从 system_settings 表读取后聚合一次。"""

    enabled: bool
    api_url: str
    preset: str
    task_timeout: float
    background_volume: float

    @property
    def is_runnable(self) -> bool:
        """开关打开且 API URL 配置非空 —— 才可以发起分离调用。"""
        return self.enabled and bool(self.api_url.strip())


def load_settings() -> SeparationSettings:
    """从 system_settings 表读取，缺省值在这里集中维护。

    上线初期 ``enabled=False`` 是默认行为：除非管理员显式打开开关，
    否则 runtime 走旧逻辑（不调分离 API、不做响度匹配、不混背景音）。
    """
    from appcore.settings import get_setting

    enabled_raw = (get_setting(SETTING_ENABLED) or "0").strip().lower()
    enabled = enabled_raw in {"1", "true", "yes", "on"}

    api_url_raw = get_setting(SETTING_API_URL)
    if api_url_raw is None:
        api_url = DEFAULT_BASE_URL
    else:
        api_url = api_url_raw.strip()
    preset = (get_setting(SETTING_PRESET) or DEFAULT_PRESET).strip() or DEFAULT_PRESET

    try:
        task_timeout = float(get_setting(SETTING_TASK_TIMEOUT) or DEFAULT_TASK_TIMEOUT)
    except (TypeError, ValueError):
        task_timeout = DEFAULT_TASK_TIMEOUT

    try:
        # 默认 0.8：让翻译版 mp4 里 vocals/BG 响度比例跟原片质感接近
        # （原片 vocals 跟 BG 差通常 10-12 dB，TTS 单独输出 -11~-15 LUFS、
        # accompaniment 分离结果 -20~-25 LUFS，bg_volume=0.8 ≈ -2 dB 衰减
        # 让 BG 进 mp4 后跟 TTS 差 ~11 dB，符合原片听感）。
        bg = float(get_setting(SETTING_BACKGROUND_VOLUME) or 0.8)
    except (TypeError, ValueError):
        bg = 0.8
    bg = max(0.0, min(2.0, bg))

    return SeparationSettings(
        enabled=enabled,
        api_url=api_url,
        preset=preset,
        task_timeout=task_timeout,
        background_volume=bg,
    )


def run_separation(
    *,
    audio_path: str,
    output_dir: str,
    api_url: str,
    preset: str = "vocal_balanced",
    model_filename: str | None = None,
    task_timeout: float = DEFAULT_TASK_TIMEOUT,
) -> dict[str, Any]:
    """同步跑一次分离 + 基准响度测量，返回结构化结果。

    本函数不抛异常 —— 所有故障情况（API 不可达 / 任务超时 / 分离失败 / vocals
    实测几乎静音）都折叠到返回 dict 的 ``status`` + ``error_kind`` 字段，让
    runtime 层逻辑保持线性。

    返回 dict 字段（与 ``task["separation"]`` 持久化字段一致）：

    - ``status``: ``"done" | "failed" | "timeout" | "unavailable" | "silence"``
    - ``model``: 实际用的 preset / 模型名（用于 UI 显示）
    - ``api_url``: 调用快照
    - ``started_at_epoch``: float
    - ``finished_at_epoch``: float
    - ``elapsed_seconds``: float
    - ``timeout_seconds``: float（caller 配置的总超时，前端用来计算"已超时"）
    - ``vocals_path``: str | None
    - ``accompaniment_path``: str | None
    - ``vocals_lufs``: float | None
    - ``error``: str | None
    - ``error_kind``: ``"timeout" | "unavailable" | "failed" | "silence" | None``
    """
    started = time.time()
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    base: dict[str, Any] = {
        "model": model_filename or preset,
        "api_url": api_url,
        "started_at_epoch": started,
        "finished_at_epoch": None,
        "elapsed_seconds": None,
        "timeout_seconds": float(task_timeout),
        "vocals_path": None,
        "accompaniment_path": None,
        "vocals_lufs": None,
        # B 算法（整体对整体）反推 TTS 目标响度时用：原视频整体（vocals + BGM
        # 一起测）的 integrated LUFS。done 时填好；失败时为 None，loudness_match
        # 自动降级到 A 算法（target=vocals_lufs）。
        "video_lufs": None,
        "error": None,
        "error_kind": None,
    }

    client = SeparationClient(api_url, task_timeout=task_timeout)

    try:
        result = client.separate(
            audio_path=audio_path,
            output_dir=str(out),
            model=(model_filename or preset),
        )
    except SeparationTimeout as exc:
        return _finish(base, status="timeout", error=str(exc),
                       error_kind="timeout")
    except SeparationApiUnavailable as exc:
        return _finish(base, status="unavailable", error=str(exc),
                       error_kind="unavailable")
    except SeparationFailed as exc:
        return _finish(base, status="failed", error=str(exc),
                       error_kind="failed")
    except FileNotFoundError as exc:
        return _finish(base, status="failed", error=str(exc),
                       error_kind="failed")
    except Exception as exc:  # noqa: BLE001 — 兜底，不允许冒泡到 runtime
        log.exception("[audio_separation] unexpected error: %s", exc)
        return _finish(base, status="failed", error=str(exc),
                       error_kind="failed")

    base.update(
        vocals_path=result.vocals_path,
        accompaniment_path=result.accompaniment_path,
        model=result.model,
    )

    try:
        vocals_lufs = measure_integrated_lufs(result.vocals_path)
    except Exception as exc:  # noqa: BLE001 — 测量失败也走降级
        log.warning("[audio_separation] vocals LUFS measure failed: %s", exc)
        return _finish(base, status="failed", error=f"loudness measure failed: {exc}",
                       error_kind="failed")

    base["vocals_lufs"] = vocals_lufs

    # B 算法（整体对整体匹配）需要原视频整体 LUFS：合成阶段反推 TTS 目标响度，
    # 让 mix(TTS_norm, BG×bg_vol) 整体跟原视频整体 LUFS 收敛。
    # 测量失败时降级到 A 算法（仅人声响度），不影响主流程。
    try:
        video_lufs = measure_integrated_lufs(audio_path)
        base["video_lufs"] = video_lufs
    except Exception as exc:  # noqa: BLE001
        log.warning("[audio_separation] video LUFS measure failed: %s", exc)
        base["video_lufs"] = None

    if is_likely_silence(vocals_lufs):
        # vocals 几乎静音 → 分离结果不可用（原视频可能本就纯音乐 / 纯人声）
        return _finish(base, status="silence",
                       error=f"vocals are near-silent (L_v={vocals_lufs:.1f} LUFS), "
                             "treating as separation failure",
                       error_kind="silence")

    return _finish(base, status="done", error=None, error_kind=None)


def _finish(
    base: dict[str, Any],
    *,
    status: str,
    error: str | None,
    error_kind: str | None,
) -> dict[str, Any]:
    finished = time.time()
    base["status"] = status
    base["error"] = error
    base["error_kind"] = error_kind
    base["finished_at_epoch"] = finished
    base["elapsed_seconds"] = finished - base["started_at_epoch"]
    return base


def disabled_result(reason: str = "disabled by settings") -> dict[str, Any]:
    """构造一个 ``status="disabled"`` 占位结果，用于总开关关掉时。

    UI 据此显示"人声分离未启用"，runtime 走旧路径。
    """
    now = time.time()
    return {
        "status": "disabled",
        "model": None,
        "api_url": None,
        "started_at_epoch": now,
        "finished_at_epoch": now,
        "elapsed_seconds": 0.0,
        "timeout_seconds": 0.0,
        "vocals_path": None,
        "accompaniment_path": None,
        "vocals_lufs": None,
        "error": reason,
        "error_kind": None,
    }


def is_usable(separation: dict[str, Any] | None) -> bool:
    """task["separation"] 是否可用（status="done" 且 vocals_lufs / paths 都齐）。

    后续 :func:`pipeline.audio_stitch` 在合成阶段用这个判定要不要做响度匹配
    + background 混音；任何降级状态都返回 False，回退旧逻辑。
    """
    if not separation:
        return False
    if separation.get("status") != "done":
        return False
    if not separation.get("vocals_path"):
        return False
    if not separation.get("accompaniment_path"):
        return False
    if separation.get("vocals_lufs") is None:
        return False
    return True
