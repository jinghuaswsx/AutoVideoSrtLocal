"""Clear duplicate product asset payloads from Dianxiaomi ranking snapshot rows.

Docs-anchor: docs/superpowers/specs/2026-05-19-mingkong-product-assets-dedup-top500-design.md
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from appcore.db import execute, query
from tools.dianxiaomi_listing_ranking_sync import guard_against_windows_local_mysql


LEGACY_ASSET_COLUMNS = (
    "product_main_image_url",
    "product_main_image_object_key",
    "product_detail_images_json",
    "product_assets_error",
    "product_cn_name",
    "mk_first_material_name",
    "mk_first_material_path",
    "mk_first_material_url",
    "mk_material_error",
    "product_assets_synced_at",
)


def _legacy_payload_predicate() -> str:
    return " OR ".join(f"{column} IS NOT NULL" for column in LEGACY_ASSET_COLUMNS)


def count_legacy_asset_rows(*, query_fn: Callable[[str, tuple], list[dict]] = query) -> int:
    rows = query_fn(
        f"""
        SELECT COUNT(*) AS cnt
        FROM dianxiaomi_rankings
        WHERE {_legacy_payload_predicate()}
        """,
        (),
    )
    return int((rows[0] if rows else {}).get("cnt") or 0)


def count_rankings_over_limit(
    *,
    rank_limit: int,
    query_fn: Callable[[str, tuple], list[dict]] = query,
) -> int:
    rows = query_fn(
        """
        SELECT COUNT(*) AS cnt
        FROM dianxiaomi_rankings
        WHERE rank_position > %s
        """,
        (int(rank_limit),),
    )
    return int((rows[0] if rows else {}).get("cnt") or 0)


def clear_legacy_asset_columns(*, execute_fn: Callable[[str, tuple], int] = execute) -> int:
    set_clause = ",\n            ".join(f"{column}=NULL" for column in LEGACY_ASSET_COLUMNS)
    return int(
        execute_fn(
            f"""
            UPDATE dianxiaomi_rankings
            SET {set_clause}
            WHERE {_legacy_payload_predicate()}
            """,
            (),
        )
        or 0
    )


def prune_rankings_over_limit(
    *,
    rank_limit: int,
    execute_fn: Callable[[str, tuple], int] = execute,
) -> int:
    return int(
        execute_fn(
            """
            DELETE FROM dianxiaomi_rankings
            WHERE rank_position > %s
            """,
            (int(rank_limit),),
        )
        or 0
    )


def run_compaction(
    *,
    dry_run: bool = True,
    prune_rankings: bool = False,
    rank_limit: int = 500,
    optimize_table: bool = False,
    query_fn: Callable[[str, tuple], list[dict]] = query,
    execute_fn: Callable[[str, tuple], int] = execute,
) -> dict[str, Any]:
    legacy_rows = count_legacy_asset_rows(query_fn=query_fn)
    rows_over_limit = count_rankings_over_limit(rank_limit=rank_limit, query_fn=query_fn)
    summary = {
        "dry_run": bool(dry_run),
        "legacy_rows_with_asset_payload": legacy_rows,
        "rank_limit": int(rank_limit),
        "ranking_rows_over_limit": rows_over_limit,
        "ranking_rows_compacted": 0,
        "ranking_rows_pruned": 0,
        "optimized_table": False,
    }
    if dry_run:
        return summary
    summary["ranking_rows_compacted"] = clear_legacy_asset_columns(execute_fn=execute_fn)
    if prune_rankings:
        summary["ranking_rows_pruned"] = prune_rankings_over_limit(
            rank_limit=rank_limit,
            execute_fn=execute_fn,
        )
    if optimize_table:
        execute_fn("OPTIMIZE TABLE dianxiaomi_rankings", ())
        summary["optimized_table"] = True
    return summary


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compact duplicate Dianxiaomi ranking product asset columns.")
    parser.add_argument("--apply", action="store_true", help="Actually clear legacy asset columns. Default is dry-run.")
    parser.add_argument("--prune-rankings", action="store_true", help="Delete historical ranking rows above --rank-limit.")
    parser.add_argument("--rank-limit", type=int, default=500)
    parser.add_argument("--optimize-table", action="store_true", help="Run OPTIMIZE TABLE after clearing payload columns.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    guard_against_windows_local_mysql()
    summary = run_compaction(
        dry_run=not args.apply,
        prune_rankings=bool(args.prune_rankings),
        rank_limit=int(args.rank_limit),
        optimize_table=bool(args.optimize_table),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
