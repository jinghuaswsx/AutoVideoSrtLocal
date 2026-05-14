"""Gemini 分镜拆解 + ASR 对齐。

调用 Gemini 视觉模型识别视频镜头切换，输出首尾相接的分镜列表；
再按时间重叠度把 ASR 片段归并到对应分镜上。
"""
from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import os
import subprocess
import tempfile
from typing import Any, Dict, List

from pipeline.ffutil import probe_media_info

# 用 alias 便于测试 mock（patch 本模块的 gemini_generate，不触发真实调用）
from appcore.llm_client import invoke_generate as gemini_generate

log = logging.getLogger(__name__)

SHOT_DECOMPOSE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "shots": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "start": {"type": "number"},
                    "end": {"type": "number"},
                    "description": {"type": "string"},
                },
                "required": ["index", "start", "end", "description"],
            },
        },
    },
    "required": ["shots"],
}

SHOT_DECOMPOSE_PROMPT = """你是专业的视频分镜师。请分析这段视频，识别所有镜头切换点，输出分镜列表。

要求：
1. 每个分镜有明确的起止时间（秒，保留 2 位小数）
2. 每个分镜附带一句画面内容描述（20-40字中文）
3. 分镜的 end 必须等于下一个分镜的 start，即首尾相连
4. 第一个分镜从 0.0 开始
5. 最后一个分镜的 end 等于视频总时长 {duration:.2f} 秒

输出 JSON 格式：
{{
  "shots": [
    {{"index": 1, "start": 0.0, "end": 5.2, "description": "..."}}
  ]
}}
"""

DEFAULT_MODEL = "google/gemini-3-flash-preview"
SHOT_DECOMPOSE_PREPROCESS_FILTER = "scale=-2:min(480\\,ih),fps=15"


@dataclass(frozen=True)
class ShotDecomposeMedia:
    original_path: str
    llm_path: str
    preprocessed: bool
    cleanup_path: str | None = None
    original_bytes: int | None = None
    llm_bytes: int | None = None
    error: str | None = None


def _file_size(path: str) -> int | None:
    try:
        return os.path.getsize(path)
    except OSError:
        return None


def prepare_shot_decompose_media(
    video_path: str,
    *,
    output_dir: str | None = None,
) -> ShotDecomposeMedia:
    """Create a 480p/15fps/no-audio LLM input for shot decomposition.

    Docs-anchor:
    docs/superpowers/specs/2026-05-14-omni-shot-decompose-480p-preprocess-design.md
    """
    original = str(Path(video_path))
    original_bytes = _file_size(original)
    if not Path(original).is_file():
        return ShotDecomposeMedia(
            original_path=original,
            llm_path=original,
            preprocessed=False,
            original_bytes=original_bytes,
            llm_bytes=original_bytes,
            error="source_missing",
        )

    info = probe_media_info(original)
    height = int(info.get("height") or 0)
    if height and height <= 480:
        return ShotDecomposeMedia(
            original_path=original,
            llm_path=original,
            preprocessed=False,
            original_bytes=original_bytes,
            llm_bytes=original_bytes,
        )

    fd = None
    out_path = ""
    try:
        tmp_dir = str(Path(output_dir)) if output_dir else None
        fd, out_path = tempfile.mkstemp(
            prefix="shot_decompose_480p_",
            suffix=".mp4",
            dir=tmp_dir,
        )
        os.close(fd)
        fd = None
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            original,
            "-vf",
            SHOT_DECOMPOSE_PREPROCESS_FILTER,
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-b:v",
            "600k",
            "-maxrate",
            "800k",
            "-bufsize",
            "1200k",
            "-an",
            "-movflags",
            "+faststart",
            out_path,
        ]
        subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
            check=True,
        )
        llm_bytes = _file_size(out_path)
        if not llm_bytes:
            raise RuntimeError("ffmpeg produced empty shot_decompose video")
        return ShotDecomposeMedia(
            original_path=original,
            llm_path=out_path,
            preprocessed=True,
            cleanup_path=out_path,
            original_bytes=original_bytes,
            llm_bytes=llm_bytes,
        )
    except Exception as exc:  # noqa: BLE001 - preprocessing must not block the task.
        if fd is not None:
            os.close(fd)
        if out_path:
            try:
                os.unlink(out_path)
            except OSError:
                pass
        log.warning(
            "shot_decompose 480p preprocess failed for %s, using original video: %s",
            original,
            exc,
        )
        return ShotDecomposeMedia(
            original_path=original,
            llm_path=original,
            preprocessed=False,
            original_bytes=original_bytes,
            llm_bytes=original_bytes,
            error=str(exc)[:300],
        )


