"""Archive builders for media product detail images."""

from __future__ import annotations

import io
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

from flask import send_file


@dataclass(frozen=True)
class DetailImagesZipGroup:
    folder: str
    rows: Sequence[Mapping[str, object]]


@dataclass(frozen=True)
class DetailImagesArchive:
    archive_base: str
    buffer: io.BytesIO


@dataclass(frozen=True)
class DetailImagesArchiveResponse:
    archive: DetailImagesArchive | None = None
    audit_action: str | None = None
    audit_detail: dict | None = None
    error: str | None = None
    status_code: int = 200
    not_found: bool = False


def build_detail_images_archive(
    *,
    archive_base: str,
    groups: Sequence[DetailImagesZipGroup],
    download_media_object: Callable[[str, str], object],
    temp_prefix: str = "detail_images_zip_",
) -> DetailImagesArchive:
    buf = io.BytesIO()
    with tempfile.TemporaryDirectory(prefix=temp_prefix) as tmp_dir:
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for group in groups:
                for idx, row in enumerate(group.rows, start=1):
                    object_key = str(row.get("object_key") or "").strip()
                    if not object_key:
                        continue
                    suffix = Path(object_key).suffix or ".jpg"
                    local_path = Path(tmp_dir) / f"{uuid.uuid4().hex}{suffix}"
                    download_media_object(object_key, str(local_path))
                    zf.write(local_path, arcname=f"{group.folder}/{idx:02d}{suffix}")
    buf.seek(0)
    return DetailImagesArchive(archive_base=archive_base, buffer=buf)


def build_detail_images_zip_response(
    product_id: int,
    product: Mapping[str, object],
    lang: str,
    kind: str,
    *,
    is_valid_language_fn: Callable[[str], bool],
    list_detail_images_fn: Callable[[int, str], Sequence[Mapping[str, object]]],
    detail_images_is_gif_fn: Callable[[Mapping[str, object]], bool],
    archive_basename_fn: Callable[[Mapping[str, object], int, str], str],
    download_media_object_fn: Callable[[str, str], object],
) -> DetailImagesArchiveResponse:
    lang = (lang or "en").strip().lower()
    if not is_valid_language_fn(lang):
        return DetailImagesArchiveResponse(error=f"unsupported language: {lang}", status_code=400)

    kind = (kind or "image").strip().lower()
    if kind not in {"image", "gif", "all"}:
        return DetailImagesArchiveResponse(error=f"unsupported kind: {kind}", status_code=400)

    rows = list(list_detail_images_fn(product_id, lang))
    if not rows:
        return DetailImagesArchiveResponse(not_found=True, status_code=404)

    if kind == "gif":
        rows = [row for row in rows if detail_images_is_gif_fn(row)]
    elif kind == "image":
        rows = [row for row in rows if not detail_images_is_gif_fn(row)]
    if not rows:
        return DetailImagesArchiveResponse(not_found=True, status_code=404)

    base = archive_basename_fn(product or {}, product_id, lang)
    archive_base = f"{base}_gif" if kind == "gif" else base
    archive = build_detail_images_archive(
        archive_base=archive_base,
        groups=[DetailImagesZipGroup(folder=archive_base, rows=rows)],
        download_media_object=download_media_object_fn,
    )
    return DetailImagesArchiveResponse(
        archive=archive,
        audit_action="detail_images_zip_download",
        audit_detail={
            "lang": lang,
            "kind": kind,
            "file_count": len(rows),
            "object_keys": [_object_key(row) for row in rows],
        },
    )


def build_localized_detail_images_zip_response(
    product_id: int,
    product: Mapping[str, object],
    *,
    list_languages_fn: Callable[[], Sequence[Mapping[str, object]]],
    list_detail_images_fn: Callable[[int, str], Sequence[Mapping[str, object]]],
    detail_images_is_gif_fn: Callable[[Mapping[str, object]], bool],
    archive_product_code_fn: Callable[[Mapping[str, object], int], str],
    archive_part_fn: Callable[[object, str], str],
    download_media_object_fn: Callable[[str, str], object],
) -> DetailImagesArchiveResponse:
    product_code = archive_product_code_fn(product or {}, product_id)
    archive_base = f"\u5c0f\u8bed\u79cd-{product_code}"
    groups: list[tuple[str, str, list[Mapping[str, object]]]] = []
    for lang_row in list_languages_fn():
        lang = str(lang_row.get("code") or "").strip().lower()
        if not lang or lang == "en":
            continue
        rows = [
            row
            for row in list_detail_images_fn(product_id, lang)
            if _object_key(row) and not detail_images_is_gif_fn(row)
        ]
        if not rows:
            continue
        lang_name = archive_part_fn(lang_row.get("name_zh"), lang)
        groups.append((lang, f"{lang_name}-{product_code}", rows))

    if not groups:
        return DetailImagesArchiveResponse(not_found=True, status_code=404)

    archive = build_detail_images_archive(
        archive_base=archive_base,
        groups=[
            DetailImagesZipGroup(folder=folder, rows=rows)
            for _lang, folder, rows in groups
        ],
        download_media_object=download_media_object_fn,
        temp_prefix="localized_detail_images_zip_",
    )
    return DetailImagesArchiveResponse(
        archive=archive,
        audit_action="localized_detail_images_zip_download",
        audit_detail={
            "languages": [lang for lang, _folder, _rows in groups],
            "file_count": sum(len(rows) for _lang, _folder, rows in groups),
            "object_keys": [
                _object_key(row)
                for _lang, _folder, rows in groups
                for row in rows
            ],
        },
    )


def detail_images_zip_flask_response(result: DetailImagesArchiveResponse):
    archive = result.archive
    if archive is None:
        return ("", result.status_code)
    return send_file(
        archive.buffer,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"{archive.archive_base}.zip",
    )


def _object_key(row: Mapping[str, object]) -> str:
    return str(row.get("object_key") or "").strip()
