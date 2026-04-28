from __future__ import annotations

import argparse
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from tools.shopify_image_localizer import api_client
from tools.shopify_image_localizer import cancellation
from tools.shopify_image_localizer import controller
from tools.shopify_image_localizer import downloader
from tools.shopify_image_localizer.browser import session
from tools.shopify_image_localizer.rpa import ez_cdp
from tools.shopify_image_localizer.rpa import taa_cdp
from tools.shopify_image_localizer.rpa import run_product_cdp


def _localized(filename: str) -> dict:
    return {"filename": filename, "local_path": str(Path("C:/tmp") / filename)}


def _write_shape_image(path: Path, *, shape: str, fill: str = "black") -> None:
    image = Image.new("RGB", (160, 120), "white")
    draw = ImageDraw.Draw(image)
    if shape == "circle":
        draw.ellipse((44, 24, 116, 96), fill=fill)
    elif shape == "bar":
        draw.rectangle((36, 48, 124, 72), fill=fill)
    else:
        draw.rectangle((40, 28, 120, 92), fill=fill)
    image.save(path)


def test_downloader_fallback_shortens_long_shopify_filename_without_losing_match_keys():
    token = "f348cc3161901b6173b86170ab9a2eca"
    filename = (
        "20260425_0b9f7177_20260420_ed1b2369_"
        f"from_url_en_10_{token}_"
        "9af389e3-ed41-4433-8a5f-a1b16fb37c59.png"
    )

    safe = downloader._safe_filename(
        filename,
        "fallback.png",
        max_length=downloader.FALLBACK_FILENAME_LENGTH,
    )

    assert safe != filename
    assert len(safe) <= downloader.FALLBACK_FILENAME_LENGTH
    assert f"from_url_en_10_{token}" in safe
    assert safe.endswith(".png")


def test_downloader_preserves_long_shopify_filename_when_path_is_valid(tmp_path, monkeypatch):
    token = "f348cc3161901b6173b86170ab9a2eca"
    filename = (
        "20260425_0b9f7177_20260420_ed1b2369_"
        f"from_url_en_10_{token}_"
        "9af389e3-ed41-4433-8a5f-a1b16fb37c59.png"
    )

    class DummyResponse:
        content = b"image-bytes"

        @staticmethod
        def raise_for_status():
            return None

    monkeypatch.setattr(downloader.requests, "get", lambda *_args, **_kwargs: DummyResponse())

    downloaded = downloader.download_images(
        [{"id": "image-10", "filename": filename, "url": "https://cdn.example.com/image.png"}],
        tmp_path,
    )

    local_path = Path(downloaded[0]["local_path"])
    assert local_path.parent == tmp_path
    assert local_path.is_file()
    assert local_path.read_bytes() == b"image-bytes"
    assert local_path.name == filename
    assert len(local_path.name) <= downloader.MAX_FILENAME_LENGTH
    assert f"from_url_en_10_{token}" in local_path.name
    assert downloaded[0]["filename"] == local_path.name
    assert downloaded[0]["original_filename"] == filename


def test_downloader_shortens_only_when_filename_exceeds_windows_limit(tmp_path, monkeypatch):
    token = "f348cc3161901b6173b86170ab9a2eca"
    filename = (
        "20260425_0b9f7177_20260420_ed1b2369_"
        f"from_url_en_10_{token}_"
        f"{'tail_' * 45}.png"
    )
    assert len(filename) > downloader.MAX_FILENAME_LENGTH

    class DummyResponse:
        content = b"image-bytes"

        @staticmethod
        def raise_for_status():
            return None

    monkeypatch.setattr(downloader.requests, "get", lambda *_args, **_kwargs: DummyResponse())

    downloaded = downloader.download_images(
        [{"id": "image-10", "filename": filename, "url": "https://cdn.example.com/image.png"}],
        tmp_path,
    )

    local_path = Path(downloaded[0]["local_path"])
    assert local_path.name != filename
    assert len(local_path.name) <= downloader.MAX_FILENAME_LENGTH
    assert f"from_url_en_10_{token}" in local_path.name
    assert downloaded[0]["filename"] == local_path.name
    assert downloaded[0]["original_filename"] == filename


def test_pair_carousel_images_prefers_matching_source_index_for_duplicate_tokens():
    token = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    product_images = [
        {"src": f"https://cdn.shopify.com/files/{token}.jpg"},
        {"src": "https://cdn.shopify.com/files/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.jpg"},
        {"src": f"https://cdn.shopify.com/files/{token}_copy.jpg"},
    ]
    localized_images = [
        _localized(f"loc_from_url_en_02_{token}.jpg"),
        _localized(f"loc_from_url_en_00_{token}.jpg"),
        _localized("loc_from_url_en_01_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.jpg"),
    ]

    pairs = run_product_cdp.pair_carousel_images(localized_images, product_images)

    assert pairs == [
        (0, str(Path("C:/tmp") / f"loc_from_url_en_00_{token}.jpg")),
        (1, str(Path("C:/tmp") / "loc_from_url_en_01_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.jpg")),
        (2, str(Path("C:/tmp") / f"loc_from_url_en_02_{token}.jpg")),
    ]


def test_pair_carousel_images_falls_back_to_source_index_when_urls_have_no_hash_token():
    product_images = [
        {"src": "https://cdn.shopify.com/files/nano6_8ec008c6-5d50-41f9-9f75-f54df04cbf0f.jpg?v=1"},
        {"src": "https://cdn.shopify.com/files/nano3_688f4398-4308-4953-b792-f64f0e21c504.jpg?v=1"},
    ]
    localized_images = [
        _localized("loc_from_url_en_00_nano6_8ec008c6-5d50-41f9-9f75-f54df04cbf0f.png"),
        _localized("loc_from_url_en_01_nano3_688f4398-4308-4953-b792-f64f0e21c504.png"),
    ]

    pairs = run_product_cdp.pair_carousel_images(localized_images, product_images)

    assert pairs == [
        (0, str(Path("C:/tmp") / "loc_from_url_en_00_nano6_8ec008c6-5d50-41f9-9f75-f54df04cbf0f.png")),
        (1, str(Path("C:/tmp") / "loc_from_url_en_01_nano3_688f4398-4308-4953-b792-f64f0e21c504.png")),
    ]


