from __future__ import annotations

import re
from urllib.parse import urlparse
from typing import Any


DEFAULT_LINK_DOMAINS: tuple[str, ...] = ("newjoyloo.com", "omurio.com")

_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)


def _query(sql: str, args: tuple = ()):
    from appcore.db import query

    return query(sql, args)


def _execute(sql: str, args: tuple = ()):
    from appcore.db import execute

    return execute(sql, args)


def normalize_domain(value: str) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        raise ValueError("domain_required")
    parsed = urlparse(raw if "://" in raw else f"//{raw}")
    domain = (parsed.hostname or parsed.netloc or parsed.path).strip().lower()
    if domain.startswith("www."):
        domain = domain[4:]
    domain = domain.rstrip(".")
    if not _DOMAIN_RE.match(domain):
        raise ValueError("domain_invalid")
    return domain


def domain_from_url(value: str) -> str:
    try:
        return normalize_domain(value)
    except ValueError:
        return ""


def build_product_page_url(domain: str, lang: str, product_code: str) -> str:
    normalized_domain = normalize_domain(domain)
    code = str(product_code or "").strip()
    if not code:
        return ""
    lang_code = (lang or "en").strip().lower() or "en"
    if lang_code == "en":
        return f"https://{normalized_domain}/products/{code}"
    return f"https://{normalized_domain}/{lang_code}/products/{code}"


def list_domains(*, include_disabled: bool = True) -> list[dict[str, Any]]:
    where = "" if include_disabled else "WHERE enabled=1"
    rows = _query(
        f"SELECT id, domain, enabled, sort_order, created_at, updated_at "
        f"FROM media_link_domains {where} "
        "ORDER BY sort_order ASC, id ASC",
        (),
    ) or []
    return [
        {
            **dict(row),
            "id": int(row.get("id") or 0),
            "domain": normalize_domain(str(row.get("domain") or "")),
            "enabled": bool(row.get("enabled")),
            "sort_order": int(row.get("sort_order") or 0),
        }
        for row in rows
    ]


def upsert_domain(domain: str, *, enabled: bool = True) -> int:
    normalized = normalize_domain(domain)
    existing = _query(
        "SELECT id FROM media_link_domains WHERE domain=%s LIMIT 1",
        (normalized,),
    ) or []
    if existing:
        domain_id = int(existing[0].get("id") or 0)
        _execute(
            "UPDATE media_link_domains SET enabled=%s WHERE id=%s",
            (1 if enabled else 0, domain_id),
        )
        return domain_id
    rows = _query("SELECT COALESCE(MAX(sort_order), 0) AS max_order FROM media_link_domains", ()) or []
    sort_order = int((rows[0] if rows else {}).get("max_order") or 0) + 10
    return int(_execute(
        "INSERT INTO media_link_domains (domain, enabled, sort_order) VALUES (%s,%s,%s)",
        (normalized, 1 if enabled else 0, sort_order),
    ) or 0)


def set_global_enabled_domain_ids(enabled_ids: list[int]) -> None:
    enabled_set: set[int] = set()
    for value in enabled_ids:
        try:
            domain_id = int(value)
        except (TypeError, ValueError):
            continue
        if domain_id > 0:
            enabled_set.add(domain_id)
    rows = list_domains(include_disabled=True)
    for row in rows:
        domain_id = int(row["id"])
        _execute(
            "UPDATE media_link_domains SET enabled=%s WHERE id=%s",
            (1 if domain_id in enabled_set else 0, domain_id),
        )


def delete_domain(domain_id: int) -> int:
    did = int(domain_id)
    if did <= 0:
        return 0
    _execute("DELETE FROM media_product_link_domains WHERE domain_id=%s", (did,))
    return int(_execute("DELETE FROM media_link_domains WHERE id=%s", (did,)) or 0)


def _product_domain_rows(product_id: int) -> dict[int, bool]:
    rows = _query(
        "SELECT domain_id, enabled FROM media_product_link_domains WHERE product_id=%s",
        (int(product_id),),
    ) or []
    return {int(row.get("domain_id") or 0): bool(row.get("enabled")) for row in rows}


def list_product_domain_options(product_id: int) -> list[dict[str, Any]]:
    domains = list_domains(include_disabled=True)
    overrides = _product_domain_rows(product_id)
    customized = bool(overrides)
    options: list[dict[str, Any]] = []
    for row in domains:
        domain_id = int(row["id"])
        global_enabled = bool(row["enabled"])
        product_enabled = overrides.get(domain_id, global_enabled)
        options.append({
            **row,
            "enabled": global_enabled,
            "product_enabled": bool(product_enabled),
            "effective_enabled": bool(global_enabled and product_enabled),
            "customized": customized,
        })
    return options


def list_enabled_product_domains(product_id: int) -> list[dict[str, Any]]:
    return [
        row for row in list_product_domain_options(product_id)
        if row["effective_enabled"]
    ]


def set_product_domain_enabled_ids(product_id: int, enabled_ids: list[int]) -> None:
    pid = int(product_id)
    enabled_set = {int(value) for value in enabled_ids if int(value) > 0}
    domains = list_domains(include_disabled=True)
    for row in domains:
        domain_id = int(row["id"])
        enabled = 1 if domain_id in enabled_set else 0
        _execute(
            """
            INSERT INTO media_product_link_domains (product_id, domain_id, enabled)
            VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE enabled=VALUES(enabled)
            """,
            (pid, domain_id, enabled),
        )
