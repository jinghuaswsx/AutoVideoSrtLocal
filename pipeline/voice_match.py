"""
音色匹配：原视频人声采样 + embedding 相似度 top-k 候选
"""
from __future__ import annotations

import os
import subprocess
from typing import Any, Dict, List, Optional

import numpy as np

from appcore.db import query
from pipeline.voice_embedding import (
    embed_audio_file, cosine_similarity, deserialize_embedding,
)
from pipeline.ffutil import get_media_duration

SAMPLE_CLIP_SECONDS = 10.0


def _extract_audio_track(video_path: str, out_dir: str) -> str:
    """用 ffmpeg 从视频中导出 16kHz mono WAV，便于 resemblyzer 处理。"""
    os.makedirs(out_dir, exist_ok=True)
    wav_path = os.path.join(out_dir, "source_audio.wav")
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
        wav_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return wav_path


def _cut_clip(src_wav: str, start: float, end: float, dest_dir: str) -> str:
    """从 src_wav 切出 [start, end] 片段。"""
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, "source_clip.wav")
    cmd = [
        "ffmpeg", "-y", "-i", src_wav,
        "-ss", f"{start:.3f}", "-to", f"{end:.3f}",
        "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
        dest,
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return dest


def _get_duration(path: str) -> float:
    return get_media_duration(path)


def extract_sample_clip(video_path: str, *, out_dir: str) -> str:
    """提取视频中间 10 秒人声片段作为音色采样。

    若视频短于 10 秒，则输出视频整段。
    """
    full_wav = _extract_audio_track(video_path, out_dir)
    dur = _get_duration(full_wav)
    mid = dur / 2.0
    half = SAMPLE_CLIP_SECONDS / 2.0
    start = max(0.0, mid - half)
    end = min(dur, start + SAMPLE_CLIP_SECONDS)
    # 若尾部被截断，将起点回推以保留更多样本
    if end - start < SAMPLE_CLIP_SECONDS and start > 0:
        start = max(0.0, end - SAMPLE_CLIP_SECONDS)
    return _cut_clip(full_wav, start, end, out_dir)


def _query_voices_by_language(language: str, gender: Optional[str] = None,
                               limit: Optional[int] = None) -> List[Dict[str, Any]]:
    sql = (
        "SELECT voice_id, name, gender, language, accent, category, "
        "preview_url, audio_embedding "
        "FROM elevenlabs_voices "
        "WHERE language = %s AND audio_embedding IS NOT NULL"
    )
    params: List[Any] = [language]
    if gender:
        sql += " AND gender = %s"
        params.append(gender)
    if limit:
        sql += f" LIMIT {int(limit)}"
    return query(sql, tuple(params))


def match_candidates(
    query_embedding: np.ndarray,
    *,
    language: str,
    gender: Optional[str] = None,
    top_k: int = 3,
) -> List[Dict[str, Any]]:
    """对候选音色按余弦相似度排序，返回前 top_k 条。"""
    rows = _query_voices_by_language(language=language, gender=gender)
    scored: List[Dict[str, Any]] = []
    for row in rows:
        blob = row.get("audio_embedding")
        if not blob:
            continue
        cand_vec = deserialize_embedding(blob)
        sim = cosine_similarity(query_embedding, cand_vec)
        scored.append({
            "voice_id": row["voice_id"],
            "name": row.get("name"),
            "language": row.get("language"),
            "gender": row.get("gender"),
            "accent": row.get("accent"),
            "preview_url": row.get("preview_url"),
            "similarity": sim,
        })
    scored.sort(key=lambda x: x["similarity"], reverse=True)
    return scored[:top_k]


def match_for_video(
    video_path: str,
    *,
    language: str,
    gender: Optional[str] = None,
    top_k: int = 3,
    out_dir: str,
) -> List[Dict[str, Any]]:
    """完整流程：提取采样 → 计算 embedding → 数据库匹配。"""
    clip_path = extract_sample_clip(video_path, out_dir=out_dir)
    query_vec = embed_audio_file(clip_path)
    return match_candidates(
        query_vec, language=language, gender=gender, top_k=top_k,
    )
