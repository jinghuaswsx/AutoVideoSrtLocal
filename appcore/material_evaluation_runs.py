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


def rerun_product_evaluation_country_async(
    product_id: int,
    run_id: str,
    country_code: str,
) -> dict[str, Any]:
    product_id = int(product_id)
    run = _get_run(run_id)
    if not run or int(run.get("product_id") or 0) != product_id:
        raise MaterialEvaluationRunNotFound(str(run_id or ""))
    normalized_code = _normalize_country_code(country_code, run.get("progress") or {})
    if not normalized_code:
        raise ValueError(f"unsupported country code: {country_code}")

    progress = _mark_country_progress(
        run.get("progress") or {},
        normalized_code,
        status="running",
        error="",
        reset_timing=True,
    )
    _update_run(run_id, status="running", progress=progress)
    thread = threading.Thread(
        target=_run_country_rerun_sync,
        kwargs={
            "run_id": str(run_id),
            "product_id": product_id,
            "country_code": normalized_code,
        },
        name=f"material-eval-rerun-{str(run_id)[:12]}-{normalized_code}",
        daemon=True,
    )
    thread.start()
    return {
        "run_id": str(run_id),
        "product_id": product_id,
        "status": "running",
        "progress": progress,
    }


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


def _run_country_rerun_sync(
    *,
    run_id: str,
    product_id: int,
    country_code: str,
) -> None:
    from appcore import material_evaluation

    run = _get_run(run_id)
    if not run:
        return
    result = run.get("result") if isinstance(run.get("result"), dict) else {}
    existing_detail = (
        result.get("ai_evaluation_detail")
        or result.get("detail")
        or None
    )

    def update_progress(progress: dict[str, Any]) -> None:
        current = (_get_run(run_id) or {}).get("progress") or {}
        merged = _merge_single_country_progress(current, country_code, progress)
        _update_run(run_id, status="running", progress=merged)

    try:
        rerun_result = material_evaluation.rerun_country_evaluation(
            product_id,
            country_code,
            media_item_id=run.get("media_item_id"),
            product_url_override=run.get("product_url_override"),
            existing_detail=existing_detail,
            progress_callback=update_progress,
        )
        current = (_get_run(run_id) or {}).get("progress") or {}
        if rerun_result.get("status") == "evaluated":
            detail = rerun_result.get("ai_evaluation_detail") or {}
            progress = _progress_from_detail_after_rerun(current, country_code, detail)
            status = _terminal_status_for_progress(progress)
            _update_run(
                run_id,
                status=status,
                result=rerun_result,
                error="" if status != "failed" else rerun_result.get("error", ""),
                completed_at=_now_iso(),
                progress=progress,
            )
            return

        error = str(rerun_result.get("error") or rerun_result.get("message") or "评估失败")
        progress = _mark_country_progress(
            current,
            country_code,
            status="failed",
            error=error,
            finished_at=_now_iso(),
        )
        status = _terminal_status_for_progress(progress)
        _update_run(
            run_id,
            status=status,
            error=error if status == "failed" else "",
            completed_at=_now_iso(),
            progress=progress,
        )
    except Exception as exc:  # pragma: no cover - defensive around background work
        log.exception(
            "material evaluation country rerun failed: run_id=%s product_id=%s country=%s",
            run_id,
            product_id,
            country_code,
        )
        progress = _mark_country_progress(
            (_get_run(run_id) or {}).get("progress") or {},
            country_code,
            status="failed",
            error=str(exc)[:1000] or exc.__class__.__name__,
            finished_at=_now_iso(),
        )
        _update_run(
            run_id,
            status=_terminal_status_for_progress(progress),
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


def _normalize_country_code(country_code: str, progress: dict[str, Any]) -> str:
    raw = str(country_code or "").strip().lower()
    if not raw:
        return ""
    countries = progress.get("countries") if isinstance(progress, dict) else []
    if isinstance(countries, list):
        known = {str(row.get("lang") or "").strip().lower() for row in countries if isinstance(row, dict)}
        if raw in known:
            return raw
    return raw if len(raw) in {2, 5} and raw.replace("-", "").isalnum() else ""


def _mark_country_progress(
    progress: dict[str, Any],
    country_code: str,
    *,
    status: str,
    error: str = "",
    score: Any = None,
    result: str = "",
    summary: str = "",
    finished_at: str | None = None,
    reset_timing: bool = False,
) -> dict[str, Any]:
    now = _now_iso()
    payload = dict(progress or {})
    target = str(country_code or "").strip().lower()
    rows = []
    found = False
    for row in payload.get("countries") or []:
        item = dict(row) if isinstance(row, dict) else {}
        if str(item.get("lang") or "").strip().lower() == target:
            found = True
            item["status"] = status
            if reset_timing:
                item["started_at"] = now
                item["finished_at"] = None
                item["elapsed_seconds"] = 0
                item["score"] = None
                item["result"] = ""
                item["summary"] = ""
            if finished_at:
                item["finished_at"] = finished_at
            if score is not None:
                item["score"] = score
            if result:
                item["result"] = result
            if summary:
                item["summary"] = summary
            item["error"] = error or ""
        rows.append(item)
    if not found:
        rows.append({
            "lang": target,
            "language": target,
            "country": target,
            "status": status,
            "started_at": now if reset_timing else None,
            "finished_at": finished_at,
            "elapsed_seconds": 0,
            "score": score,
            "result": result,
            "summary": summary,
            "error": error or "",
        })
    payload["countries"] = rows
    payload["status"] = "running" if status == "running" else _terminal_status_for_progress(payload)
    payload["current_lang"] = target if status == "running" else ""
    if status != "running":
        payload["finished_at"] = finished_at or now
    _refresh_progress_counts(payload)
    return payload


def _merge_single_country_progress(
    current_progress: dict[str, Any],
    country_code: str,
    single_progress: dict[str, Any],
) -> dict[str, Any]:
    target = str(country_code or "").strip().lower()
    single_rows = {
        str(row.get("lang") or "").strip().lower(): row
        for row in (single_progress.get("countries") or [])
        if isinstance(row, dict)
    }
    row = single_rows.get(target)
    if not row:
        return dict(current_progress or {})
    merged = _mark_country_progress(
        current_progress,
        target,
        status=str(row.get("status") or "running").lower(),
        error=str(row.get("error") or ""),
        score=row.get("score"),
        result=str(row.get("result") or ""),
        summary=str(row.get("summary") or ""),
        finished_at=row.get("finished_at"),
        reset_timing=str(row.get("status") or "").lower() == "running",
    )
    merged["status"] = "running"
    merged["current_lang"] = target if row.get("status") == "running" else ""
    return merged


def _progress_from_detail_after_rerun(
    current_progress: dict[str, Any],
    country_code: str,
    detail: dict[str, Any],
) -> dict[str, Any]:
    target = str(country_code or "").strip().lower()
    country_rows = detail.get("countries") if isinstance(detail, dict) else []
    target_row = next(
        (
            row for row in country_rows or []
            if isinstance(row, dict) and str(row.get("lang") or "").strip().lower() == target
        ),
        {},
    )
    progress = _mark_country_progress(
        current_progress,
        target,
        status="completed",
        score=target_row.get("score"),
        result=str(target_row.get("decision") or target_row.get("recommendation") or ""),
        summary=str(target_row.get("summary") or target_row.get("reason") or ""),
        finished_at=_now_iso(),
    )
    progress["status"] = _terminal_status_for_progress(progress)
    progress["finished_at"] = _now_iso()
    progress["current_lang"] = ""
    _refresh_progress_counts(progress)
    return progress


def _refresh_progress_counts(progress: dict[str, Any]) -> None:
    countries = progress.get("countries") if isinstance(progress, dict) else []
    rows = countries if isinstance(countries, list) else []
    completed = sum(1 for row in rows if str((row or {}).get("status") or "").lower() == "completed")
    failed = sum(1 for row in rows if str((row or {}).get("status") or "").lower() == "failed")
    running = sum(1 for row in rows if str((row or {}).get("status") or "").lower() == "running")
    queued = sum(1 for row in rows if str((row or {}).get("status") or "").lower() in {"queued", "pending", ""})
    progress["completed_count"] = completed
    progress["failed_count"] = failed
    progress["total_count"] = len(rows)
    progress["summary"] = {
        "total": len(rows),
        "completed": completed,
        "failed": failed,
        "running": running,
        "queued": queued,
    }


def _terminal_status_for_progress(progress: dict[str, Any]) -> str:
    countries = progress.get("countries") if isinstance(progress, dict) else []
    rows = countries if isinstance(countries, list) else []
    completed = sum(1 for row in rows if str((row or {}).get("status") or "").lower() == "completed")
    failed = sum(1 for row in rows if str((row or {}).get("status") or "").lower() == "failed")
    running = any(str((row or {}).get("status") or "").lower() == "running" for row in rows)
    queued = any(str((row or {}).get("status") or "").lower() in {"queued", "pending", ""} for row in rows)
    if running or queued:
        return "running"
    if failed and completed:
        return "partially_completed"
    if failed:
        return "failed"
    return "completed"


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
        "product_url_override": row.get("product_url_override"),
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
