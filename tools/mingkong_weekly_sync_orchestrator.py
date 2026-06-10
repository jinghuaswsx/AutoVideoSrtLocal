"""Paced weekly orchestration for Mingkong SKU backfill stages."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools import mingkong_unprocessed_sku_backfill as backfill


OUTPUT_DIR = REPO_ROOT / "output" / "mingkong_weekly_sync"


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _sleep_seconds(seconds: float) -> None:
    if seconds and seconds > 0:
        time.sleep(float(seconds))


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _within_created_days(product: dict[str, Any], days: int) -> bool:
    if not days or days <= 0:
        return True
    created_at = _parse_datetime(product.get("created_at"))
    if created_at is None:
        return True
    return created_at >= datetime.now() - timedelta(days=int(days))


def classify_plan(result: dict[str, Any]) -> str:
    status = str(result.get("status") or "")
    if status == "error":
        return "error"
    if _int_value(result.get("new_fillable_sku_count")) > 0:
        return "ready"
    if _int_value(result.get("local_sku_count")) > 0:
        return "base_only"
    return "no_pairs"


def should_execute_plan(
    result: dict[str, Any],
    *,
    phase: str,
    max_sku_rows: int = 0,
    base_refresh_existing: bool = False,
) -> bool:
    classification = classify_plan(result)
    local_sku_count = _int_value(result.get("local_sku_count"))
    if max_sku_rows and local_sku_count > int(max_sku_rows):
        return False
    if phase == "ready":
        return classification == "ready"
    if phase == "base":
        if classification != "base_only":
            return False
        return base_refresh_existing or _int_value(result.get("existing_empty_base_count")) == 0
    if phase == "all":
        return classification in {"ready", "base_only"}
    return False


def _print_event(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, default=str), flush=True)


def _error_result(product: dict[str, Any], exc: Exception) -> dict[str, Any]:
    return {
        "product_id": product.get("id"),
        "product_code": product.get("product_code") or "",
        "name": product.get("name") or "",
        "status": "error",
        "message": str(exc),
        "local_sku_count": 0,
        "new_fillable_sku_count": 0,
    }


def run_orchestrator(args: argparse.Namespace) -> dict[str, Any]:
    started_at = datetime.now().isoformat(timespec="seconds")
    listed_only = not bool(args.include_unlisted)
    products = backfill.find_unprocessed_products(
        limit=int(args.scan_limit or 0),
        include_archived=bool(args.include_archived),
        listed_only=listed_only,
    )
    products = [
        product for product in products
        if _within_created_days(product, int(args.created_within_days or 0))
    ]

    items: list[dict[str, Any]] = []
    selected_count = 0
    executed_count = 0
    for index, product in enumerate(products):
        try:
            plan = backfill.run_product_sync(
                product,
                execute=False,
                force_refresh_mingkong=bool(args.force_refresh_mingkong),
                protect_configured_local_skus=bool(args.protect_configured_local_skus),
            )
        except Exception as exc:  # noqa: BLE001 - report all products in weekly scan
            plan = _error_result(product, exc)

        classification = classify_plan(plan)
        selected = bool(args.execute) and should_execute_plan(
            plan,
            phase=args.phase,
            max_sku_rows=int(args.max_sku_rows or 0),
            base_refresh_existing=bool(args.base_refresh_existing),
        )
        if selected and args.max_products and selected_count >= int(args.max_products):
            selected = False
        if selected:
            selected_count += 1

        _print_event({
            "event": "planned",
            "phase": args.phase,
            "product_id": plan.get("product_id"),
            "product_code": plan.get("product_code"),
            "classification": classification,
            "selected": selected,
            "local_sku_count": plan.get("local_sku_count"),
            "new_fillable_sku_count": plan.get("new_fillable_sku_count"),
        })

        execute_result = None
        if selected:
            try:
                execute_result = backfill.run_product_sync(
                    product,
                    execute=True,
                    force_refresh_mingkong=bool(args.force_refresh_mingkong),
                    overwrite_existing_pairing=bool(args.overwrite_existing_pairing),
                    protect_configured_local_skus=bool(args.protect_configured_local_skus),
                )
            except Exception as exc:  # noqa: BLE001 - keep weekly job reportable
                execute_result = _error_result(product, exc)
            executed_count += 1
            _print_event({
                "event": "executed",
                "phase": args.phase,
                "product_id": execute_result.get("product_id"),
                "product_code": execute_result.get("product_code"),
                "status": execute_result.get("status"),
                "message": execute_result.get("message"),
            })
            if executed_count < selected_count or index < len(products) - 1:
                _print_event({
                    "event": "sleep",
                    "seconds": float(args.product_delay_seconds or 0),
                    "reason": "product_delay",
                })
                _sleep_seconds(float(args.product_delay_seconds or 0))
        elif index < len(products) - 1:
            _sleep_seconds(float(args.plan_delay_seconds or 0))

        items.append({
            "product_id": plan.get("product_id"),
            "product_code": plan.get("product_code"),
            "classification": classification,
            "selected": selected,
            "plan": plan,
            "execute": execute_result,
        })

    finished_at = datetime.now().isoformat(timespec="seconds")
    execute_results = [item.get("execute") for item in items if item.get("execute")]
    plan_results = [item.get("plan") for item in items]
    summary = {
        "phase": args.phase,
        "execute": bool(args.execute),
        "candidate_count": len(products),
        "selected_count": selected_count,
        "executed_count": executed_count,
        "ready_count": sum(1 for item in items if item.get("classification") == "ready"),
        "base_only_count": sum(1 for item in items if item.get("classification") == "base_only"),
        "no_pairs_count": sum(1 for item in items if item.get("classification") == "no_pairs"),
        "plan_error_count": sum(1 for item in plan_results if item.get("status") == "error"),
        "execute_error_count": sum(1 for item in execute_results if item.get("status") == "error"),
        "execute_blocked_count": sum(
            1
            for item in execute_results
            if str(item.get("status") or "").endswith("_blocked")
            or str(item.get("status") or "").startswith("blocked_")
        ),
    }
    return {
        "started_at": started_at,
        "finished_at": finished_at,
        "summary": summary,
        "items": items,
    }


def write_report(report: dict[str, Any], *, output_dir: Path = OUTPUT_DIR) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    phase = (report.get("summary") or {}).get("phase") or "unknown"
    suffix = "execute" if (report.get("summary") or {}).get("execute") else "plan"
    path = output_dir / f"mingkong-weekly-sync-{phase}-{suffix}-{stamp}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Paced weekly Mingkong SKU sync orchestration.")
    parser.add_argument("--phase", choices=["plan", "ready", "base", "all"], default="plan")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--scan-limit", type=int, default=80)
    parser.add_argument("--max-products", type=int, default=0)
    parser.add_argument("--max-sku-rows", type=int, default=0)
    parser.add_argument("--created-within-days", type=int, default=14)
    parser.add_argument("--plan-delay-seconds", type=float, default=0.0)
    parser.add_argument("--product-delay-seconds", type=float, default=0.0)
    parser.add_argument("--include-archived", action="store_true")
    parser.add_argument("--include-unlisted", action="store_true")
    parser.add_argument("--force-refresh-mingkong", action="store_true")
    parser.add_argument("--overwrite-existing-pairing", action="store_true")
    parser.add_argument("--base-refresh-existing", action="store_true")
    parser.add_argument("--no-protect-configured-local-skus", action="store_true")
    args = parser.parse_args(argv)
    args.protect_configured_local_skus = not bool(args.no_protect_configured_local_skus)

    report = run_orchestrator(args)
    path = write_report(report)
    summary = report["summary"]
    print(json.dumps({
        "ok": summary["plan_error_count"] == 0 and summary["execute_error_count"] == 0,
        "report": str(path),
        "summary": summary,
    }, ensure_ascii=False, indent=2))
    return 0 if summary["plan_error_count"] == 0 and summary["execute_error_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
