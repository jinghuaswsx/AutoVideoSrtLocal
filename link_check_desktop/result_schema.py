from __future__ import annotations

from datetime import datetime
from typing import Any


def build_task_manifest(target_url: str, bootstrap: dict[str, Any], workspace) -> dict[str, Any]:
    return {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "target_url": target_url,
        "normalized_url": bootstrap.get("normalized_url") or target_url,
        "matched_by": bootstrap.get("matched_by") or "",
        "product": dict(bootstrap.get("product") or {}),
        "target_language": bootstrap.get("target_language") or "",
        "target_language_name": bootstrap.get("target_language_name") or "",
        "reference_images": list(bootstrap.get("reference_images") or []),
        "workspace": {
            "root": str(workspace.root),
            "reference_dir": str(workspace.reference_dir),
            "site_dir": str(workspace.site_dir),
            "compare_dir": str(workspace.compare_dir),
        },
    }
