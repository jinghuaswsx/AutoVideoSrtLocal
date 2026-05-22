from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Callable, Iterable

from appcore import link_availability


ProbeFn = Callable[[str], dict[str, Any]]


def resolve_product_link(
    *,
    current_link: str,
    candidate_links: Iterable[str] | None = None,
    probe_fn: ProbeFn | None = None,
    checked_candidates: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Probe the current link first, then Mingkong candidates in order."""
    original = _clean_link(current_link)
    probe = probe_fn or link_availability.probe
    targets: list[dict[str, str]] = []
    if original:
        targets.append({"url": original, "source": "current"})
    for link in candidate_links or []:
        url = _clean_link(link)
        if url:
            targets.append({"url": url, "source": "mingkong"})

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for checked in checked_candidates or []:
        row = _normalize_checked_candidate(checked)
        if not row or row["url"] in seen:
            continue
        seen.add(row["url"])
        rows.append(row)
        if row["ok"]:
            return _build_result(original=original, rows=rows, selected=row["url"])

    for target in targets:
        url = target["url"]
        if url in seen:
            continue
        seen.add(url)
        row = _candidate_result(
            url=url,
            source=target["source"],
            result=probe(url),
            used=False,
        )
        rows.append(row)
        if row["ok"]:
            return _build_result(original=original, rows=rows, selected=url)

    return _build_result(original=original, rows=rows, selected="")


def _clean_link(value: Any) -> str:
    text = str(value or "").strip()
    return text if text.startswith(("http://", "https://")) else ""


def _candidate_result(*, url: str, source: str, result: dict[str, Any], used: bool) -> dict[str, Any]:
    result = result if isinstance(result, dict) else {}
    return {
        "url": url,
        "source": source,
        "ok": bool(result.get("ok")),
        "http_status": (
            int(result["http_status"]) if result.get("http_status") is not None else None
        ),
        "error": str(result.get("error") or "") or None,
        "elapsed_ms": (
            int(result["elapsed_ms"]) if result.get("elapsed_ms") is not None else None
        ),
        "used": bool(used),
    }


def _normalize_checked_candidate(row: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    url = _clean_link(row.get("url"))
    if not url:
        return None
    return {
        "url": url,
        "source": str(row.get("source") or "current"),
        "ok": bool(row.get("ok")),
        "http_status": (
            int(row["http_status"]) if row.get("http_status") is not None else None
        ),
        "error": str(row.get("error") or "") or None,
        "elapsed_ms": (
            int(row["elapsed_ms"]) if row.get("elapsed_ms") is not None else None
        ),
        "used": False,
    }


def _build_result(*, original: str, rows: list[dict[str, Any]], selected: str) -> dict[str, Any]:
    candidates = [dict(row, used=(row["url"] == selected)) for row in rows]
    if selected:
        status = "ok" if selected == original else "replaced"
    else:
        status = "failed"
    return {
        "ok": bool(selected),
        "status": status,
        "original_link": original,
        "selected_link": selected,
        "candidate_count": len(candidates),
        "candidates": candidates,
        "checked_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "message": _message(status, len(candidates)),
    }


def _message(status: str, candidate_count: int) -> str:
    if status == "ok":
        return "当前商品链接可访问"
    if status == "replaced":
        return "当前商品链接不可访问，已使用明空候选链接"
    if candidate_count:
        return "商品链接和明空候选链接均不可访问"
    return "没有可检测的商品链接"