def test_visual_carousel_pair_plan_matches_unkeyed_slot_to_localized_candidate(tmp_path):
    slot_path = tmp_path / "shopify-slot-a.png"
    reference_path = tmp_path / "server-reference-a.png"
    other_reference_path = tmp_path / "server-reference-b.png"
    localized_path = tmp_path / "translated-a.png"
    other_localized_path = tmp_path / "translated-b.png"
    _write_shape_image(slot_path, shape="circle")
    _write_shape_image(reference_path, shape="circle")
    _write_shape_image(other_reference_path, shape="bar")
    _write_shape_image(localized_path, shape="circle")
    _write_shape_image(other_localized_path, shape="bar")

    plan = run_product_cdp.build_visual_carousel_pair_plan(
        slot_images=[
            {
                "slot_id": "carousel-00",
                "slot_index": 0,
                "src": "https://cdn.shopify.com/files/non-token-a.jpg",
                "local_path": str(slot_path),
            }
        ],
        reference_images=[
            {"id": "ref-a", "filename": "server-reference-a.png", "local_path": str(reference_path)},
            {"id": "ref-b", "filename": "server-reference-b.png", "local_path": str(other_reference_path)},
        ],
        localized_images=[
            {"id": "loc-a", "filename": "translated-a.png", "local_path": str(localized_path)},
            {"id": "loc-b", "filename": "translated-b.png", "local_path": str(other_localized_path)},
        ],
    )

    assert plan["pairs"] == [(0, str(localized_path))]
    assert plan["confirmation_pairs"][0]["current_local_path"] == str(slot_path)
    assert plan["confirmation_pairs"][0]["replacement_local_path"] == str(localized_path)
    assert plan["confirmation_pairs"][0]["reference_filename"] == "server-reference-a.png"
    assert plan["confirmation_pairs"][0]["match_method"] == "visual"
    assert plan["review"] == []


def test_visual_pair_plan_keeps_compare_match_when_binary_check_warns(monkeypatch, tmp_path):
    slot_path = tmp_path / "slot.png"
    reference_path = tmp_path / "reference.png"
    localized_path = tmp_path / "localized.png"
    for path in (slot_path, reference_path, localized_path):
        path.write_bytes(b"image-placeholder")

    monkeypatch.setattr(
        run_product_cdp,
        "find_best_reference",
        lambda _image_path, _reference_paths: {
            "status": "matched",
            "score": 0.91,
            "phash_distance": 4,
            "dhash_distance": 5,
            "ssim": 0.88,
            "ratio_delta": 0.0,
            "reference_path": str(reference_path),
        },
    )
    monkeypatch.setattr(
        run_product_cdp,
        "run_binary_quick_check",
        lambda _image_path, _reference_path: {
            "status": "fail",
            "binary_similarity": 0.82,
            "foreground_overlap": 0.76,
            "threshold": 0.90,
        },
    )

    plan = run_product_cdp.build_visual_carousel_pair_plan(
        slot_images=[{"slot_id": "carousel-00", "slot_index": 0, "src": "https://cdn/slot.jpg", "local_path": str(slot_path)}],
        reference_images=[{"id": "ref", "filename": "reference.png", "local_path": str(reference_path)}],
        localized_images=[{"id": "loc", "filename": "localized.png", "local_path": str(localized_path)}],
    )

    assert plan["pairs"] == [(0, str(localized_path))]
    assert plan["confirmation_pairs"][0]["binary_status"] == "fail"
    assert plan["confirmation_pairs"][0]["confidence"] == "needs_review"
    assert plan["review"] == []


def test_visual_carousel_confirmation_reject_cancels_replacement():
    with pytest.raises(cancellation.OperationCancelled):
        run_product_cdp.confirm_visual_carousel_pairs(
            [{"slot_index": 0, "replacement_local_path": "C:/tmp/a.png"}],
            confirm_cb=lambda _pairs: False,
        )


def test_visual_detail_replacement_plan_matches_unkeyed_detail_src(tmp_path):
    detail_path = tmp_path / "detail-slot.png"
    reference_path = tmp_path / "reference-detail.png"
    localized_path = tmp_path / "localized-detail.png"
    for path in (detail_path, reference_path, localized_path):
        _write_shape_image(path, shape="bar")

    src = "https://cdn.shopify.com/files/plain-detail.jpg?v=1"
    plan = run_product_cdp.build_visual_detail_replacement_plan(
        slot_images=[
            {
                "slot_id": "detail-00",
                "slot_index": 0,
                "src": src,
                "local_path": str(detail_path),
            }
        ],
        reference_images=[
            {"id": "ref-detail", "filename": "reference-detail.png", "local_path": str(reference_path)}
        ],
        localized_images=[
            {"id": "loc-detail", "filename": "localized-detail.png", "local_path": str(localized_path)}
        ],
    )

    assert plan["forced_replacements_by_src"][src]["local_path"] == str(localized_path)
    assert plan["confirmation_pairs"][0]["target_kind"] == "detail"
    assert plan["confirmation_pairs"][0]["replacement_local_path"] == str(localized_path)


