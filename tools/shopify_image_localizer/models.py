from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LanguageOption:
    code: str
    name_zh: str
    shop_locale: str
    folder_code: str
    label: str


@dataclass(frozen=True)
class DownloadedImage:
    id: str
    kind: str
    filename: str
    url: str
    local_path: str
