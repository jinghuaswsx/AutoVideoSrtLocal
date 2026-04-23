from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Workspace:
    root: Path
    source_en_dir: Path
    source_localized_dir: Path
    classify_ez_dir: Path
    classify_taa_dir: Path
    screenshots_ez_dir: Path
    screenshots_taa_dir: Path
    manifest_path: Path
    log_path: Path


def executable_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def create_workspace(product_code: str, lang: str) -> Workspace:
    normalized_code = str(product_code or "").strip()
    normalized_lang = str(lang or "").strip().lower()
    root = executable_root() / normalized_code / normalized_lang

    source_en_dir = root / "source" / "en"
    source_localized_dir = root / "source" / "localized"
    classify_ez_dir = root / "classify" / "ez"
    classify_taa_dir = root / "classify" / "taa"
    screenshots_ez_dir = root / "screenshots" / "ez"
    screenshots_taa_dir = root / "screenshots" / "taa"

    for path in (
        source_en_dir,
        source_localized_dir,
        classify_ez_dir,
        classify_taa_dir,
        screenshots_ez_dir,
        screenshots_taa_dir,
    ):
        path.mkdir(parents=True, exist_ok=True)

    return Workspace(
        root=root,
        source_en_dir=source_en_dir,
        source_localized_dir=source_localized_dir,
        classify_ez_dir=classify_ez_dir,
        classify_taa_dir=classify_taa_dir,
        screenshots_ez_dir=screenshots_ez_dir,
        screenshots_taa_dir=screenshots_taa_dir,
        manifest_path=root / "manifest.json",
        log_path=root / "run.log",
    )


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def append_log(path: Path, message: str) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(f"{message}\n")