def test_run_uses_confirmed_visual_carousel_fallback_when_deterministic_pairing_misses(monkeypatch, tmp_path):
    localized_path = tmp_path / "translated-a.png"
    reference_path = tmp_path / "reference-a.png"
    slot_path = tmp_path / "slot-a.png"
    for path in (localized_path, reference_path, slot_path):
        _write_shape_image(path, shape="circle")

    workspace = run_product_cdp.storage.Workspace(
        root=tmp_path,
        source_en_dir=tmp_path / "source" / "en",
        source_localized_dir=tmp_path / "source" / "localized",
        classify_ez_dir=tmp_path / "classify" / "ez",
        classify_taa_dir=tmp_path / "classify" / "taa",
        screenshots_ez_dir=tmp_path / "screenshots" / "ez",
        screenshots_taa_dir=tmp_path / "screenshots" / "taa",
        manifest_path=tmp_path / "manifest.json",
        log_path=tmp_path / "run.log",
    )
    for path in (
        workspace.source_en_dir,
        workspace.source_localized_dir,
        workspace.classify_ez_dir,
        workspace.classify_taa_dir,
        workspace.screenshots_ez_dir,
        workspace.screenshots_taa_dir,
    ):
        path.mkdir(parents=True, exist_ok=True)

    downloaded = [{"id": "loc-a", "filename": "translated-a.png", "local_path": str(localized_path)}]
    bootstrap = {
        "reference_images": [{"id": "ref-a", "filename": "reference-a.png", "url": "https://server/ref-a.png"}],
        "localized_images": [{"id": "loc-a", "filename": "translated-a.png", "url": "https://server/loc-a.png"}],
    }
    product = {
        "id": "8560000000000",
        "images": [{"src": "https://cdn.shopify.com/files/plain-name.jpg"}],
        "description": "",
    }
    replaced_pairs: list[tuple[int, str]] = []
    confirmed_pairs: list[dict] = []

    monkeypatch.setattr(run_product_cdp.settings, "load_runtime_config", lambda: {"browser_user_data_dir": "C:/chrome"})
    monkeypatch.setattr(run_product_cdp, "fetch_storefront_product", lambda *_args, **_kwargs: product)
    monkeypatch.setattr(run_product_cdp, "fetch_bootstrap_ready", lambda **_kwargs: bootstrap)
    monkeypatch.setattr(run_product_cdp, "download_localized", lambda *_args, **_kwargs: (workspace, downloaded))
    monkeypatch.setattr(run_product_cdp, "pair_carousel_images", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        run_product_cdp,
        "download_visual_carousel_sources",
        lambda **_kwargs: {
            "slot_images": [
                {
                    "slot_id": "carousel-00",
                    "slot_index": 0,
                    "src": "https://cdn.shopify.com/files/plain-name.jpg",
                    "local_path": str(slot_path),
                }
            ],
            "reference_images": [
                {"id": "ref-a", "filename": "reference-a.png", "local_path": str(reference_path)}
            ],
        },
    )
    monkeypatch.setattr(run_product_cdp.session, "build_ez_url", lambda product_id: f"https://ez/{product_id}")

    def fake_replace_many(**kwargs):
        replaced_pairs.extend(kwargs["pairs"])
        return [{"slot_index": 0, "status": "ok"}]

    monkeypatch.setattr(run_product_cdp.ez_cdp, "replace_many", fake_replace_many)

    args = argparse.Namespace(
        product_code="plain-name-product-rjc",
        lang="de",
        shop_locale="de",
        language="German",
        product_id="",
        store_domain="newjoyloo.com",
        bootstrap_timeout_s=120,
        port=7777,
        carousel_limit=0,
        skip_carousel=False,
        skip_detail=True,
        skip_existing_carousel=False,
    )

    result = run_product_cdp.run(
        args,
        visual_pair_confirm_cb=lambda pairs: confirmed_pairs.extend(pairs) or True,
    )

    assert replaced_pairs == [(0, str(localized_path))]
    assert confirmed_pairs[0]["match_method"] == "visual"
    assert result["carousel"]["visual_fallback_count"] == 1


def test_run_uses_confirmed_visual_detail_fallback_when_plan_has_missing_src(monkeypatch, tmp_path):
    localized_path = tmp_path / "localized-detail.png"
    reference_path = tmp_path / "reference-detail.png"
    detail_path = tmp_path / "detail-slot.png"
    for path in (localized_path, reference_path, detail_path):
        _write_shape_image(path, shape="bar")

    workspace = run_product_cdp.storage.Workspace(
        root=tmp_path,
        source_en_dir=tmp_path / "source" / "en",
        source_localized_dir=tmp_path / "source" / "localized",
        classify_ez_dir=tmp_path / "classify" / "ez",
        classify_taa_dir=tmp_path / "classify" / "taa",
        screenshots_ez_dir=tmp_path / "screenshots" / "ez",
        screenshots_taa_dir=tmp_path / "screenshots" / "taa",
        manifest_path=tmp_path / "manifest.json",
        log_path=tmp_path / "run.log",
    )
    for path in (
        workspace.source_en_dir,
        workspace.source_localized_dir,
        workspace.classify_ez_dir,
        workspace.classify_taa_dir,
        workspace.screenshots_ez_dir,
        workspace.screenshots_taa_dir,
    ):
        path.mkdir(parents=True, exist_ok=True)

    src = "https://cdn.shopify.com/files/plain-detail.jpg?v=1"
    html = f'<p><img src="{src}"></p>'
    product = {"id": "8560000000000", "images": [], "description": html}
    downloaded = [{"id": "loc-detail", "filename": "localized-detail.png", "local_path": str(localized_path)}]
    bootstrap = {
        "reference_images": [{"id": "ref-detail", "filename": "reference-detail.png", "url": "https://server/ref.png"}],
        "localized_images": [{"id": "loc-detail", "filename": "localized-detail.png", "url": "https://server/loc.png"}],
    }
    confirmed_pairs: list[dict] = []
    captured_forced: list[dict] = []

    monkeypatch.setattr(run_product_cdp.settings, "load_runtime_config", lambda: {"browser_user_data_dir": "C:/chrome"})
    monkeypatch.setattr(run_product_cdp, "fetch_storefront_product", lambda *_args, **_kwargs: product)
    monkeypatch.setattr(run_product_cdp, "fetch_bootstrap_ready", lambda **_kwargs: bootstrap)
    monkeypatch.setattr(run_product_cdp, "download_localized", lambda *_args, **_kwargs: (workspace, downloaded))
    monkeypatch.setattr(run_product_cdp, "add_original_detail_fallbacks", lambda **_kwargs: [])
    monkeypatch.setattr(run_product_cdp, "fetch_storefront_image_display_sizes", lambda **_kwargs: {})
    monkeypatch.setattr(run_product_cdp, "build_detail_source_index_map", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        run_product_cdp,
        "download_visual_detail_sources",
        lambda **_kwargs: {
            "slot_images": [
                {
                    "slot_id": "detail-00",
                    "slot_index": 0,
                    "src": src,
                    "local_path": str(detail_path),
                }
            ],
            "reference_images": [
                {"id": "ref-detail", "filename": "reference-detail.png", "local_path": str(reference_path)}
            ],
        },
    )

    def fake_replace_detail_images(**kwargs):
        captured_forced.append(kwargs["forced_replacements_by_src"])
        return {
            "status": "done",
            "image_count": 1,
            "replacement_count": 1,
            "skipped_existing_count": 0,
            "skipped_missing_count": 0,
            "replacements": [{"old": src, "new": "https://cdn.shopify.com/new.png"}],
            "verify": {"expected_new_urls_present": 1, "expected_total": 1, "old_non_shopify_count": 0},
        }

    monkeypatch.setattr(run_product_cdp.taa_cdp, "replace_detail_images", fake_replace_detail_images)
    monkeypatch.setattr(run_product_cdp, "verify_storefront_body", lambda *_args, **_kwargs: {"expected_present": 1})

    args = argparse.Namespace(
        product_code="plain-detail-product-rjc",
        lang="de",
        shop_locale="de",
        language="German",
        product_id="",
        store_domain="newjoyloo.com",
        bootstrap_timeout_s=120,
        port=7777,
        carousel_limit=0,
        skip_carousel=True,
        skip_detail=False,
        skip_existing_carousel=False,
        source_index_map="",
        replace_shopify_cdn=True,
        no_preserve_detail_size=True,
        no_original_detail_fallback=True,
        no_detail_reload_verify=True,
    )

    result = run_product_cdp.run(
        args,
        visual_pair_confirm_cb=lambda pairs: confirmed_pairs.extend(pairs) or True,
    )

    assert captured_forced[0][src]["local_path"] == str(localized_path)
    assert confirmed_pairs[0]["target_kind"] == "detail"
    assert result["detail"]["visual_fallback_count"] == 1


