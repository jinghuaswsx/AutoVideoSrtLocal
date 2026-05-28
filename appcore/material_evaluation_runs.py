"""Async run tracking for material AI evaluation."""

from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import UTC, datetime
from typing import Any

from appcore.db import execute, query_one

log = logging.getLogger(__name__)


class MaterialEvaluationRunNotFound(RuntimeError):
    pass


def start_product_evaluation_async(
    product_id: int,
    *,
    media_item_id: int | None = None,
    product_url_override: str | None = None,
) -> dict[str, Any]:
    from appcore import material_evaluation

    product_id = int(product_id)
    languages = material_evaluation.evaluation_target_languages()
    llm_config = material_evaluation.resolve_evaluation_llm_config()
    run_id = f"mat_eval_{uuid.uuid4().hex}"
    now = _now_iso()
    progress = _initial_progress(
        product_id=product_id,
        run_id=run_id,
        languages=languages,
        status="queued",
        llm_config=llm_config,
        started_at=now,
    )
    _create_run(
        run_id=run_id,
        product_id=product_id,
        media_item_id=media_item_id,
        product_url_override=product_url_override,
        progress=progress,
    )
    thread = threading.Thread(
        target=_run_product_evaluation_sync,
        kwargs={
            "run_id": run_id,
            "product_id": product_id,
            "media_item_id": media_item_id,
            "product_url_override": product_url_override,
        },
        name=f"material-eval-{run_id[:16]}",
        daemon=True,
    )
    thread.start()
    return {
        "run_id": run_id,
        "product_id": product_id,
        "status": "queued",
        "progress": progress,
        "created_at": now,
    }


def get_product_evaluation_status(product_id: int, run_id: str) -> dict[str, Any]:
    run = _get_run(run_id)
    if not run or int(run.get("product_id") or 0) != int(product_id):
        raise MaterialEvaluationRunNotFound(str(run_id or ""))
    return run


def _run_product_evaluation_sync(
    *,
    run_id: str,
    product_id: int,
    media_item_id: int | None,
    product_url_override: str | None,
) -> None:
    from appcore import material_evaluation

    started_at = _now_iso()
    _update_run(
        run_id,
        status="running",
        started_at=started_at,
        progress_patch={"status": "running", "started_at": started_at},
    )

    def update_progress(progress: dict[str, Any]) -> None:
        payload = dict(progress or {})
        payload["run_id"] = run_id
        payload["product_id"] = int(product_id)
        _update_run(run_id, status=payload.get("status") or "running", progress=payload)

    try:
        result = material_evaluation.evaluate_product_if_ready(
            product_id,
            force=True,
            manual=True,
            media_item_id=media_item_id,
            product_url_override=product_url_override,
            progress_callback=update_progress,
        )
        current_progress = (_get_run(run_id) or {}).get("progress") or {}
        progress_status = str(current_progress.get("status") or "").strip().lower()
        if result.get("status") == "evaluated":
            status = progress_status if progress_status in {"completed", "partially_completed"} else "completed"
        else:
            status = "failed"
        progress = _terminal_progress(
            run_id=run_id,
            product_id=product_id,
            result=result,
            status=status,
            current_progress=current_progress,
        )
        _update_run(
            run_id,
            status=status,
            result=result,
            error="" if status == "completed" else _error_from_result(result),
            completed_at=_now_iso(),
            progress=progress,
        )
    except Exception as exc:  # pragma: no cover - defensive around background work
        log.exception("material evaluation async run failed: run_id=%s product_id=%s", run_id, product_id)
        progress = _progress_from_result(
            run_id,
            product_id,
            {"status": "failed", "error": str(exc) or exc.__class__.__name__},
            status="failed",
        )
        _update_run(
            run_id,
            status="failed",
            error=str(exc)[:1000] or exc.__class__.__name__,
            completed_at=_now_iso(),
            progress=progress,
        )


def _initial_progress(
    *,
    product_id: int,
    run_id: str,
    languages: list[dict[str, Any]],
    status: str,
    llm_config: dict[str, Any] | None = None,
    started_at: str | None = None,
) -> dict[str, Any]:
    rows = []
    for lang in languages:
        code = str(lang.get("code") or "").strip().lower()
        rows.append({
            "lang": code,
            "language": lang.get("name") or code,
            "country": lang.get("country") or lang.get("name") or code,
            "status": "queued",
            "started_at": None,
            "finished_at": None,
            "elapsed_seconds": 0,
            "score": None,
            "result": "",
            "error": "",
        })
    return {
        "schema_version": 1,
        "evaluation_mode": "per_country",
        "run_id": run_id,
        "product_id": int(product_id),
        "status": status,
        "started_at": started_at,
        "finished_at": None,
        "elapsed_seconds": 0,
        "current_lang": "",
        "completed_count": 0,
        "failed_count": 0,
        "total_count": len(rows),
        "provider": (llm_config or {}).get("provider"),
        "model": (llm_config or {}).get("model"),
        "countries": rows,
    }


