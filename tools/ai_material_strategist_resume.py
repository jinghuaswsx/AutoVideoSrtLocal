"""Resume an AI素材军师 project from persisted checkpoints.

Docs anchor:
docs/superpowers/specs/2026-06-09-ai-material-strategist-project-design.md#断点续跑与恢复
"""
from __future__ import annotations

import argparse
import json

from appcore import ai_material_strategist


def main() -> int:
    parser = argparse.ArgumentParser(description="Resume an AI素材军师 project")
    parser.add_argument("project_id", type=int, help="ai_material_strategist_projects.id")
    parser.add_argument("--user-id", type=int, default=None)
    parser.add_argument("--no-ai", action="store_true", help="Use deterministic fallback without LLM calls")
    args = parser.parse_args()

    project = ai_material_strategist.run_project(
        args.project_id,
        user_id=args.user_id,
        run_ai=not args.no_ai,
    )
    print(json.dumps({
        "id": project.get("id"),
        "status": project.get("status"),
        "project_name": project.get("project_name"),
        "top_product_count": len(project.get("products") or []),
        "summary": project.get("summary") or {},
        "error_message": project.get("error_message") or "",
    }, ensure_ascii=False, indent=2))
    return 0 if project.get("status") == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
