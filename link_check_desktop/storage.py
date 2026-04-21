from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Workspace:
    root: Path
    reference_dir: Path
    site_dir: Path
    compare_dir: Path


def executable_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def create_workspace(product_id: int, *, now: datetime | None = None) -> Workspace:
    current = now or datetime.now()
    root = executable_root() / "img" / f"{product_id}-{current:%Y%m%d%H%M%S}"
    reference_dir = root / "reference"
    site_dir = root / "site"
    compare_dir = root / "compare"

    reference_dir.mkdir(parents=True, exist_ok=False)
    site_dir.mkdir(parents=True, exist_ok=True)
    compare_dir.mkdir(parents=True, exist_ok=True)

    return Workspace(
        root=root,
        reference_dir=reference_dir,
        site_dir=site_dir,
        compare_dir=compare_dir,
    )


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
