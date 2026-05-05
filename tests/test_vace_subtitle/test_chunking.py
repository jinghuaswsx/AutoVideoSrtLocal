"""Chunk-planning tests."""
from __future__ import annotations

import pytest

from appcore.vace_subtitle.chunking import (
    is_valid_frame_num,
    plan_chunks,
    round_to_4n_plus_1,
)


@pytest.mark.parametrize("n,expected", [
    (1, True), (5, True), (9, True), (41, True), (81, True),
    (0, False), (2, False), (4, False), (40, False), (-3, False),
])
def test_is_valid_frame_num(n, expected):
    assert is_valid_frame_num(n) is expected


@pytest.mark.parametrize("n,ceil_,expected", [
    (1, False, 1), (3, False, 5), (5, False, 5), (10, False, 9),
    (42, False, 41), (43, False, 45),
    (1, True, 1), (2, True, 5), (5, True, 5), (6, True, 9),
])
def test_round_to_4n_plus_1(n, ceil_, expected):
    assert round_to_4n_plus_1(n, ceil=ceil_) == expected


def test_plan_chunks_simple():
    """frame_num=81 / fps=30 = 2.7s budget >= chunk_seconds=2.5; chunk_seconds wins."""
    chunks = plan_chunks(duration_seconds=10.0, fps=30.0,
                         chunk_seconds=2.5, frame_num=81)
    assert len(chunks) == 4
    assert chunks[0].start_seconds == 0.0
    assert pytest.approx(chunks[-1].end_seconds, abs=0.01) == 10.0


def test_plan_chunks_capped_by_frame_budget():
    """frame_num/fps cap dominates when it's smaller than chunk_seconds."""
    # frame_num=41 / fps=30 = 1.366 sec budget; chunk_seconds=5.0 should be capped
    chunks = plan_chunks(duration_seconds=4.0, fps=30.0,
                         chunk_seconds=5.0, frame_num=41)
    for c in chunks:
        assert c.duration_seconds <= 41 / 30 + 0.01


def test_plan_chunks_short_video():
    """Video shorter than first chunk slot yields exactly one chunk == video length."""
    # budget=81/30=2.7s; chunk_seconds=3.0 capped to 2.7; video=1.5 < 2.7 -> 1 chunk
    chunks = plan_chunks(duration_seconds=1.5, fps=30.0,
                         chunk_seconds=3.0, frame_num=81)
    assert len(chunks) == 1
    assert pytest.approx(chunks[0].duration_seconds, abs=0.01) == 1.5


def test_plan_chunks_zero_duration():
    assert plan_chunks(duration_seconds=0.0, fps=30.0,
                       chunk_seconds=3.0, frame_num=41) == []


def test_plan_chunks_invalid_args():
    with pytest.raises(ValueError):
        plan_chunks(duration_seconds=10.0, fps=0.0, chunk_seconds=3.0, frame_num=41)
    with pytest.raises(ValueError):
        plan_chunks(duration_seconds=10.0, fps=30.0, chunk_seconds=0, frame_num=41)
    with pytest.raises(ValueError):
        plan_chunks(duration_seconds=10.0, fps=30.0, chunk_seconds=3.0, frame_num=0)


def test_plan_chunks_indices_are_sequential():
    chunks = plan_chunks(duration_seconds=15.0, fps=30.0,
                         chunk_seconds=3.0, frame_num=81)
    indices = [c.index for c in chunks]
    assert indices == list(range(len(indices)))