def cleanup_shot_decompose_media(media: ShotDecomposeMedia) -> None:
    cleanup_path = media.cleanup_path
    if not cleanup_path:
        return
    try:
        if cleanup_path != media.original_path:
            os.unlink(cleanup_path)
    except OSError:
        pass


def decompose_shots(
    video_path: str,
    *,
    user_id: int,
    duration_seconds: float,
    model: str | None = None,
    preprocess_video: bool = True,
    preprocess_output_dir: str | None = None,
) -> List[Dict[str, Any]]:
    """调用 Gemini 拆分分镜，返回归一化（首尾对齐、相邻衔接、附 duration）的 shots。"""
    prompt = SHOT_DECOMPOSE_PROMPT.format(duration=duration_seconds)
    media_input = (
        prepare_shot_decompose_media(video_path, output_dir=preprocess_output_dir)
        if preprocess_video
        else ShotDecomposeMedia(
            original_path=str(Path(video_path)),
            llm_path=str(Path(video_path)),
            preprocessed=False,
            original_bytes=_file_size(str(Path(video_path))),
            llm_bytes=_file_size(str(Path(video_path))),
        )
    )
    # appcore.gemini.generate 的 media 参数接受路径字符串/Path 或其列表。
    # 测试通过 patch pipeline.shot_decompose.gemini_generate 拦截整条调用，
    # 不会真实走到 Gemini。
    try:
        invoked = gemini_generate(
            "shot_decompose.run",
            prompt=prompt,
            media=[media_input.llm_path],
            user_id=user_id,
            model_override=model,
            response_schema=SHOT_DECOMPOSE_SCHEMA,
        )
        response = invoked.get("json") or {}
        shots = response.get("shots") or []
        _normalize_shots(shots, duration_seconds)
        return shots
    finally:
        if preprocess_video:
            cleanup_shot_decompose_media(media_input)


def _normalize_shots(
    shots: List[Dict[str, Any]], duration_seconds: float,
) -> None:
    """强制分镜首尾衔接 + 每个分镜加 duration 字段。就地修改。"""
    if not shots:
        raise ValueError("Gemini 未返回任何分镜")
    duration = float(duration_seconds or 0.0)
    if duration <= 0:
        duration = max(float(shot.get("end") or 0.0) for shot in shots)
    shots[0]["start"] = 0.0
    shots[-1]["end"] = max(float(shots[-1].get("end") or 0.0), duration)
    for i in range(len(shots) - 1):
        shots[i + 1]["start"] = shots[i]["end"]
    for shot in shots:
        if float(shot["end"]) < float(shot["start"]):
            shot["end"] = shot["start"]
        shot["duration"] = round(
            float(shot["end"]) - float(shot["start"]), 3,
        )


def align_asr_to_shots(
    shots: List[Dict[str, Any]],
    asr_segments: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """把 ASR 片段按时间重叠度归并到分镜中。

    每个分镜附带 source_text（拼接）、asr_segments（原始条目）、silent 标志。
    """
    enriched = [
        dict(
            s,
            source_text="",
            asr_segments=[],
            overlap_source_text="",
            overlapping_asr_segments=[],
        )
        for s in shots
    ]
    for seg in asr_segments:
        s_start = float(seg.get("start") or 0.0)
        s_end = float(seg.get("end") or 0.0)
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        best_idx = None
        best_overlap = 0.0
        overlaps: list[tuple[int, float]] = []
        for i, shot in enumerate(enriched):
            ov = max(
                0.0,
                min(s_end, shot["end"]) - max(s_start, shot["start"]),
            )
            if ov > 0:
                overlaps.append((i, ov))
            if ov > best_overlap:
                best_overlap = ov
                best_idx = i
        for i, ov in overlaps:
            overlap_seg = dict(seg)
            overlap_seg["overlap_duration"] = round(ov, 3)
            enriched[i]["overlapping_asr_segments"].append(overlap_seg)
        if best_idx is None or best_overlap <= 0:
            continue
        if enriched[best_idx]["source_text"]:
            enriched[best_idx]["source_text"] += " " + text
        else:
            enriched[best_idx]["source_text"] = text
        enriched[best_idx]["asr_segments"].append(seg)
    for shot in enriched:
        overlap_text = " ".join(
            (seg.get("text") or "").strip()
            for seg in shot["overlapping_asr_segments"]
            if (seg.get("text") or "").strip()
        ).strip()
        shot["overlap_source_text"] = overlap_text
        shot["silent"] = not (shot["source_text"] or overlap_text)
    return enriched
