from __future__ import annotations

from pathlib import Path
import re


def test_pyinstaller_spec_bundles_visual_fallback_dependencies() -> None:
    spec_path = (
        Path(__file__).resolve().parents[1]
        / "tools"
        / "shopify_image_localizer"
        / "packaging"
        / "shopify_image_localizer.spec"
    )
    spec = spec_path.read_text(encoding="utf-8")

    assert '"link_check_desktop.image_compare"' in spec
    assert 'collect_submodules("link_check_desktop")' not in spec
    excludes_match = re.search(r"excludes=\[(.*?)\]", spec, re.S)
    assert excludes_match is not None
    excludes = excludes_match.group(1)
    assert '"link_check_desktop"' not in excludes
    assert '"skimage"' not in excludes
    assert '"scipy"' not in excludes