def test_run_skips_detail_visual_fallback_for_non_auto_replace_targets(monkeypatch, tmp_path):
    workspace = run_product_cdp.storage.Workspace(
        root=tmp_path,
        source_en_dir=tmp_path / "source" / "en",
        source_localized_dir=tmp_path / "source" / "localized",
        classify_ez_dir=tmp_path / "classify" / "ez",
        classify_taa_dir=tmp_path / "classify" / "taa",
        screenshots_ez_dir=tmp_path / "screenshots" / "ez",
        screenshots_taa_dir=tmp_path / "screenshots" / "taa",
        manifest_path=tmp_path / "manifest.json",
        log_path=tmp_path / "run.log",
    )
    for path in (
        workspace.source_en_dir,
        workspace.source_localized_dir,
        workspace.classify_ez_dir,
        workspace.classify_taa_dir,
        workspace.screenshots_ez_dir,
        workspace.screenshots_taa_dir,
    ):
        path.mkdir(parents=True, exist_ok=True)

    shopify_src = "https://cdn.shopify.com/s/files/1/0000/files/plain-detail.jpg?v=1"
    gif_src = "https://cdn.example.com/files/plain-detail.gif?v=1"
    html = f'<p><img src="{shopify_src}"><img src="{gif_src}"></p>'
    product = {"id": "8560000000000", "images": [], "description": html}
    bootstrap = {
        "reference_images": [{"id": "ref-detail", "filename": "reference-detail.png", "url": "https://server/ref.png"}],
        "localized_images": [],
    }
    captured_forced: list[dict] = []

    monkeypatch.setattr(run_product_cdp.settings, "load_runtime_config", lambda: {"browser_user_data_dir": "C:/chrome"})
    monkeypatch.setattr(run_product_cdp, "fetch_storefront_product", lambda *_args, **_kwargs: product)
    monkeypatch.setattr(run_product_cdp, "fetch_bootstrap_ready", lambda **_kwargs: bootstrap)
    monkeypatch.setattr(run_product_cdp, "download_localized", lambda *_args, **_kwargs: (workspace, []))
    monkeypatch.setattr(run_product_cdp, "add_original_detail_fallbacks", lambda **_kwargs: [])
    monkeypatch.setattr(run_product_cdp, "fetch_storefront_image_display_sizes", lambda **_kwargs: {})
    monkeypatch.setattr(run_product_cdp, "build_detail_source_index_map", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        run_product_cdp,
        "download_visual_detail_sources",
        lambda **_kwargs: pytest.fail("detail visual fallback should not run for skipped targets"),
    )

    def fake_replace_detail_images(**kwargs):
        captured_forced.append(kwargs["forced_replacements_by_src"])
        return {
            "status": "skipped",
            "image_count": 2,
            "replacement_count": 0,
            "skipped_existing_count": 0,
            "skipped_missing_count": 2,
            "replacements": [],
            "verify": {"expected_new_urls_present": 0, "expected_total": 0, "old_non_shopify_count": 0},
        }

    monkeypatch.setattr(run_product_cdp.taa_cdp, "replace_detail_images", fake_replace_detail_images)

    args = argparse.Namespace(
        product_code="plain-detail-product-rjc",
        lang="de",
        shop_locale="de",
        language="German",
        product_id="",
        store_domain="newjoyloo.com",
        bootstrap_timeout_s=120,
        port=7777,
        carousel_limit=0,
        skip_carousel=True,
        skip_detail=False,
        skip_existing_carousel=False,
        source_index_map="",
        replace_shopify_cdn=False,
        no_preserve_detail_size=True,
        no_original_detail_fallback=True,
        no_detail_reload_verify=True,
    )

    result = run_product_cdp.run(args, visual_pair_confirm_cb=lambda _pairs: True)

    assert captured_forced == [{}]
    assert result["detail"]["visual_fallback_count"] == 0
    assert result["detail"]["visual_confirmation_pairs"] == []


def test_build_detail_source_index_map_prefers_detail_side_indices():
    token = "cccccccccccccccccccccccccccccccc"
    html = f'<section><img src="https://cdn.example.com/{token}.jpg"></section>'
    reference_images = [
        {"filename": f"ref_from_url_en_01_{token}.jpg"},
        {"filename": f"ref_from_url_en_12_{token}.jpg"},
    ]

    mapping = run_product_cdp.build_detail_source_index_map(
        html,
        reference_images,
        carousel_image_count=11,
    )

    assert mapping == {token: 12}


