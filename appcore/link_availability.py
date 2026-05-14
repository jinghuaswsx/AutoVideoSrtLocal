"""Lightweight HTTP availability probe + cache for product page URLs.

Powers the "产品链接管理" modal in the medias edit page. Spec:
docs/superpowers/specs/2026-05-09-product-link-management-modal.md

Probes are HEAD-first (with GET fallback on 405), follow up to 5 redirects,
and cap at a 5s timeout. Results upsert per (product_id, lang, domain) into
media_product_link_availability so the modal can show last known status
without re-probing on every open.
"""
from __future__ import annotations

import socket
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Iterable
from urllib import error as urllib_error
from urllib import request as urllib_request


USER_AGENT = "Mozilla/5.0 (compatible; AutoVideoSrt-LinkAvailability/1.0)"
DEFAULT_TIMEOUT = 5.0
DEFAULT_MAX_REDIRECTS = 5
DEFAULT_PROBE_PARALLELISM = 8


def _query(sql: str, args: tuple = ()):
    from appcore.db import query

    return query(sql, args)


def _execute(sql: str, args: tuple = ()):
    from appcore.db import execute

    return execute(sql, args)


def _build_request(url: str, method: str) -> urllib_request.Request:
    return urllib_request.Request(
        url,
        method=method,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
            "Accept-Language": "*",
        },
    )


def _classify_http_error(status: int) -> tuple[bool, str | None]:
    if 200 <= status < 400:
        return True, None
    return False, f"http {status}"


def probe(
    url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
    opener_factory=None,
) -> dict[str, Any]:
    """Probe one URL and return a normalized availability dict.

    Returns keys: http_status (int|None), ok (bool), error (str|None),
    elapsed_ms (int).
    """
    started = time.monotonic()
    handler = urllib_request.HTTPRedirectHandler()
    handler.max_redirections = max_redirects  # type: ignore[attr-defined]
    if opener_factory is None:
        opener = urllib_request.build_opener(handler)
    else:
        opener = opener_factory(handler)

    def _do(method: str):
        req = _build_request(url, method)
        return opener.open(req, timeout=timeout)

    response = None
    last_error: str | None = None
    status: int | None = None
    try:
        try:
            response = _do("HEAD")
        except urllib_error.HTTPError as exc:
            # Some servers reject HEAD with 405 / 501; retry once with GET.
            if exc.code in (405, 501):
                try:
                    response = _do("GET")
                except urllib_error.HTTPError as exc2:
                    status = exc2.code
                    ok, last_error = _classify_http_error(status)
                    return {
                        "http_status": status,
                        "ok": ok,
                        "error": last_error,
                        "elapsed_ms": int((time.monotonic() - started) * 1000),
                    }
                except Exception as exc2:  # noqa: BLE001
                    last_error = _format_error(exc2)
                    return {
                        "http_status": None,
                        "ok": False,
                        "error": last_error,
                        "elapsed_ms": int((time.monotonic() - started) * 1000),
                    }
            else:
                status = exc.code
                ok, last_error = _classify_http_error(status)
                return {
                    "http_status": status,
                    "ok": ok,
                    "error": last_error,
                    "elapsed_ms": int((time.monotonic() - started) * 1000),
                }
        except Exception as exc:  # noqa: BLE001
            last_error = _format_error(exc)
            return {
                "http_status": None,
                "ok": False,
                "error": last_error,
                "elapsed_ms": int((time.monotonic() - started) * 1000),
            }

        status = getattr(response, "status", None) or response.getcode()
        ok, last_error = _classify_http_error(int(status))
        return {
            "http_status": int(status),
            "ok": ok,
            "error": last_error,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }
    finally:
        if response is not None:
            try:
                response.close()
            except Exception:  # noqa: BLE001
                pass


def _format_error(exc: BaseException) -> str:
    if isinstance(exc, socket.timeout):
        return "timeout"
    if isinstance(exc, urllib_error.URLError):
        reason = getattr(exc, "reason", None)
        if isinstance(reason, socket.timeout):
            return "timeout"
        if reason is not None:
            return f"network: {reason}"
        return f"network: {exc}"
    return f"{type(exc).__name__}: {exc}".strip()


