"""Bbox utilities for the VACE subtitle removal pipeline.

Three coordinate spaces:
1. **original** — the input video (e.g. 1920x1080).
2. **crop**     — the ROI cropped from the original (e.g. 1920x424).
3. **vace**     — what's actually fed to VACE after scale+pad to model-friendly
   dimensions (e.g. 832x192 letterboxed onto 832x480).

Bbox tuple convention throughout: ``(x1, y1, x2, y2)`` with x2 > x1 and y2 > y1.
All coordinates are integer pixels. Closed-open is implicit (x2/y2 are exclusive
slice ends, matching numpy/OpenCV).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

Bbox = tuple[int, int, int, int]


# Public defaults (overridable via :class:`VaceWindowsSubtitleRemover` kwargs).
DEFAULT_BOTTOM_TOP_FRAC = 0.72       # bbox_y1 = round(height * 0.72)
DEFAULT_BOTTOM_BOTTOM_FRAC = 0.95    # bbox_y2 = round(height * 0.95)
DEFAULT_CONTEXT_TOP_PX = 128         # extra rows above bbox in ROI crop
DEFAULT_CONTEXT_BOTTOM_PX = 48       # extra rows below bbox in ROI crop
DEFAULT_DILATION_PX = 8              # mask dilation
DEFAULT_FEATHER_PX = 12              # mask feather (Gaussian blur radius approx)
ALIGN_TO = 16                        # encoder-friendly alignment for crop sizes


def default_subtitle_bbox(width: int, height: int) -> Bbox:
    """Return the default bottom-strip bbox for an unknown video.

    For 1920x1080 the result is approximately (0, 778, 1920, 1026).
    """
    if width <= 0 or height <= 0:
        raise ValueError(f"width/height must be positive, got {width}x{height}")
    y1 = round(height * DEFAULT_BOTTOM_TOP_FRAC)
    y2 = round(height * DEFAULT_BOTTOM_BOTTOM_FRAC)
    bbox = (0, int(y1), int(width), int(y2))
    log.info(
        "vace_subtitle: using auto-default bbox %s for %dx%d (override via --bbox)",
        bbox, width, height,
    )
    return _validate_bbox(bbox, width, height)


def _validate_bbox(bbox: Bbox, width: int, height: int) -> Bbox:
    x1, y1, x2, y2 = bbox
    if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
        raise ValueError(
            f"invalid bbox {bbox} for {width}x{height} "
            "(require 0 <= x1 < x2 <= W and 0 <= y1 < y2 <= H)"
        )
    return (int(x1), int(y1), int(x2), int(y2))


def normalize_bbox(
    bbox: Bbox | None,
    width: int,
    height: int,
) -> Bbox:
    """Validate user-supplied bbox or fall back to default."""
    if bbox is None:
        return default_subtitle_bbox(width, height)
    return _validate_bbox(bbox, width, height)


def parse_bbox_arg(value: str | None) -> Bbox | None:
    """Parse a CLI string ``x1,y1,x2,y2`` into a Bbox tuple. Empty -> None."""
    if value is None:
        return None
    s = value.strip()
    if not s:
        return None
    parts = [p.strip() for p in s.split(",")]
    if len(parts) != 4:
        raise ValueError(f"bbox must be 'x1,y1,x2,y2', got {value!r}")
    try:
        nums = tuple(int(p) for p in parts)
    except ValueError as exc:
        raise ValueError(f"bbox values must be integers, got {value!r}") from exc
    return nums  # validation deferred to normalize_bbox


def _align(value: int, multiple: int = ALIGN_TO) -> int:
    """Round ``value`` to the nearest multiple. Always >= multiple."""
    aligned = max(multiple, round(value / multiple) * multiple)
    return int(aligned)


@dataclass(frozen=True)
class CropPlan:
    """Geometry plan for one ROI crop operation."""

    crop_bbox: Bbox          # in ORIGINAL coords; the rectangle we cut out
    bbox_in_crop: Bbox       # bbox in CROP coords (offset by crop top-left)
    width: int               # crop width
    height: int              # crop height


def compute_crop_plan(
    bbox: Bbox,
    width: int,
    height: int,
    *,
    context_top_px: int = DEFAULT_CONTEXT_TOP_PX,
    context_bottom_px: int = DEFAULT_CONTEXT_BOTTOM_PX,
) -> CropPlan:
    """Inflate ``bbox`` with vertical context, clamp to frame, return CropPlan.

    Width is the full frame width (we always crop full rows).
    Height adds context_top_px/context_bottom_px and clamps to [0, height].
    """
    x1, y1, x2, y2 = _validate_bbox(bbox, width, height)
    crop_y1 = max(0, y1 - max(0, context_top_px))
    crop_y2 = min(height, y2 + max(0, context_bottom_px))
    crop_x1, crop_x2 = 0, width
    crop_bbox = (crop_x1, crop_y1, crop_x2, crop_y2)
    cw = crop_x2 - crop_x1
    ch = crop_y2 - crop_y1
    bbox_in_crop = (x1 - crop_x1, y1 - crop_y1, x2 - crop_x1, y2 - crop_y1)
    return CropPlan(
        crop_bbox=crop_bbox,
        bbox_in_crop=bbox_in_crop,
        width=cw,
        height=ch,
    )


@dataclass(frozen=True)
class ScalePlan:
    """Scale + letterbox plan for fitting a CropPlan into VACE input."""

    target_width: int       # final VACE input width (multiple of ALIGN_TO)
    target_height: int      # final VACE input height (multiple of ALIGN_TO)
    inner_width: int        # scaled crop content width (no padding)
    inner_height: int       # scaled crop content height (no padding)
    pad_left: int           # letterbox padding (always 0 for our W=full-width case)
    pad_top: int            # letterbox padding above content
    scale_x: float          # inner_width / crop.width
    scale_y: float          # inner_height / crop.height
    bbox_in_vace: tuple[int, int, int, int]  # bbox mapped into VACE input space


def compute_scale_plan(
    crop: CropPlan,
    *,
    max_long_edge: int,
    max_short_edge: int,
) -> ScalePlan:
    """Fit ``crop`` into a model-friendly canvas <= (max_long_edge, max_short_edge).

    Aspect ratio is preserved. Padding is centered (top/bottom only when crop
    is wider than max canvas). Final canvas is aligned to ALIGN_TO on both
    axes. The bbox is mapped through the same scale + offset.
    """
    cw, ch = crop.width, crop.height
    if cw <= 0 or ch <= 0:
        raise ValueError(f"crop has non-positive size {cw}x{ch}")

    # Decide which dim is the long edge and apply both caps.
    long_edge = max(cw, ch)
    short_edge = min(cw, ch)
    scale = min(max_long_edge / long_edge, max_short_edge / short_edge, 1.0)

    inner_w = max(1, int(round(cw * scale)))
    inner_h = max(1, int(round(ch * scale)))
    target_w = _align(inner_w)
    target_h = _align(inner_h)
    pad_left = (target_w - inner_w) // 2
    pad_top = (target_h - inner_h) // 2

    # bbox in VACE coords (after scale + pad)
    bx1, by1, bx2, by2 = crop.bbox_in_crop
    sx = inner_w / cw
    sy = inner_h / ch
    vbx1 = int(round(bx1 * sx)) + pad_left
    vby1 = int(round(by1 * sy)) + pad_top
    vbx2 = int(round(bx2 * sx)) + pad_left
    vby2 = int(round(by2 * sy)) + pad_top

    return ScalePlan(
        target_width=target_w,
        target_height=target_h,
        inner_width=inner_w,
        inner_height=inner_h,
        pad_left=pad_left,
        pad_top=pad_top,
        scale_x=sx,
        scale_y=sy,
        bbox_in_vace=(vbx1, vby1, vbx2, vby2),
    )


def map_vace_bbox_to_crop_bbox(plan: ScalePlan, vace_bbox: Bbox) -> Bbox:
    """Inverse of :func:`compute_scale_plan` for a single bbox.

    Strips letterbox offset and undoes the scale. Used when the caller needs
    to know where in CROP coordinates a VACE-space bbox actually lives.
    """
    vx1, vy1, vx2, vy2 = vace_bbox
    cx1 = int(round((vx1 - plan.pad_left) / plan.scale_x))
    cy1 = int(round((vy1 - plan.pad_top) / plan.scale_y))
    cx2 = int(round((vx2 - plan.pad_left) / plan.scale_x))
    cy2 = int(round((vy2 - plan.pad_top) / plan.scale_y))
    return (cx1, cy1, cx2, cy2)