def test_detail_replacements_fall_back_to_reference_filename_when_src_has_no_hash_token():
    html = '<section><img src="https://cdn.shopify.com/files/pic1_480x480.jpg?v=1658166836"></section>'
    reference_images = [
        {"filename": "ref_from_url_en_07_pic1_480x480.jpg"},
    ]
    localized_images = [
        _localized("loc_from_url_en_07_pic1_480x480.png"),
    ]

    mapping = run_product_cdp.build_detail_source_index_map(
        html,
        reference_images,
        carousel_image_count=7,
    )
    plan = taa_cdp.plan_body_html_replacements(
        html,
        localized_images,
        source_index_by_token=mapping,
        replace_shopify_cdn=True,
    )

    assert mapping == {"name:pic1_480x480": 7}
    assert len(plan["replacements"]) == 1
    assert plan["replacements"][0]["candidate"]["local_path"] == str(Path("C:/tmp") / "loc_from_url_en_07_pic1_480x480.png")


def test_detail_replacements_accept_visual_forced_candidate_when_src_has_no_match_key():
    src = "https://cdn.shopify.com/files/plain-detail-image.jpg?v=1"
    html = f'<section><img src="{src}"></section>'
    candidate = _localized("translated-plain-detail.png")

    plan = taa_cdp.plan_body_html_replacements(
        html,
        localized_images=[],
        forced_replacements_by_src={src: candidate},
        replace_shopify_cdn=True,
    )

    assert len(plan["replacements"]) == 1
    assert plan["replacements"][0]["old"] == src
    assert plan["replacements"][0]["candidate"]["local_path"] == candidate["local_path"]
    assert plan["replacements"][0]["match_method"] == "visual"


def test_apply_uploaded_replacements_preserves_display_width():
    html = (
        '<p><img alt="demo" src="https://old.example.com/a.jpg" '
        'style="max-width: 100%; height: auto;"></p>'
    )

    updated = taa_cdp.apply_uploaded_replacements(
        html,
        [{"old": "https://old.example.com/a.jpg", "new": "https://cdn.shopify.com/a.jpg"}],
        display_size_by_src={"https://old.example.com/a.jpg": {"width": 420, "height": 315}},
    )

    assert 'src="https://cdn.shopify.com/a.jpg"' in updated
    assert "width: 420px" in updated
    assert "max-width: 100%" in updated
    assert "height: auto" in updated


def test_plan_body_html_replacements_treats_sanitized_shopify_upload_as_existing():
    token = "dddddddddddddddddddddddddddddddd"
    src = (
        "https://cdn.shopify.com/s/files/1/0727/2831/4029/files/"
        f"20260424_abcd_from_url_en_19_{token}_webp_1234.png?v=1"
    )
    html = f'<p><img src="{src}"></p>'
    localized_images = [
        _localized(f"20260424_abcd_from_url_en_19_{token}.webp.png"),
    ]

    plan = taa_cdp.plan_body_html_replacements(
        html,
        localized_images,
        replace_shopify_cdn=True,
    )

    assert plan["replacements"] == []
    assert len(plan["skipped_existing"]) == 1
    assert plan["skipped_existing"][0]["reason"] == "already localized"


def test_plan_body_html_replacements_skips_detail_image_without_server_candidate():
    token = "e91c999470fd206bac418a40a6d21c2f"
    src = f"https://cdn.example.com/from_url_en_19_{token}.png"
    html = f'<p><img src="{src}"></p>'

    plan = taa_cdp.plan_body_html_replacements(html, [])

    assert plan["replacements"] == []
    assert plan["skipped_existing"] == []
    assert len(plan["skipped_missing"]) == 1
    assert plan["skipped_missing"][0]["token"] == token
    assert plan["skipped_missing"][0]["source_index"] == 19


def test_plan_body_html_replacements_skips_detail_source_index_mismatch():
    token = "e91c999470fd206bac418a40a6d21c2f"
    src = f"https://cdn.example.com/from_url_en_19_{token}.png"
    html = f'<p><img src="{src}"></p>'
    localized_images = [
        _localized(f"loc_from_url_en_18_{token}.png"),
    ]

    plan = taa_cdp.plan_body_html_replacements(html, localized_images)

    assert plan["replacements"] == []
    assert len(plan["skipped_missing"]) == 1
    assert "source index 19" in plan["skipped_missing"][0]["reason"]


def test_plan_body_html_replacements_ignores_extra_server_candidates_not_in_html():
    token = "ffffffffffffffffffffffffffffffff"
    html = "<p>No detail images here</p>"
    localized_images = [
        _localized(f"loc_from_url_en_22_{token}.png"),
    ]

    plan = taa_cdp.plan_body_html_replacements(html, localized_images)

    assert plan["image_count"] == 0
    assert plan["replacements"] == []
    assert plan["skipped_missing"] == []


def test_taa_toolbar_detection_supports_chinese_shopify_admin_labels():
    assert "插入图片" in taa_cdp.INSERT_IMAGE_BUTTON_LABELS
    assert "保存" in taa_cdp.SAVE_BUTTON_LABELS
    assert "s-internal-icon[type=\"image\"]" in taa_cdp.build_insert_image_modal_script()


def test_wait_file_input_node_retries_until_modal_input_exists():
    class FakeCdp:
        def __init__(self):
            self.query_count = 0

        def call(self, method, params=None):
            if method == "DOM.getDocument":
                return taa_cdp.CdpResponse({"result": {"root": {"nodeId": 1}}}, [])
            if method == "DOM.querySelector":
                self.query_count += 1
                node_id = 0 if self.query_count == 1 else 42
                return taa_cdp.CdpResponse({"result": {"nodeId": node_id}}, [])
            raise AssertionError(method)

    cdp = FakeCdp()

    node_id = taa_cdp._wait_file_input_node_id(cdp, timeout_s=1, interval_s=0)

    assert node_id == 42
    assert cdp.query_count == 2


