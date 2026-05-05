"""Bbox geometry tests for the VACE backend."""
from __future__ import annotations

import pytest

from appcore.vace_subtitle.bbox import (
    DEFAULT_CONTEXT_BOTTOM_PX,
    DEFAULT_CONTEXT_TOP_PX,
    compute_crop_plan,
    compute_scale_plan,
    default_subtitle_bbox,
    map_vace_bbox_to_crop_bbox,
    normalize_bbox,
    parse_bbox_arg,
)


# ---------------------------------------------------------------------------
# default_subtitle_bbox
# ---------------------------------------------------------------------------

def test_default_subtitle_bbox_1080p():
    """For 1920x1080, default bbox should be approximately (0, 778, 1920, 1026)."""
    bbox = default_subtitle_bbox(1920, 1080)
    assert bbox[0] == 0
    assert bbox[2] == 1920
    assert 770 <= bbox[1] <= 786       # 0.72 * 1080 = 777.6 -> 778
    assert 1020 <= bbox[3] <= 1030     # 0.95 * 1080 = 1026


def test_default_subtitle_bbox_720p():
    bbox = default_subtitle_bbox(1280, 720)
    assert bbox[0] == 0 and bbox[2] == 1280
    assert bbox[1] < bbox[3] <= 720


def test_default_subtitle_bbox_invalid_dim():
    with pytest.raises(ValueError):
        default_subtitle_bbox(0, 1080)
    with pytest.raises(ValueError):
        default_subtitle_bbox(1920, -1)


# ---------------------------------------------------------------------------
# normalize_bbox / parse_bbox_arg
# ---------------------------------------------------------------------------

def test_normalize_bbox_none_falls_back_to_default():
    bbox = normalize_bbox(None, 1920, 1080)
    assert bbox[0] == 0 and bbox[2] == 1920


def test_normalize_bbox_user_supplied_passes_through():
    bbox = normalize_bbox((100, 800, 1800, 1000), 1920, 1080)
    assert bbox == (100, 800, 1800, 1000)


def test_normalize_bbox_rejects_out_of_bounds():
    with pytest.raises(ValueError):
        normalize_bbox((0, 0, 2000, 1080), 1920, 1080)
    with pytest.raises(ValueError):
        normalize_bbox((0, 0, 100, 1100), 1920, 1080)


def test_normalize_bbox_rejects_inverted():
    with pytest.raises(ValueError):
        normalize_bbox((100, 100, 50, 50), 1920, 1080)


@pytest.mark.parametrize("raw,expected", [
    ("0,780,1920,1025", (0, 780, 1920, 1025)),
    (" 10, 20, 30, 40 ", (10, 20, 30, 40)),
    (None, None),
    ("", None),
])
def test_parse_bbox_arg_happy(raw, expected):
    assert parse_bbox_arg(raw) == expected


@pytest.mark.parametrize("raw", ["1,2,3", "a,b,c,d", "1,2,3,4,5"])
def test_parse_bbox_arg_rejects_garbage(raw):
    with pytest.raises(ValueError):
        parse_bbox_arg(raw)


# ---------------------------------------------------------------------------
# compute_crop_plan
# ---------------------------------------------------------------------------

def test_crop_plan_1080p_default():
    bbox = (0, 778, 1920, 1026)
    plan = compute_crop_plan(bbox, 1920, 1080)
    cx1, cy1, cx2, cy2 = plan.crop_bbox
    assert cx1 == 0 and cx2 == 1920
    assert cy1 == 778 - DEFAULT_CONTEXT_TOP_PX
    assert cy2 == min(1080, 1026 + DEFAULT_CONTEXT_BOTTOM_PX)
    assert plan.width == 1920
    assert plan.height == cy2 - cy1


def test_crop_plan_clamps_to_frame():
    """Context must not push beyond frame edges."""
    bbox = (0, 5, 1920, 50)              # near top edge
    plan = compute_crop_plan(bbox, 1920, 1080,
                             context_top_px=200, context_bottom_px=200)
    assert plan.crop_bbox[1] >= 0
    assert plan.crop_bbox[3] <= 1080


def test_crop_plan_bbox_in_crop_offsets():
    bbox = (100, 800, 1800, 1000)
    plan = compute_crop_plan(bbox, 1920, 1080,
                             context_top_px=50, context_bottom_px=20)
    cx1, cy1, _, _ = plan.crop_bbox
    bx1, by1, bx2, by2 = plan.bbox_in_crop
    assert bx1 == 100 - cx1
    assert by1 == 800 - cy1
    assert bx2 == 1800 - cx1
    assert by2 == 1000 - cy1
    # bbox must lie entirely inside the crop
    assert 0 <= bx1 < bx2 <= plan.width
    assert 0 <= by1 < by2 <= plan.height


# ---------------------------------------------------------------------------
# compute_scale_plan + roundtrip
# ---------------------------------------------------------------------------

def test_scale_plan_1080p_safe():
    bbox = (0, 778, 1920, 1026)
    crop = compute_crop_plan(bbox, 1920, 1080)
    scale = compute_scale_plan(crop, max_long_edge=832, max_short_edge=480)
    # safe profile: long edge capped at 832
    assert scale.target_width <= 832
    assert scale.target_height <= 480
    # alignment to ALIGN_TO=16
    assert scale.target_width % 16 == 0
    assert scale.target_height % 16 == 0
    # vace bbox must be inside the canvas
    vx1, vy1, vx2, vy2 = scale.bbox_in_vace
    assert 0 <= vx1 < vx2 <= scale.target_width
    assert 0 <= vy1 < vy2 <= scale.target_height


def test_scale_plan_inverse_mapping_close_to_input():
    """vace_bbox -> crop_bbox should round-trip within rounding tolerance."""
    bbox = (50, 800, 1500, 1000)
    crop = compute_crop_plan(bbox, 1920, 1080)
    scale = compute_scale_plan(crop, max_long_edge=832, max_short_edge=480)
    recovered = map_vace_bbox_to_crop_bbox(scale, scale.bbox_in_vace)
    bx1, by1, bx2, by2 = crop.bbox_in_crop
    rx1, ry1, rx2, ry2 = recovered
    assert abs(rx1 - bx1) <= 2
    assert abs(ry1 - by1) <= 2
    assert abs(rx2 - bx2) <= 2
    assert abs(ry2 - by2) <= 2


def test_scale_plan_quality_profile_caps():
    bbox = (0, 778, 1920, 1026)
    crop = compute_crop_plan(bbox, 1920, 1080)
    scale = compute_scale_plan(crop, max_long_edge=1280, max_short_edge=720)
    assert scale.target_width <= 1280
    assert scale.target_height <= 720
