from __future__ import annotations

"""Release metadata for the Dianxiaomi procurement insights Chrome extension.

Docs-anchor:
docs/superpowers/specs/2026-06-09-chrome-extension-tool-release-standard.md
"""

from appcore import tool_release_info


SETTING_KEY = "dianxiaomi_procurement_insights_extension_release"


def get_release_info() -> dict[str, str]:
    return tool_release_info.get_release_info(SETTING_KEY)


def set_release_info(
    *,
    version: str,
    released_at: str,
    download_url: str,
    release_note: str = "",
    filename: str = "",
) -> dict[str, str]:
    return tool_release_info.set_release_info(
        SETTING_KEY,
        version=version,
        released_at=released_at,
        download_url=download_url,
        release_note=release_note,
        filename=filename,
    )