def test_ez_replace_slot_clicks_save_when_upload_input_state_is_empty(monkeypatch, tmp_path):
    image_path = tmp_path / "loc_from_url_en_00_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.png"
    image_path.write_bytes(b"image")
    calls = []

    class FakeLocator:
        def set_input_files(self, path, timeout):
            calls.append(("set_input_files", path, timeout))

    class FakePage:
        def wait_for_timeout(self, ms):
            calls.append(("wait_for_timeout", ms))

    class FakeFrame:
        page = FakePage()

        def locator(self, selector):
            assert selector == "input[type=file]"
            return FakeLocator()

    monkeypatch.setattr(ez_cdp, "_open_slot", lambda *_args, **_kwargs: {"visible_buttons": 1})
    monkeypatch.setattr(ez_cdp, "_target_exists", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(ez_cdp, "_select_language", lambda *_args, **_kwargs: {"ok": True, "value": "Dutch"})
    monkeypatch.setattr(ez_cdp, "_uploaded_file_state", lambda *_args, **_kwargs: {"ok": False, "count": 0, "names": []})
    monkeypatch.setattr(ez_cdp, "_click_save_and_wait", lambda *_args, **_kwargs: calls.append(("save",)) or {"dialog_closed": True})
    monkeypatch.setattr(ez_cdp, "_click_cancel", lambda *_args, **_kwargs: calls.append(("cancel",)) or True)

    result = ez_cdp.replace_slot(FakeFrame(), 0, str(image_path), language="Dutch")

    assert result["status"] == "ok"
    assert ("set_input_files", str(image_path), 10000) in calls
    assert ("save",) in calls
    assert calls.index(("set_input_files", str(image_path), 10000)) < calls.index(("save",))
    assert ("cancel",) not in calls


def test_fetch_bootstrap_sends_optional_shopify_product_id(monkeypatch):
    calls = []

    class DummyResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"ok": True}

    def fake_post(url, *, headers, json, timeout):
        calls.append({
            "url": url,
            "headers": headers,
            "json": json,
            "timeout": timeout,
        })
        return DummyResponse()

    monkeypatch.setattr(api_client.requests, "post", fake_post)

    payload = api_client.fetch_bootstrap(
        "http://172.30.254.14",
        "demo-key",
        "sonic-lens-refresher-rjc",
        "it",
        shopify_product_id="8559391932589",
    )

    assert payload == {"ok": True}
    assert calls[0]["json"] == {
        "product_code": "sonic-lens-refresher-rjc",
        "lang": "it",
        "shopify_product_id": "8559391932589",
    }


def test_fetch_bootstrap_ready_passes_shopify_product_id_override(monkeypatch):
    calls = []

    monkeypatch.setattr(
        run_product_cdp.settings,
        "load_runtime_config",
        lambda: {"base_url": "http://172.30.254.14", "api_key": "demo-key"},
    )

    def fake_fetch_bootstrap(base_url, api_key, product_code, lang, **kwargs):
        calls.append({
            "base_url": base_url,
            "api_key": api_key,
            "product_code": product_code,
            "lang": lang,
            **kwargs,
        })
        return {"localized_images": [{"id": 1}]}

    monkeypatch.setattr(run_product_cdp.api_client, "fetch_bootstrap", fake_fetch_bootstrap)

    payload = run_product_cdp.fetch_bootstrap_ready(
        product_code="sonic-lens-refresher-rjc",
        lang="it",
        timeout_s=1,
        shopify_product_id="8559391932589",
    )

    assert payload["localized_images"] == [{"id": 1}]
    assert calls[0]["shopify_product_id"] == "8559391932589"


def test_fetch_bootstrap_ready_honors_pre_cancelled_token(monkeypatch):
    token = cancellation.CancellationToken()
    token.cancel()
    monkeypatch.setattr(
        run_product_cdp.api_client,
        "fetch_bootstrap",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("bootstrap should not be called")),
    )

    try:
        run_product_cdp.fetch_bootstrap_ready(
            product_code="sonic-lens-refresher-rjc",
            lang="it",
            timeout_s=1,
            shopify_product_id="8559391932589",
            cancel_token=token,
        )
    except cancellation.OperationCancelled:
        pass
    else:
        raise AssertionError("expected OperationCancelled")


def test_controller_passes_gui_shopify_id_to_batch_runner(monkeypatch):
    saved_config = []
    captured_args = []
    browser_cleanups = []
    statuses = []

    monkeypatch.setattr(controller.settings, "save_runtime_config", lambda **kwargs: saved_config.append(kwargs))
    monkeypatch.setattr(
        controller.session,
        "kill_chrome_for_profile",
        lambda browser_dir: browser_cleanups.append(browser_dir),
    )

    token = cancellation.CancellationToken()

    def fake_run(args, *, cancel_token=None):
        captured_args.append(args)
        captured_args.append(cancel_token)
        return {
            "product_code": args.product_code,
            "lang": args.lang,
            "shopify_product_id": args.product_id,
            "workspace": "C:/work/demo/it",
            "carousel": {"requested": 1, "ok": 1, "skipped": 0, "results": [{"status": "ok"}]},
            "detail": {"replacement_count": 2, "skipped_existing_count": 0, "fallback_original_count": 0},
        }

    monkeypatch.setattr(controller.run_product_cdp, "run", fake_run)

    result = controller.run_shopify_localizer(
        base_url="http://172.30.254.14",
        api_key="demo-key",
        browser_user_data_dir=r"C:\chrome-shopify-image",
        product_code="sonic-lens-refresher-rjc",
        lang="it",
        shopify_product_id="8559391932589",
        cancel_token=token,
        status_cb=statuses.append,
    )

    assert result["shopify_product_id"] == "8559391932589"
    assert result["workspace_root"] == "C:/work/demo/it"
    assert captured_args[0].product_id == "8559391932589"
    assert captured_args[1] is token
    assert captured_args[0].replace_shopify_cdn is True
    assert captured_args[0].no_preserve_detail_size is False
    assert saved_config[0]["base_url"] == "http://172.30.254.14"
    assert browser_cleanups == [r"C:\chrome-shopify-image"]
    assert any("开始连续替换流程" in message for message in statuses)


