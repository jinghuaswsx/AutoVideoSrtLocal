"""Archive builders for media product detail images."""

from __future__ import annotations

import io
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence


@dataclass(frozen=True)
class DetailImagesZipGroup:
    folder: str
    rows: Sequence[Mapping[str, object]]


@dataclass(frozen=True)
class DetailImagesArchive:
    archive_base: str
    buffer: io.BytesIO


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
