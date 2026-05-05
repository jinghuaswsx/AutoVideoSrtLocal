from __future__ import annotations

import io
import zipfile


def test_build_detail_images_zip_response_filters_gif_and_builds_audit_detail(tmp_path):
    from web.services.media_detail_archives import build_detail_images_zip_response

    rows = [
        {"id": 1, "object_key": "1/medias/1/a.jpg"},
        {"id": 2, "object_key": "1/medias/1/b.gif"},
    ]

    def fake_download(object_key, local_path):
        del local_path
        target = tmp_path / object_key.replace("/", "_")
        target.write_bytes(b"BYTES-" + object_key.encode())
        return target

    def archive_download(object_key, local_path):
        data = b"BYTES-" + object_key.encode()
        with open(local_path, "wb") as fh:
            fh.write(data)

    result = build_detail_images_zip_response(
        123,
        {"product_code": "demo"},
        "en",
        "gif",
        is_valid_language_fn=lambda lang: lang == "en",
        list_detail_images_fn=lambda pid, lang: rows,
        detail_images_is_gif_fn=lambda row: str(row.get("object_key") or "").endswith(".gif"),
        archive_basename_fn=lambda product, pid, lang: "demo_en_detail-images",
        download_media_object_fn=archive_download,
    )

    assert result.status_code == 200
    assert result.not_found is False
    assert result.audit_action == "detail_images_zip_download"
    assert result.audit_detail == {
        "lang": "en",
        "kind": "gif",
        "file_count": 1,
        "object_keys": ["1/medias/1/b.gif"],
    }
    assert result.archive is not None
    assert result.archive.archive_base == "demo_en_detail-images_gif"
    archive = zipfile.ZipFile(io.BytesIO(result.archive.buffer.getvalue()))
    assert archive.namelist() == ["demo_en_detail-images_gif/01.gif"]
    assert archive.read("demo_en_detail-images_gif/01.gif") == b"BYTES-1/medias/1/b.gif"


def test_build_detail_images_zip_response_rejects_invalid_kind_before_listing():
    from web.services.media_detail_archives import build_detail_images_zip_response

    result = build_detail_images_zip_response(
        123,
        {"product_code": "demo"},
        "en",
        "video",
        is_valid_language_fn=lambda lang: True,
        list_detail_images_fn=lambda pid, lang: (_ for _ in ()).throw(AssertionError("list not reached")),
        detail_images_is_gif_fn=lambda row: False,
        archive_basename_fn=lambda product, pid, lang: "demo",
        download_media_object_fn=lambda object_key, local_path: None,
    )

    assert result.status_code == 400
    assert result.error == "涓嶆敮鎸佺殑 kind: video"
    assert result.archive is None


def test_build_detail_images_zip_response_marks_empty_filter_as_not_found():
    from web.services.media_detail_archives import build_detail_images_zip_response

    result = build_detail_images_zip_response(
        123,
        {"product_code": "demo"},
        "en",
        "gif",
        is_valid_language_fn=lambda lang: True,
        list_detail_images_fn=lambda pid, lang: [{"id": 1, "object_key": "1/medias/1/a.jpg"}],
        detail_images_is_gif_fn=lambda row: False,
        archive_basename_fn=lambda product, pid, lang: "demo",
        download_media_object_fn=lambda object_key, local_path: None,
    )

    assert result.not_found is True
    assert result.archive is None


def test_build_localized_detail_images_zip_response_groups_static_images_by_language():
    from web.services.media_detail_archives import build_localized_detail_images_zip_response

    rows_by_lang = {
        "en": [{"id": 10, "object_key": "1/medias/1/en.jpg"}],
        "de": [
            {"id": 21, "object_key": "1/medias/1/de-a.jpg"},
            {"id": 22, "object_key": "1/medias/1/de-b.gif"},
            {"id": 23, "object_key": "1/medias/1/de-c.webp"},
        ],
        "fr": [{"id": 31, "object_key": "1/medias/1/fr-a.png"}],
    }

    def fake_download(object_key, local_path):
        with open(local_path, "wb") as fh:
            fh.write(b"BYTES-" + object_key.encode())

    result = build_localized_detail_images_zip_response(
        123,
        {"product_code": "digital-lint-shaver"},
        list_languages_fn=lambda: [
            {"code": "en", "name_zh": "英语"},
            {"code": "de", "name_zh": "德语"},
            {"code": "fr", "name_zh": "法语"},
        ],
        list_detail_images_fn=lambda pid, lang: rows_by_lang.get(lang, []),
        detail_images_is_gif_fn=lambda row: str(row.get("object_key") or "").endswith(".gif"),
        archive_product_code_fn=lambda product, pid: "digital-lint-shaver",
        archive_part_fn=lambda value, fallback: str(value or fallback),
        download_media_object_fn=fake_download,
    )

    assert result.status_code == 200
    assert result.not_found is False
    assert result.audit_action == "localized_detail_images_zip_download"
    assert result.audit_detail == {
        "languages": ["de", "fr"],
        "file_count": 3,
        "object_keys": [
            "1/medias/1/de-a.jpg",
            "1/medias/1/de-c.webp",
            "1/medias/1/fr-a.png",
        ],
    }
    assert result.archive is not None
    assert result.archive.archive_base == "小语种-digital-lint-shaver"
    archive = zipfile.ZipFile(io.BytesIO(result.archive.buffer.getvalue()))
    assert archive.namelist() == [
        "德语-digital-lint-shaver/01.jpg",
        "德语-digital-lint-shaver/02.webp",
        "法语-digital-lint-shaver/01.png",
    ]


def test_build_localized_detail_images_zip_response_marks_no_static_images_not_found():
    from web.services.media_detail_archives import build_localized_detail_images_zip_response

    result = build_localized_detail_images_zip_response(
        123,
        {"product_code": "digital-lint-shaver"},
        list_languages_fn=lambda: [{"code": "en"}, {"code": "de", "name_zh": "德语"}],
        list_detail_images_fn=lambda pid, lang: [{"id": 22, "object_key": "1/medias/1/de-b.gif"}],
        detail_images_is_gif_fn=lambda row: True,
        archive_product_code_fn=lambda product, pid: "digital-lint-shaver",
        archive_part_fn=lambda value, fallback: str(value or fallback),
        download_media_object_fn=lambda object_key, local_path: None,
    )

    assert result.not_found is True
    assert result.archive is None
