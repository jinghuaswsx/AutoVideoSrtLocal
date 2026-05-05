"""ROI compositing: blend a VACE-restored ROI back into the original 1080P chunk.

Algorithm (per chunk, frame-by-frame):
1. Read original chunk frame (full resolution, e.g. 1920x1080).
2. Read VACE output frame, strip its letterbox padding, resize back to the
   original ROI crop dimensions (e.g. 1920x424).
3. Build a feather-mask of the subtitle bbox in CROP coords (white = fix).
4. ``out = orig.copy(); out[crop_y1:crop_y2, crop_x1:crop_x2] = blend(orig_roi, vace_roi, mask)``
5. Write out to the composited chunk video.

OpenCV is the dependency of choice (already in the project's pipeline). All
heavy work is per-frame so memory stays bounded regardless of clip length.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from .bbox import Bbox, CropPlan, ScalePlan
from .mask import build_feather_mask

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CompositeResult:
    """Return value of :func:`composite_chunk`."""

    output_path: Path
    frames_written: int
    frames_skipped: int     # frames where VACE source ran out


def composite_chunk(
    *,
    original_chunk: Path,
    vace_chunk: Path,
    output_path: Path,
    crop: CropPlan,
    scale: ScalePlan,
    bbox_in_crop: Bbox,
    dilation_px: int,
    feather_px: int,
    fourcc: str = "mp4v",
) -> CompositeResult:
    """Composite a VACE-restored ROI back into the original chunk.

    Writes ``output_path`` as a video with the SAME resolution and fps as
    ``original_chunk``. Non-mask pixels are byte-identical to the source.
    """
    import numpy as np
    import cv2

    cap_orig = cv2.VideoCapture(str(original_chunk))
    cap_vace = cv2.VideoCapture(str(vace_chunk))
    if not cap_orig.isOpened():
        raise RuntimeError(f"cannot open original chunk: {original_chunk}")
    if not cap_vace.isOpened():
        cap_orig.release()
        raise RuntimeError(f"cannot open VACE chunk: {vace_chunk}")

    width = int(cap_orig.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap_orig.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap_orig.get(cv2.CAP_PROP_FPS) or 0.0
    if width <= 0 or height <= 0 or fps <= 0:
        cap_orig.release()
        cap_vace.release()
        raise RuntimeError(
            f"invalid original chunk geometry {width}x{height}@{fps}"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(
        str(output_path),
        cv2.VideoWriter_fourcc(*fourcc),
        fps,
        (width, height),
    )
    if not writer.isOpened():
        cap_orig.release()
        cap_vace.release()
        raise RuntimeError(f"cannot open writer: {output_path}")

    # Pre-build the mask once; same for every frame in this chunk.
    mask = build_feather_mask(
        crop_width=crop.width,
        crop_height=crop.height,
        bbox_in_crop=bbox_in_crop,
        dilation_px=dilation_px,
        feather_px=feather_px,
    ).astype(np.float32) / 255.0
    mask_3c = cv2.cvtColor((mask * 255.0).astype(np.uint8), cv2.COLOR_GRAY2BGR).astype(np.float32) / 255.0

    cx1, cy1, cx2, cy2 = crop.crop_bbox
    inner_x1 = scale.pad_left
    inner_y1 = scale.pad_top
    inner_x2 = inner_x1 + scale.inner_width
    inner_y2 = inner_y1 + scale.inner_height

    frames_written = 0
    frames_skipped = 0

    try:
        while True:
            ok_orig, frame_orig = cap_orig.read()
            if not ok_orig:
                break

            ok_vace, frame_vace = cap_vace.read()
            if not ok_vace:
                # VACE clip ran out of frames before original — keep original
                # for the rest of the chunk and log how many.
                writer.write(frame_orig)
                frames_skipped += 1
                continue

            # Strip letterbox -> back to inner crop -> resize to ROI crop size.
            roi_padded = frame_vace[inner_y1:inner_y2, inner_x1:inner_x2]
            if roi_padded.shape[0] <= 0 or roi_padded.shape[1] <= 0:
                writer.write(frame_orig)
                frames_skipped += 1
                continue
            roi_full = cv2.resize(
                roi_padded, (crop.width, crop.height), interpolation=cv2.INTER_LANCZOS4
            )

            # Blend within the crop region only; leave the rest of the frame untouched.
            orig_roi = frame_orig[cy1:cy2, cx1:cx2].astype(np.float32)
            vace_roi = roi_full.astype(np.float32)
            blended = orig_roi * (1.0 - mask_3c) + vace_roi * mask_3c
            frame_orig[cy1:cy2, cx1:cx2] = blended.astype("uint8")
            writer.write(frame_orig)
            frames_written += 1
    finally:
        cap_orig.release()
        cap_vace.release()
        writer.release()

    log.info(
        "vace_subtitle: composited %s frames=%d skipped=%d -> %s",
        original_chunk.name, frames_written, frames_skipped, output_path,
    )
    return CompositeResult(
        output_path=output_path,
        frames_written=frames_written,
        frames_skipped=frames_skipped,
    )
