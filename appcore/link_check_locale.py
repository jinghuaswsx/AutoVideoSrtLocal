from __future__ import annotations

from urllib.parse import urlparse


def _path_segments(link_url: str) -> list[str]:
    path = urlparse(link_url).path or ""
    return [segment.strip().lower() for segment in path.split("/") if segment.strip()]


def detect_target_language_from_url(link_url: str, enabled_codes: set[str]) -> str:
    for segment in _path_segments(link_url):
        if segment in enabled_codes:
            return segment
        if "-" in segment:
            primary = segment.split("-", 1)[0]
            if primary in enabled_codes:
                return primary
    return ""


def build_link_check_display_name(link_url: str, target_language: str) -> str:
    segments = _path_segments(link_url)
    handle = ""
    if "products" in segments:
        index = segments.index("products")
        if index + 1 < len(segments):
            handle = segments[index + 1]
    base = handle or urlparse(link_url).netloc or "link-check"
    suffix = (target_language or "").upper()
    return f"{base[:40]} · {suffix}" if suffix else base[:40]
