from pathlib import Path

from PIL import Image, ImageDraw


def _draw_scaled_text(image: Image.Image, text: str, *, box: tuple[int, int, int, int]) -> None:
    x0, y0, x1, y1 = box
    base = Image.new("L", (max(1, x1 - x0), max(1, y1 - y0)), 255)
    base_draw = ImageDraw.Draw(base)
    base_draw.text((10, 8), text, fill=0)
    scaled = base.resize((base.width * 8, base.height * 8), Image.Resampling.NEAREST)
    image.paste(Image.merge("RGB", (scaled, scaled, scaled)), (x0, y0))


def _make_sample(path: Path, *, size: tuple[int, int], quality: int = 95) -> Path:
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    width, height = size
    draw.rectangle((24, 24, width - 24, height - 24), outline="navy", width=8)
    draw.ellipse((width * 0.35, height * 0.28, width * 0.65, height * 0.58), outline="teal", width=6)
    draw.line((32, height - 48, width - 32, 48), fill="black", width=5)
    image.save(path, quality=quality)
    return path


def _make_text_variant(path: Path, *, text: str) -> Path:
    image = Image.new("RGB", (1200, 800), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((24, 24, 1176, 776), outline="navy", width=8)
    draw.ellipse((360, 180, 840, 560), outline="teal", width=6)
    draw.line((32, 720, 1168, 80), fill="black", width=5)
    _draw_scaled_text(image, text, box=(260, 260, 940, 540))
    image.save(path, quality=90)
    return path


def test_desktop_same_image_with_different_sizes_matches(tmp_path):
    from link_check_desktop import image_compare

    left = _make_sample(tmp_path / "left.jpg", size=(1200, 800))
    right = _make_sample(tmp_path / "right.jpg", size=(600, 400))

    result = image_compare.compare_images(left, right)

    assert result["status"] == "matched"
    assert result["score"] >= 0.85


def test_desktop_different_text_layout_does_not_match(tmp_path):
    from link_check_desktop import image_compare

    left = _make_text_variant(tmp_path / "left.jpg", text="SALE TODAY")
    right = _make_text_variant(tmp_path / "right.jpg", text="NEW ARRIVAL")

    result = image_compare.compare_images(left, right)

    assert result["status"] != "matched"
    assert result["score"] < 0.80


def test_desktop_binary_quick_check_fails_when_text_changes(tmp_path):
    from link_check_desktop import image_compare

    left = _make_text_variant(tmp_path / "left.jpg", text="SALE TODAY")
    right = _make_text_variant(tmp_path / "right.jpg", text="NEW ARRIVAL")

    result = image_compare.run_binary_quick_check(left, right)

    assert result["status"] == "fail"
    assert result["foreground_overlap"] < 0.90


def test_desktop_find_best_reference_uses_highest_score(tmp_path):
    from link_check_desktop import image_compare

    candidate = _make_sample(tmp_path / "candidate.jpg", size=(1200, 800), quality=90)
    weak = Image.new("RGB", (1200, 800), "red")
    weak_path = tmp_path / "weak.jpg"
    weak.save(weak_path)
    strong = _make_sample(tmp_path / "strong.jpg", size=(600, 400), quality=40)

    result = image_compare.find_best_reference(candidate, [weak_path, strong])

    assert result["status"] == "matched"
    assert result["reference_path"] == str(strong)
    assert result["score"] >= 0.80