def test_controller_backfills_resolved_shopify_id_before_batch_runner(monkeypatch):
    backfilled_ids = []
    captured_args = []

    monkeypatch.setattr(controller.settings, "save_runtime_config", lambda **kwargs: None)
    monkeypatch.setattr(controller.session, "kill_chrome_for_profile", lambda browser_dir: None)
    monkeypatch.setattr(
        controller,
        "resolve_shopify_product_id",
        lambda **kwargs: "8559445180589",
    )

    def fake_run(args, *, cancel_token=None):
        captured_args.append(args)
        return {
            "product_code": args.product_code,
            "lang": args.lang,
            "shopify_product_id": args.product_id,
            "workspace": "C:/work/demo/de",
            "carousel": {"requested": 1, "ok": 1, "skipped": 0, "results": [{"status": "ok"}]},
            "detail": {"replacement_count": 0, "skipped_existing_count": 0, "fallback_original_count": 0},
        }

    monkeypatch.setattr(controller.run_product_cdp, "run", fake_run)

    result = controller.run_shopify_localizer(
        base_url="http://172.30.254.14",
        api_key="demo-key",
        browser_user_data_dir=r"C:\chrome-shopify-image",
        product_code="dual-auto-fuse-tester-puller-rjc",
        lang="de",
        shopify_product_id="",
        shopify_product_id_cb=backfilled_ids.append,
    )

    assert backfilled_ids == ["8559445180589"]
    assert captured_args[0].product_id == "8559445180589"
    assert result["shopify_product_id"] == "8559445180589"


def test_controller_maps_locale_code_to_ez_language_label():
    args = controller._build_batch_args(
        product_code="sonic-lens-refresher-rjc",
        lang="nl",
        shopify_product_id="8559391932589",
    )

    assert args.language == "Dutch"


def test_controller_prefers_api_shopify_language_name_over_static_fallback():
    args = controller._build_batch_args(
        product_code="sonic-lens-refresher-rjc",
        lang="nl",
        shopify_product_id="8559391932589",
        shopify_language_name="Nederlands",
    )

    assert args.language == "Nederlands"


def test_controller_separates_storefront_locale_from_translate_and_adapt_locale():
    args = controller._build_batch_args(
        product_code="sonic-lens-refresher-rjc",
        lang="pt",
        shopify_product_id="8559391932589",
    )

    assert args.shop_locale == "pt"
    assert args.taa_shop_locale == "pt-PT"


def test_translate_and_adapt_url_maps_portuguese_to_shopify_region_locale():
    url = session.build_translate_url("8559391932589", "pt")

    assert "shopLocale=pt-PT" in url
    assert "shopLocale=pt-pt" not in url


def test_verify_target_language_marks_all_expected_slots():
    from tools.shopify_image_localizer.rpa import ez_cdp

    class FakeFrame:
        def evaluate(self, script, arg=None):
            return [
                {"slot": 0, "languages": ["Italian"]},
                {"slot": 1, "languages": ["Italian", "Spanish"]},
            ]

    result = ez_cdp.verify_target_language_markers(FakeFrame(), [0, 1], "Italian")

    assert result["ok"] is True
    assert result["expected"] == 2
    assert result["matched"] == 2


def test_ez_filters_out_slots_that_already_have_language_marker():
    from tools.shopify_image_localizer.rpa import ez_cdp

    class FakeFrame:
        def evaluate(self, script, arg=None):
            return [
                {"slot": 0, "text": "Remove German", "languages": ["Remove German"]},
                {"slot": 1, "text": "English", "languages": ["English"]},
                {"slot": 2, "text": "German", "languages": []},
            ]

    skipped, missing_pairs = ez_cdp.filter_pairs_missing_language_markers(
        FakeFrame(),
        [(0, "C:/tmp/a.jpg"), (1, "C:/tmp/b.jpg"), (2, "C:/tmp/c.jpg")],
        "German",
    )

    assert [row["slot"] for row in skipped] == [0, 2]
    assert [row["status"] for row in skipped] == ["skipped", "skipped"]
    assert missing_pairs == [(1, "C:/tmp/b.jpg")]


def test_ez_replace_many_skips_slots_that_already_have_language_marker(monkeypatch, capsys):
    from tools.shopify_image_localizer.rpa import ez_cdp

    calls = []

    class FakePage:
        def goto(self, url, wait_until=None, timeout=None):
            calls.append(("goto", url))

        def close(self):
            calls.append(("page_close",))

    class FakeContext:
        def __init__(self):
            self.page = FakePage()

        def set_default_timeout(self, timeout):
            calls.append(("timeout", timeout))

        def new_page(self):
            calls.append(("new_page",))
            return self.page

    class FakeBrowser:
        def __init__(self):
            self.contexts = [FakeContext()]

        def close(self):
            calls.append(("browser_close",))

    class FakeChromium:
        def connect_over_cdp(self, endpoint):
            calls.append(("connect", endpoint))
            return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(ez_cdp, "ensure_cdp_chrome", lambda *args, **kwargs: calls.append(("ensure",)))
    monkeypatch.setattr(ez_cdp, "_cdp_ws_endpoint", lambda port: "ws://example.test")
    monkeypatch.setattr(ez_cdp, "sync_playwright", lambda: FakePlaywright())
    monkeypatch.setattr(ez_cdp, "_wait_plugin_frame", lambda page, **kwargs: object())
    monkeypatch.setattr(
        ez_cdp,
        "filter_pairs_missing_language_markers",
        lambda frame, pairs, language: (
            [{"slot": 0, "status": "skipped", "reason": f"{language} already exists", "path": "C:/tmp/a.jpg"}],
            [(1, "C:/tmp/b.jpg")],
        ),
    )

    def fake_replace_slot(frame, slot_idx, path, **kwargs):
        calls.append(("replace_slot", slot_idx, path, kwargs["language"]))
        return {"slot": slot_idx, "status": "ok", "path": path}

    monkeypatch.setattr(ez_cdp, "replace_slot", fake_replace_slot)

    result = ez_cdp.replace_many(
        ez_url="https://admin.shopify.com/store/0ixug9-pv/apps/ez-product-image-translate/product/8559445180589",
        user_data_dir=r"C:\chrome-shopify-image",
        pairs=[(0, "C:/tmp/a.jpg"), (1, "C:/tmp/b.jpg")],
        language="German",
    )

    assert result == [
        {"slot": 0, "status": "skipped", "reason": "German already exists", "path": "C:/tmp/a.jpg"},
        {"slot": 1, "status": "ok", "path": "C:/tmp/b.jpg"},
    ]
    assert ("replace_slot", 0, "C:/tmp/a.jpg", "German") not in calls
    assert ("replace_slot", 1, "C:/tmp/b.jpg", "German") in calls
    output = capsys.readouterr().out
    assert "[carousel] START open EZ page" in output
    assert "[carousel] END scan existing language markers: ok skipped=1 pending=1" in output
    assert "[carousel] RESULT done requested=2 ok=1 skipped=1 failed=0" in output


