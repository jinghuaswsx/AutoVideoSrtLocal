from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import imagehash
import numpy as np
from PIL import Image, ImageOps
from skimage import filters
from skimage.measure import label, regionprops
from skimage.metrics import structural_similarity


_TARGET_SIZE = 256
_PENALTY_TARGET_SIZE = 512
_BINARY_SIZE = 100
_BINARY_THRESHOLD = 0.90
_MATCH_THRESHOLD = 0.80
_WEAK_MATCH_THRESHOLD = 0.65


@dataclass(frozen=True)
class _PreparedImage:
    image: Image.Image
    width: int
    height: int

    @property
    def ratio(self) -> float:
        return self.width / self.height if self.height else 0.0


def _prepare_image(path: str | Path, *, target_size: int = _TARGET_SIZE) -> _PreparedImage:
    image = Image.open(path)
    image = ImageOps.exif_transpose(image)
    image = image.convert("RGB")
    width, height = image.size

    fitted = image.copy()
    fitted.thumbnail((target_size, target_size), Image.Resampling.LANCZOS)

    canvas = Image.new("RGB", (target_size, target_size), "white")
    offset_x = (target_size - fitted.width) // 2
    offset_y = (target_size - fitted.height) // 2
    canvas.paste(fitted, (offset_x, offset_y))

    return _PreparedImage(image=canvas, width=width, height=height)


def _prepare_binary_mask(path: str | Path) -> np.ndarray:
    image = Image.open(path)
    image = ImageOps.exif_transpose(image)
    image = image.convert("RGB")
    image.thumbnail((_BINARY_SIZE, _BINARY_SIZE), Image.Resampling.LANCZOS)

    canvas = Image.new("RGB", (_BINARY_SIZE, _BINARY_SIZE), "white")
    offset_x = (_BINARY_SIZE - image.width) // 2
    offset_y = (_BINARY_SIZE - image.height) // 2
    canvas.paste(image, (offset_x, offset_y))

    gray = np.asarray(canvas.convert("L"), dtype=np.uint8)
    threshold = filters.threshold_local(gray, block_size=21, offset=8)
    return (gray <= threshold).astype(np.uint8)


def _compute_dark_area_penalty(candidate_path: str | Path, reference_path: str | Path) -> float:
    candidate = _prepare_image(candidate_path, target_size=_PENALTY_TARGET_SIZE)
    reference = _prepare_image(reference_path, target_size=_PENALTY_TARGET_SIZE)

    candidate_gray = np.asarray(candidate.image.convert("L"), dtype=np.float32)
    reference_gray = np.asarray(reference.image.convert("L"), dtype=np.float32)
    dark_mask = (candidate_gray < 220.0) | (reference_gray < 220.0)
    if not np.any(dark_mask):
        return 0.0

    high_diff = np.abs(candidate_gray - reference_gray) > 30.0
    high_diff_ratio = float(np.mean(high_diff[dark_mask]))
    if not np.any(high_diff):
        return 0.0

    labeled = label(high_diff)
    small_area = 0.0
    total_area = 0.0
    height, width = high_diff.shape
    for region in regionprops(labeled):
        min_row, min_col, max_row, max_col = region.bbox
        if min_row == 0 or min_col == 0 or max_row == height or max_col == width:
            continue
        area = float(region.area)
        total_area += area
        if area <= 100.0:
            small_area += area

    if total_area <= 0.0:
        return 0.0

    small_component_ratio = small_area / total_area
    if high_diff_ratio >= 0.7:
        return small_component_ratio * 0.25
    if high_diff_ratio >= 0.25:
        return high_diff_ratio
    return small_component_ratio * max(0.0, 1.0 - high_diff_ratio)


def _score_from_distance(distance: int) -> float:
    return max(0.0, 1.0 - (distance / 64.0))


def _classify_score(score: float) -> str:
    if score >= _MATCH_THRESHOLD:
        return "matched"
    if score >= _WEAK_MATCH_THRESHOLD:
        return "weak_match"
    return "not_matched"


def compare_images(candidate_path: str | Path, reference_path: str | Path) -> dict:
    candidate = _prepare_image(candidate_path)
    reference = _prepare_image(reference_path)

    candidate_image = candidate.image
    reference_image = reference.image

    phash_distance = int(imagehash.phash(candidate_image) - imagehash.phash(reference_image))
    dhash_distance = int(imagehash.dhash(candidate_image) - imagehash.dhash(reference_image))

    candidate_gray = np.asarray(candidate_image.convert("L"), dtype=np.float32)
    reference_gray = np.asarray(reference_image.convert("L"), dtype=np.float32)
    ssim_score = float(structural_similarity(candidate_gray, reference_gray, data_range=255.0))

    ratio_delta = abs(candidate.ratio - reference.ratio)
    dark_area_penalty = _compute_dark_area_penalty(candidate_path, reference_path)
    phash_score = _score_from_distance(phash_distance)
    dhash_score = _score_from_distance(dhash_distance)
    ratio_score = max(0.0, 1.0 - min(ratio_delta, 1.0))
    score = (
        phash_score * 0.40
        + dhash_score * 0.25
        + ssim_score * 0.30
        + ratio_score * 0.05
        - dark_area_penalty * 0.50
    )
    score = round(max(0.0, score), 4)

    return {
        "status": _classify_score(score),
        "score": score,
        "phash_distance": phash_distance,
        "dhash_distance": dhash_distance,
        "ssim": round(ssim_score, 4),
        "ratio_delta": round(ratio_delta, 4),
    }


def run_binary_quick_check(candidate_path: str | Path, reference_path: str | Path) -> dict:
    try:
        candidate = _prepare_binary_mask(candidate_path)
        reference = _prepare_binary_mask(reference_path)
    except Exception as exc:
        return {
            "status": "error",
            "binary_similarity": 0.0,
            "foreground_overlap": 0.0,
            "threshold": _BINARY_THRESHOLD,
            "reason": f"二值快检执行失败：{exc}",
        }

    binary_similarity = float(np.mean(candidate == reference))
    foreground_union = (candidate == 1) | (reference == 1)
    if np.any(foreground_union):
        overlap = float(np.sum((candidate == 1) & (reference == 1)) / np.sum(foreground_union))
    else:
        overlap = 1.0

    status = (
        "pass"
        if binary_similarity >= _BINARY_THRESHOLD and overlap >= _BINARY_THRESHOLD
        else "fail"
    )
    reason = (
        "参考图已匹配，二值相似度达到阈值，直接通过"
        if status == "pass"
        else "参考图已匹配，但二值相似度低于阈值，判定需要替换"
    )
    return {
        "status": status,
        "binary_similarity": round(binary_similarity, 4),
        "foreground_overlap": round(overlap, 4),
        "threshold": _BINARY_THRESHOLD,
        "reason": reason,
    }


def find_best_reference(candidate_path: str | Path, reference_paths: Iterable[str | Path]) -> dict:
    best_result: dict | None = None
    best_reference_path: str | None = None

    for reference_path in reference_paths:
        result = compare_images(candidate_path, reference_path)
        if best_result is None or result["score"] > best_result["score"]:
            best_result = result
            best_reference_path = str(reference_path)

    if best_result is None:
        return {
            "status": "not_provided",
            "score": 0.0,
            "phash_distance": None,
            "dhash_distance": None,
            "ssim": None,
            "ratio_delta": None,
            "reference_path": "",
        }

    return {
        **best_result,
        "reference_path": best_reference_path or "",
    }
