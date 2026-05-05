"""Chunk-planning logic.

VACE pipelines are most stable on short clips. We split a long video into
ROI chunks of ~``chunk_seconds`` seconds each, then constrain the per-chunk
frame count to ``frame_num`` (a 4n+1 bound from VACE).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChunkPlan:
    """One chunk's time-window."""

    index: int
    start_seconds: float
    duration_seconds: float
    end_seconds: float


def plan_chunks(
    *,
    duration_seconds: float,
    fps: float,
    chunk_seconds: float,
    frame_num: int,
) -> list[ChunkPlan]:
    """Split [0, duration] into chunks no longer than min(chunk_seconds, frame_num/fps).

    The final chunk is shorter when the video doesn't divide evenly. Chunks
    have no overlap (overlap support is reserved for a future revision).

    Args:
        duration_seconds: total media duration from ffprobe.
        fps: source frame rate (must be > 0).
        chunk_seconds: nominal chunk length from the active profile.
        frame_num: VACE per-clip frame budget; caps duration to frame_num/fps.

    Returns:
        At least one chunk. Empty videos (duration <= 0) yield an empty list.
    """
    if fps <= 0:
        raise ValueError(f"fps must be > 0, got {fps}")
    if chunk_seconds <= 0:
        raise ValueError(f"chunk_seconds must be > 0, got {chunk_seconds}")
    if frame_num <= 0:
        raise ValueError(f"frame_num must be > 0, got {frame_num}")
    if duration_seconds <= 0:
        return []

    # Cap nominal chunk length by VACE frame budget.
    frame_budget_seconds = frame_num / fps
    effective = min(float(chunk_seconds), float(frame_budget_seconds))
    # Avoid runaway tiny chunks under degenerate fps; round down to integer-frame multiples.
    effective = max(effective, 1.0 / fps)

    chunks: list[ChunkPlan] = []
    cursor = 0.0
    idx = 0
    # Treat sub-frame remainders as zero to avoid 0.001s tail chunks.
    epsilon = 0.5 / fps
    while cursor + epsilon < duration_seconds:
        remaining = duration_seconds - cursor
        d = min(effective, remaining)
        chunks.append(
            ChunkPlan(
                index=idx,
                start_seconds=round(cursor, 3),
                duration_seconds=round(d, 3),
                end_seconds=round(cursor + d, 3),
            )
        )
        cursor += d
        idx += 1
    return chunks


def is_valid_frame_num(n: int) -> bool:
    """VACE constraint: clip must have 4n+1 frames."""
    return n > 0 and (n - 1) % 4 == 0


def round_to_4n_plus_1(n: int, *, ceil: bool = False) -> int:
    """Round ``n`` to the nearest 4k+1 value (>= 1).

    If ``ceil`` is True, round up; otherwise round to nearest.
    """
    if n < 1:
        return 1
    if ceil:
        k = (n - 1 + 3) // 4   # ceil division
        return 4 * k + 1
    # Nearest, with half-up tie-break (avoids Python's banker's rounding).
    k = (n - 1 + 2) // 4
    return max(1, 4 * k + 1)