def test_ez_replace_slot_does_not_remove_existing_language_marker(monkeypatch):
    from tools.shopify_image_localizer.rpa import ez_cdp

    calls = []

    class FakePage:
        def wait_for_timeout(self, ms):
            calls.append(("wait_for_timeout", ms))

    class FakeLocator:
        def __init__(self, selector: str):
            self.selector = selector

        def count(self):
            return 1

        def nth(self, index):
            calls.append(("nth", self.selector, index))
            return self

        def click(self, timeout=None):
            calls.append(("click", self.selector))

        def wait_for(self, state=None, timeout=None):
            calls.append(("wait_for", self.selector, state))

        def inner_text(self, timeout=None):
            return "translation for: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa."

    class FakeFrame:
        page = FakePage()

        def locator(self, selector):
            calls.append(("locator", selector))
            return FakeLocator(selector)

    monkeypatch.setattr(ez_cdp, "_target_exists", lambda frame, language: True)

    result = ez_cdp.replace_slot(FakeFrame(), 0, "C:/tmp/loc_from_url_de_00_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.jpg", language="German")

    assert result == {"slot": 0, "status": "skipped", "reason": "German already exists"}
    assert ("click", 'button[aria-label="Remove German"]') not in calls


def test_ez_replace_slot_logs_timed_steps_and_waits_between_actions(monkeypatch, capsys):
    from tools.shopify_image_localizer.rpa import ez_cdp

    calls = []

    class FakePage:
        def wait_for_timeout(self, ms):
            calls.append(("wait_for_timeout", ms))

    class FakeLocator:
        def __init__(self, selector: str):
            self.selector = selector

        def count(self):
            return 1

        def nth(self, index):
            calls.append(("nth", self.selector, index))
            return self

        def click(self, timeout=None):
            calls.append(("click", self.selector, timeout))

        def wait_for(self, state=None, timeout=None):
            calls.append(("wait_for", self.selector, state, timeout))

        def inner_text(self, timeout=None):
            return "translation for: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa."

        def set_input_files(self, path, timeout=None):
            calls.append(("set_input_files", path, timeout))

    class FakeFrame:
        page = FakePage()

        def locator(self, selector):
            calls.append(("locator", selector))
            return FakeLocator(selector)

        def evaluate(self, script, arg=None):
            if "const wanted" in script:
                return {"ok": True, "value": "de"}
            if "input.files" in script:
                return {"ok": True, "count": 1, "names": ["loc_from_url_de_00_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.jpg"]}
            raise AssertionError(script)

    monkeypatch.setattr(ez_cdp, "_target_exists", lambda frame, language: False)

    result = ez_cdp.replace_slot(
        FakeFrame(),
        0,
        "C:/tmp/loc_from_url_de_00_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.jpg",
        language="German",
    )

    output = capsys.readouterr().out
    assert result["status"] == "ok"
    assert "[carousel][slot 0] START open translation dialog" in output
    assert "[carousel][slot 0] END set upload file: ok" in output
    assert "selected_files=loc_from_url_de_00_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.jpg" in output
    assert "[carousel][slot 0] RESULT ok" in output
    assert ("wait_for_timeout", 1000) in calls


def test_ensure_cdp_chrome_clears_profile_browser_before_starting_port(monkeypatch):
    from tools.shopify_image_localizer.rpa import ez_cdp

    calls = []
    alive_results = [False, True]

    def fake_cdp_alive(port):
        calls.append(("alive", port))
        return alive_results.pop(0)

    monkeypatch.setattr(ez_cdp, "_cdp_alive", fake_cdp_alive)
    monkeypatch.setattr(ez_cdp, "_chrome_exe", lambda: "chrome.exe")
    monkeypatch.setattr(ez_cdp.session, "detect_system_proxy", lambda: None)
    monkeypatch.setattr(
        ez_cdp.session,
        "kill_chrome_for_profile",
        lambda user_data_dir: calls.append(("kill", user_data_dir)),
    )
    monkeypatch.setattr(
        ez_cdp.subprocess,
        "Popen",
        lambda args, **kwargs: calls.append(("popen", args)) or object(),
    )

    started = ez_cdp.ensure_cdp_chrome(
        r"C:\chrome-shopify-image",
        "https://admin.shopify.com/store/0ixug9-pv/apps/ez-product-image-translate/product/8559445180589",
        port=7777,
        startup_timeout_s=1,
    )

    assert started is True
    assert calls.index(("kill", r"C:\chrome-shopify-image")) < next(
        idx for idx, call in enumerate(calls) if call[0] == "popen"
    )


def test_wait_plugin_frame_pumps_playwright_page_events(monkeypatch):
    from tools.shopify_image_localizer.rpa import ez_cdp

    calls = []

    class FakeLocator:
        def count(self):
            return 1

    class FakeFrame:
        url = "https://translate.freshify.click/demo"

        def locator(self, selector):
            calls.append(("locator", selector))
            return FakeLocator()

    class FakePage:
        def __init__(self):
            self.frames = []

        def wait_for_timeout(self, ms):
            calls.append(("wait_for_timeout", ms))
            self.frames = [FakeFrame()]

    page = FakePage()

    monkeypatch.setattr(
        ez_cdp.cancellation,
        "cancellable_sleep",
        lambda token, seconds: (_ for _ in ()).throw(AssertionError("page waits must use Playwright")),
    )

    frame = ez_cdp._wait_plugin_frame(page, timeout_s=1)

    assert frame is page.frames[0]
    assert ("wait_for_timeout", 500) in calls
