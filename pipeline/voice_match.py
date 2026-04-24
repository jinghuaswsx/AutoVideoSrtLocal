"""
音色匹配：原视频人声采样 + embedding 相似度 top-k 候选
"""
from __future__ import annotations

import os
import subprocess
from typing import Any, Dict, Iterable, List, Optional

import numpy as np

from appcore.db import query
from pipeline.voice_embedding import (
    embed_audio_file, cosine_similarity, deserialize_embedding,
)
from pipeline.ffutil import get_media_duration

SAMPLE_CLIP_SECONDS = 10.0
DEFAULT_VOICE_MATCH_TOP_K = 10
_BASE_TABLE = "elevenlabs_voices"
_VARIANTS_TABLE = "elevenlabs_voice_variants"


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


def _table_for_language(language: str) -> str:
    try:
        rows = query(
            f"SELECT COUNT(*) AS c FROM {_VARIANTS_TABLE} "
            "WHERE language = %s AND audio_embedding IS NOT NULL",
            (language,),
        )
        row = rows[0] if rows else {}
        if int(row.get("c") or 0) > 0:
            return _VARIANTS_TABLE
    except Exception:
        pass
    return _BASE_TABLE


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
    table = _table_for_language(language)
    sql = (
        "SELECT voice_id, name, gender, language, accent, category, "
        "preview_url, audio_embedding "
        f"FROM {table} "
        "WHERE language = %s AND audio_embedding IS NOT NULL"
    )
    params: List[Any] = [language]
    if gender:
        sql += " AND gender = %s"
        params.append(gender)
    if limit:
        sql += " LIMIT %s"
        params.append(int(limit))
    return query(sql, tuple(params))


def match_candidates(
    query_embedding: np.ndarray,
    *,
    language: str,
    gender: Optional[str] = None,
    top_k: int = DEFAULT_VOICE_MATCH_TOP_K,
    exclude_voice_ids: Optional[Iterable[str]] = None,
) -> List[Dict[str, Any]]:
    """对候选音色按余弦相似度排序，返回前 top_k 条。"""
    rows = _query_voices_by_language(language=language, gender=gender)
    excluded = {
        str(voice_id).strip()
        for voice_id in (exclude_voice_ids or [])
        if str(voice_id).strip()
    }
    scored: List[Dict[str, Any]] = []
    for row in rows:
        voice_id = str(row.get("voice_id") or "").strip()
        if not voice_id or voice_id in excluded:
            continue
        blob = row.get("audio_embedding")
        if not blob:
            continue
        cand_vec = deserialize_embedding(blob)
        sim = cosine_similarity(query_embedding, cand_vec)
        scored.append({
            "voice_id": voice_id,
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
    top_k: int = DEFAULT_VOICE_MATCH_TOP_K,
    exclude_voice_ids: Optional[Iterable[str]] = None,
    out_dir: str,
) -> List[Dict[str, Any]]:
    """完整流程：提取采样 → 计算 embedding → 数据库匹配。"""
    clip_path = extract_sample_clip(video_path, out_dir=out_dir)
    query_vec = embed_audio_file(clip_path)
    return match_candidates(
        query_vec,
        language=language,
        gender=gender,
        top_k=top_k,
        exclude_voice_ids=exclude_voice_ids,
    )


def pick_utterance_window(utterances: list[dict], *,
                           min_duration: float = 8.0) -> tuple[float, float]:
    """从 ASR utterances 里挑一段作为音色采样窗口。

    策略：
      1. 若单个 utterance 时长 ≥ min_duration，直接用它
      2. 否则按时间顺序拼接相邻 utterances，找到第一个累计时长 ≥ min_duration 的窗口
      3. 若总时长仍不足，返回 [首个 utterance 起点, 末尾 utterance 终点]（整段）
    """
    if not utterances:
        raise ValueError("utterances is empty")

    # 策略 1：找最长单 utterance
    longest = max(utterances, key=lambda u: u["end_time"] - u["start_time"])
    if longest["end_time"] - longest["start_time"] >= min_duration:
        return float(longest["start_time"]), float(longest["end_time"])

    # 策略 2：滑动窗口拼接
    sorted_utts = sorted(utterances, key=lambda u: u["start_time"])
    for i in range(len(sorted_utts)):
        window_start = sorted_utts[i]["start_time"]
        for j in range(i, len(sorted_utts)):
            window_end = sorted_utts[j]["end_time"]
            if window_end - window_start >= min_duration:
                return float(window_start), float(window_end)

    # 策略 3：兜底，整段
    return float(sorted_utts[0]["start_time"]), float(sorted_utts[-1]["end_time"])


def extract_sample_from_utterances(video_path: str, utterances: list[dict],
                                     *, out_dir: str,
                                     min_duration: float = 8.0) -> str:
    """从视频按 utterances 窗口切出纯人声采样片段。"""
    start, end = pick_utterance_window(utterances, min_duration=min_duration)
    full_wav = _extract_audio_track(video_path, out_dir)
    return _cut_clip(full_wav, start, end,
                     os.path.join(out_dir, "utt_sample"))