def _progress_from_result(run_id: str, product_id: int, result: dict[str, Any], *, status: str) -> dict[str, Any]:
    detail = result.get("ai_evaluation_detail") or result.get("detail") or {}
    countries = detail.get("countries") if isinstance(detail, dict) else []
    rows = []
    for row in countries or []:
        rows.append({
            "lang": row.get("lang"),
            "language": row.get("language"),
            "country": row.get("country"),
            "status": "completed",
            "started_at": None,
            "finished_at": _now_iso(),
            "elapsed_seconds": 0,
            "score": row.get("score"),
            "result": row.get("decision") or row.get("recommendation") or "",
            "error": "",
        })
    failed = 1 if status == "failed" and not rows else 0
    return {
        "schema_version": 1,
        "evaluation_mode": "per_country",
        "run_id": run_id,
        "product_id": int(product_id),
        "status": status,
        "finished_at": _now_iso(),
        "current_lang": "",
        "completed_count": len(rows),
        "failed_count": failed,
        "total_count": len(rows),
        "countries": rows,
        "error": _error_from_result(result) if status == "failed" else "",
    }


def _terminal_progress(
    *,
    run_id: str,
    product_id: int,
    result: dict[str, Any],
    status: str,
    current_progress: dict[str, Any] | None = None,
) -> dict[str, Any]:
    current = dict(current_progress or {})
    if isinstance(current.get("countries"), list) and current["countries"]:
        finished_at = current.get("finished_at") or _now_iso()
        current.update({
            "run_id": run_id,
            "product_id": int(product_id),
            "status": status,
            "finished_at": finished_at,
            "current_lang": "",
        })
        if status == "failed":
            current["error"] = _error_from_result(result)
        return current
    return _progress_from_result(run_id, product_id, result, status=status)


def _create_run(
    *,
    run_id: str,
    product_id: int,
    media_item_id: int | None,
    product_url_override: str | None,
    progress: dict[str, Any],
) -> None:
    execute(
        "INSERT INTO material_evaluation_runs "
        "(run_id, product_id, media_item_id, status, product_url_override, progress_json, created_at, updated_at) "
        "VALUES (%s,%s,%s,'queued',%s,%s,NOW(),NOW())",
        (
            str(run_id),
            int(product_id),
            int(media_item_id) if media_item_id else None,
            str(product_url_override or "") or None,
            _dump(progress),
        ),
    )


def _get_run(run_id: str) -> dict[str, Any] | None:
    row = query_one(
        "SELECT * FROM material_evaluation_runs WHERE run_id=%s LIMIT 1",
        (str(run_id or ""),),
    )
    if not row:
        return None
    result = _load(row.get("result_json"), {})
    progress = _load(row.get("progress_json"), {})
    return {
        "run_id": row.get("run_id"),
        "product_id": int(row.get("product_id") or 0),
        "media_item_id": row.get("media_item_id"),
        "status": row.get("status") or "queued",
        "progress": progress,
        "result": result,
        "error": row.get("error_message") or "",
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
        "started_at": _iso(row.get("started_at")),
        "completed_at": _iso(row.get("completed_at")),
    }


def _update_run(
    run_id: str,
    *,
    status: str | None = None,
    progress: dict[str, Any] | None = None,
    progress_patch: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    error: str | None = None,
    started_at: str | None = None,
    completed_at: str | None = None,
) -> None:
    if progress_patch:
        current = (_get_run(run_id) or {}).get("progress") or {}
        progress = {**current, **progress_patch}
    set_parts = ["updated_at=NOW()"]
    args: list[Any] = []
    if status is not None:
        set_parts.append("status=%s")
        args.append(str(status))
    if progress is not None:
        set_parts.append("progress_json=%s")
        args.append(_dump(progress))
    if result is not None:
        set_parts.append("result_json=%s")
        args.append(_dump(result))
    if error is not None:
        set_parts.append("error_message=%s")
        args.append((error or "")[:1000] or None)
    if started_at is not None:
        set_parts.append("started_at=%s")
        args.append(_mysql_datetime(started_at))
    if completed_at is not None:
        set_parts.append("completed_at=%s")
        args.append(_mysql_datetime(completed_at))
    args.append(str(run_id))
    execute(
        f"UPDATE material_evaluation_runs SET {', '.join(set_parts)} WHERE run_id=%s",
        tuple(args),
    )


def _error_from_result(result: dict[str, Any]) -> str:
    return str(result.get("error") or result.get("message") or "")[:1000]


def _dump(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, default=str)


def _load(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except Exception:
        return default


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _mysql_datetime(value: str | datetime | None) -> Any:
    if value is None or isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return value
    if parsed is None:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed.replace(microsecond=0)
