"""Feathered subtitle mask generation.

The mask is white inside the (dilated, feathered) subtitle bbox and black
elsewhere. It's used to alpha-blend the VACE-restored crop back into the
original frame so non-subtitle pixels stay 100% pristine.

OpenCV is imported lazily inside functions so unit tests that only exercise
geometry (e.g. tests of bbox math) can run without cv2 installed.
"""
from __future__ import annotations

from .bbox import Bbox, DEFAULT_DILATION_PX, DEFAULT_FEATHER_PX


def build_feather_mask(
    crop_width: int,
    crop_height: int,
    bbox_in_crop: Bbox,
    *,
    dilation_px: int = DEFAULT_DILATION_PX,
    feather_px: int = DEFAULT_FEATHER_PX,
):
    """Return a (H, W) uint8 mask in [0, 255] of the dilated, feathered bbox.

    Args:
        crop_width / crop_height: dimensions of the ROI crop.
        bbox_in_crop: subtitle bbox in CROP coordinates.
        dilation_px: number of pixels to grow the white region before feather.
        feather_px: Gaussian-blur kernel half-width for soft edges.

    Returns:
        ``numpy.ndarray`` of shape ``(crop_height, crop_width)``, dtype uint8.
    """
    import numpy as np
    import cv2

    if crop_width <= 0 or crop_height <= 0:
        raise ValueError(f"crop has non-positive size {crop_width}x{crop_height}")
    x1, y1, x2, y2 = bbox_in_crop
    if not (0 <= x1 < x2 <= crop_width and 0 <= y1 < y2 <= crop_height):
        raise ValueError(
            f"bbox_in_crop {bbox_in_crop} out of crop bounds {crop_width}x{crop_height}"
        )

    mask = np.zeros((crop_height, crop_width), dtype=np.uint8)
    mask[y1:y2, x1:x2] = 255

    if dilation_px > 0:
        ksize = max(1, 2 * int(dilation_px) + 1)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (ksize, ksize))
        mask = cv2.dilate(mask, kernel)

    if feather_px > 0:
        # Gaussian kernel needs odd size; sigma derived from radius.
        ksize = max(1, 2 * int(feather_px) + 1)
        mask = cv2.GaussianBlur(mask, (ksize, ksize), sigmaX=feather_px)

    return mask
