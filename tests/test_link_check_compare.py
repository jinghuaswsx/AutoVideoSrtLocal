from pathlib import Path

import pytest
from PIL import Image, ImageDraw


def _make_sample(path: Path, *, size: tuple[int, int], quality: int = 95) -> Path:
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    width, height = size
    draw.rectangle((24, 24, width - 24, height - 24), outline="navy", width=8)
    draw.ellipse((width * 0.35, height * 0.28, width * 0.65, height * 0.58), outline="teal", width=6)
    draw.line((32, height - 48, width - 32, 48), fill="black", width=5)
    image.save(path, quality=quality)
    return path


def _make_rotated_sample(path: Path) -> Path:
    image = Image.new("RGB", (600, 900), "white")
    draw = ImageDraw.Draw(image)
    width, height = image.size
    draw.rectangle((24, 24, width - 24, height - 24), outline="navy", width=8)
    draw.ellipse((width * 0.30, height * 0.30, width * 0.70, height * 0.55), outline="teal", width=6)
    draw.line((32, height - 48, width - 32, 48), fill="black", width=5)
    stored = image.transpose(Image.Transpose.ROTATE_270)
    exif = image.getexif()
    exif[274] = 8  # Orientation: rotate 90 degrees counter-clockwise to restore base image.
    stored.save(path, exif=exif.tobytes(), quality=92)
    return path


def test_same_image_with_different_sizes_matches(tmp_path):
    from appcore.link_check_compare import compare_images

    left = _make_sample(tmp_path / "left.jpg", size=(1200, 800))
    right = _make_sample(tmp_path / "right.jpg", size=(600, 400))

    result = compare_images(left, right)

    assert result["status"] == "matched"
    assert result["score"] >= 0.85
    assert set(result) >= {
        "status",
        "score",
        "phash_distance",
        "dhash_distance",
        "ssim",
        "ratio_delta",
    }


def test_exif_rotated_same_image_matches(tmp_path):
    from appcore.link_check_compare import compare_images

    left = _make_rotated_sample(tmp_path / "left.jpg")
    right = _make_sample(tmp_path / "right.jpg", size=(600, 900))

    result = compare_images(left, right)

    assert result["status"] == "matched"
    assert result["score"] >= 0.80
    assert result["ratio_delta"] < 0.05


def test_different_images_do_not_match(tmp_path):
    from appcore.link_check_compare import compare_images

    left = _make_sample(tmp_path / "left.jpg", size=(1200, 800))
    other = Image.new("RGB", (1200, 800), "black")
    other_draw = ImageDraw.Draw(other)
    other_draw.rectangle((80, 80, 1120, 720), fill="red")
    other_draw.line((0, 0, 1199, 799), fill="yellow", width=18)
    other.save(tmp_path / "other.jpg")

    result = compare_images(left, tmp_path / "other.jpg")

    assert result["status"] == "not_matched"
    assert result["score"] < 0.65


def test_best_reference_uses_highest_score(tmp_path):
    from appcore.link_check_compare import find_best_reference

    candidate = _make_sample(tmp_path / "candidate.jpg", size=(1200, 800), quality=90)
    weak = Image.new("RGB", (1200, 800), "red")
    weak_path = tmp_path / "weak.jpg"
    weak.save(weak_path)
    strong = _make_sample(tmp_path / "strong.jpg", size=(600, 400), quality=40)

    result = find_best_reference(candidate, [weak_path, strong])

    assert result["status"] == "matched"
    assert result["reference_path"] == str(strong)
    assert result["score"] >= 0.80


def test_best_reference_rejects_empty_input():
    from appcore.link_check_compare import find_best_reference

    result = find_best_reference("candidate.jpg", [])

    assert result["status"] == "not_provided"
    assert result["score"] == 0.0