def upsert_result(
    *,
    product_id: int,
    lang: str,
    domain: str,
    link_url: str,
    result: dict[str, Any],
) -> None:
    pid = int(product_id)
    lang_code = (lang or "").strip().lower()
    domain_value = (domain or "").strip().lower()
    if not pid or not lang_code or not domain_value:
        return
    http_status = result.get("http_status")
    ok = 1 if result.get("ok") else 0
    error = result.get("error") or None
    elapsed_ms = result.get("elapsed_ms")
    _execute(
        """
        INSERT INTO media_product_link_availability
            (product_id, lang, domain, link_url, http_status, ok, error, elapsed_ms, checked_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
        ON DUPLICATE KEY UPDATE
            link_url = VALUES(link_url),
            http_status = VALUES(http_status),
            ok = VALUES(ok),
            error = VALUES(error),
            elapsed_ms = VALUES(elapsed_ms),
            checked_at = NOW()
        """,
        (
            pid,
            lang_code,
            domain_value,
            (link_url or "")[:1024],
            http_status,
            ok,
            error,
            elapsed_ms,
        ),
    )


def manual_confirm_result(
    *,
    product_id: int,
    lang: str,
    domain: str,
    link_url: str,
) -> None:
    """Mark one product-link domain as manually confirmed reachable."""
    upsert_result(
        product_id=product_id,
        lang=lang,
        domain=domain,
        link_url=link_url,
        result={
            "http_status": 200,
            "ok": True,
            "error": "manual_confirmed",
            "elapsed_ms": 0,
        },
    )


def list_results(product_id: int, lang: str) -> list[dict[str, Any]]:
    pid = int(product_id)
    lang_code = (lang or "").strip().lower()
    if not pid or not lang_code:
        return []
    rows = _query(
        """
        SELECT product_id, lang, domain, link_url, http_status, ok, error, elapsed_ms, checked_at
          FROM media_product_link_availability
         WHERE product_id=%s AND lang=%s
         ORDER BY domain ASC
        """,
        (pid, lang_code),
    ) or []
    return [_serialize_row(row) for row in rows]


def get_result_for_domain(
    product_id: int, lang: str, domain: str
) -> dict[str, Any] | None:
    pid = int(product_id)
    lang_code = (lang or "").strip().lower()
    domain_value = (domain or "").strip().lower()
    if not pid or not lang_code or not domain_value:
        return None
    rows = _query(
        """
        SELECT product_id, lang, domain, link_url, http_status, ok, error, elapsed_ms, checked_at
          FROM media_product_link_availability
         WHERE product_id=%s AND lang=%s AND domain=%s
         LIMIT 1
        """,
        (pid, lang_code, domain_value),
    ) or []
    return _serialize_row(rows[0]) if rows else None


def _serialize_row(row: dict[str, Any]) -> dict[str, Any]:
    checked_at = row.get("checked_at")
    if hasattr(checked_at, "isoformat"):
        checked_at_iso = checked_at.isoformat()
    else:
        checked_at_iso = str(checked_at) if checked_at else ""
    return {
        "product_id": int(row.get("product_id") or 0),
        "lang": str(row.get("lang") or ""),
        "domain": str(row.get("domain") or ""),
        "link_url": str(row.get("link_url") or ""),
        "http_status": (
            int(row["http_status"]) if row.get("http_status") is not None else None
        ),
        "ok": bool(row.get("ok")),
        "error": (str(row["error"]) if row.get("error") else None),
        "elapsed_ms": (
            int(row["elapsed_ms"]) if row.get("elapsed_ms") is not None else None
        ),
        "checked_at": checked_at_iso,
    }


def probe_and_record(
    *,
    product_id: int,
    lang: str,
    rows: Iterable[dict[str, Any]],
    timeout: float = DEFAULT_TIMEOUT,
    max_workers: int = DEFAULT_PROBE_PARALLELISM,
    probe_fn=probe,
) -> list[dict[str, Any]]:
    """Probe many domains in parallel, persist each result, return latest list."""
    targets = []
    for row in rows or []:
        domain = str((row or {}).get("domain") or "").strip().lower()
        url = str((row or {}).get("url") or "").strip()
        if not domain or not url:
            continue
        targets.append({"domain": domain, "url": url})

    if not targets:
        return []

    def _run(target: dict[str, Any]) -> dict[str, Any]:
        result = probe_fn(target["url"], timeout=timeout)
        upsert_result(
            product_id=product_id,
            lang=lang,
            domain=target["domain"],
            link_url=target["url"],
            result=result,
        )
        return get_result_for_domain(product_id, lang, target["domain"]) or {
            **result,
            "domain": target["domain"],
            "link_url": target["url"],
        }

    workers = max(1, min(int(max_workers), len(targets)))
    if workers == 1:
        results = [_run(target) for target in targets]
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(_run, targets))

    # Final ordering: same as input.
    by_domain = {item["domain"]: item for item in results}
    ordered: list[dict[str, Any]] = []
    seen: set[str] = set()
    for target in targets:
        domain = target["domain"]
        if domain in seen:
            continue
        seen.add(domain)
        if domain in by_domain:
            ordered.append(by_domain[domain])
    return ordered
