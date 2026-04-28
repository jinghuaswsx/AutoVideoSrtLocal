"""Refresh translated video item covers from generated cover translations.

This is a data backfill helper for historical bulk-translate tasks. It only
copies covers from media_raw_source_translations to matching translated
media_items rows; it never derives covers from video thumbnails.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from appcore.bulk_translate_backfill import refresh_all_translated_video_item_covers


def main() -> int:
    updated = refresh_all_translated_video_item_covers()
    print(f"updated_video_item_covers={updated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
