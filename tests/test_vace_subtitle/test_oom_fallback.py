"""OOM fallback policy tests (covers config.fallback_profile + runtime trigger).

The runtime test focuses on policy, not on actually launching subprocesses;
``run_invocation`` is mocked.
"""
from __future__ import annotations

from appcore.vace_subtitle.config import fallback_profile, get_profile


def test_fallback_quality_to_safe():
    quality = get_profile("rtx3060_quality_experimental")
    fb = fallback_profile(quality)
    assert fb is not None
    assert fb.name == "rtx3060_safe"


def test_fallback_balanced_drops_frame_num():
    """81 -> 41 in one step; name is annotated so callers can log it."""
    balanced = get_profile("rtx3060_balanced")
    fb = fallback_profile(balanced)
    assert fb is not None
    assert fb.frame_num == 41
    assert balanced.name in fb.name


def test_fallback_safe_drops_chunk_seconds_eventually():
    """Safe is at frame_num=41/steps=20; further fallback only adjusts chunk_seconds."""
    safe = get_profile("rtx3060_safe")
    fb = fallback_profile(safe)
    assert fb is not None
    # safe's chunk_seconds=2.7 -> 2.5
    assert fb.chunk_seconds == 2.5


def test_fallback_terminal_returns_none():
    """Once at the floor, no further fallback."""
    safe = get_profile("rtx3060_safe")
    cur = safe
    seen = {cur.name}
    # walk fallbacks until we hit None
    for _ in range(10):
        nxt = fallback_profile(cur)
        if nxt is None:
            break
        # Must converge to ever-more-conservative settings
        assert (nxt.frame_num, nxt.sample_steps, nxt.chunk_seconds) <= (
            cur.frame_num, cur.sample_steps, cur.chunk_seconds
        )
        cur = nxt
        seen.add(cur.name)
    assert fallback_profile(cur) is None
