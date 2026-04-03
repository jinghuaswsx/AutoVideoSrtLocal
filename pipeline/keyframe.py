"""pipeline/keyframe.py
视频关键帧抽取：scenedetect 检测场景切换点，ffmpeg 抽取对应帧图片。
"""

from __future__ import annotations

import logging
import os
import subprocess

log = logging.getLogger(__name__)


def detect_scene_timestamps(video_path: str, threshold: float = 27.0) -> list[float]:
    """用 scenedetect 检测场景切换时间点（秒）。

    如果 scenedetect 不可用或视频无明显场景切换，
    回退到按固定间隔采样。
    """
    try:
        from scenedetect import open_video, SceneManager
        from scenedetect.detectors import ContentDetector
    except ImportError:
        log.warning("scenedetect 未安装，回退到固定间隔采样")
        return _fallback_uniform_timestamps(video_path)

    video = open_video(video_path)
    sm = SceneManager()
    sm.add_detector(ContentDetector(threshold=threshold))
    sm.detect_scenes(video)
    scene_list = sm.get_scene_list()

    if len(scene_list) < 2:
        log.info("场景切换点不足，回退到固定间隔采样")
        return _fallback_uniform_timestamps(video_path)

    timestamps: list[float] = []
    for scene in scene_list:
        start = scene[0].get_seconds()
        timestamps.append(round(start, 3))

    return timestamps


def _fallback_uniform_timestamps(video_path: str, count: int = 6) -> list[float]:
    """均匀采样 count 个时间点。"""
    duration = _get_video_duration(video_path)
    if duration <= 0:
        return [0.0]
    step = duration / (count + 1)
    return [round(step * (i + 1), 3) for i in range(count)]


def _get_video_duration(video_path: str) -> float:
    """通过 ffprobe 获取视频时长（秒）。"""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        video_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return float(result.stdout.strip())
    except Exception:
        log.exception("ffprobe 获取时长失败")
        return 0.0


def extract_keyframes(
    video_path: str,
    output_dir: str,
    timestamps: list[float] | None = None,
    max_frames: int = 8,
    threshold: float = 27.0,
) -> list[str]:
    """抽取关键帧图片。

    Args:
        video_path: 源视频路径
        output_dir: 帧图片输出目录
        timestamps: 指定时间点（秒），为 None 则自动检测
        max_frames: 最大帧数
        threshold: scenedetect 阈值

    Returns:
        帧图片路径列表（按时间排序）
    """
    os.makedirs(output_dir, exist_ok=True)

    if timestamps is None:
        timestamps = detect_scene_timestamps(video_path, threshold=threshold)

    # 限制最大帧数：均匀采样
    if len(timestamps) > max_frames:
        step = len(timestamps) / max_frames
        timestamps = [timestamps[int(i * step)] for i in range(max_frames)]

    frame_paths: list[str] = []
    for i, ts in enumerate(timestamps):
        out_path = os.path.join(output_dir, f"frame_{i:03d}.jpg")
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(ts),
            "-i", video_path,
            "-vframes", "1",
            "-q:v", "2",
            out_path,
        ]
        try:
            subprocess.run(cmd, capture_output=True, check=True)
            if os.path.exists(out_path):
                frame_paths.append(out_path)
        except subprocess.CalledProcessError:
            log.warning("抽帧失败: ts=%.3f", ts)

    log.info("抽取了 %d 帧关键帧", len(frame_paths))
    return frame_paths
