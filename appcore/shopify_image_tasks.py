"""Shopify 图片替换任务中心的状态与队列服务。"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any

from appcore import medias, product_link_domains
from appcore.db import execute, query, query_one


REPLACE_NONE = "none"
REPLACE_PENDING = "pending"
REPLACE_RUNNING = "running"
REPLACE_AUTO_DONE = "auto_done"
REPLACE_FAILED = "failed"
REPLACE_CONFIRMED = "confirmed"

LINK_UNKNOWN = "unknown"
LINK_NEEDS_REVIEW = "needs_review"
LINK_NORMAL = "normal"
LINK_UNAVAILABLE = "unavailable"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_status_map(value: str | dict | None) -> dict[str, dict[str, Any]]:
    if isinstance(value, dict):
        return {
            str(key).strip().lower(): dict(payload or {})
            for key, payload in value.items()
            if str(key).strip() and isinstance(payload, dict)
        }
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {
        str(key).strip().lower(): dict(payload or {})
        for key, payload in parsed.items()
        if str(key).strip() and isinstance(payload, dict)
    }


def status_for_lang(
    status_map: dict[str, dict[str, Any]],
    lang: str,
    domain: str | None = None,
    fallback_legacy: bool = True,
) -> dict[str, Any]:
    normalized_lang = (lang or "").strip().lower()
    normalized_domain = product_link_domains.domain_from_url(domain or "")
    status_key = (
        product_link_domains.domain_lang_key(normalized_domain, normalized_lang)
        if normalized_domain else normalized_lang
    )
    raw = dict((status_map or {}).get(status_key) or {})
    if normalized_domain and fallback_legacy and not raw:
        raw = dict((status_map or {}).get(normalized_lang) or {})
    return {
        "replace_status": raw.get("replace_status") or REPLACE_NONE,
        "link_status": raw.get("link_status") or LINK_UNKNOWN,
        "status_key": status_key,
        "domain": normalized_domain,
        "lang": normalized_lang,
        "last_task_id": raw.get("last_task_id"),
        "last_error": raw.get("last_error") or "",
        "result_summary": raw.get("result_summary") or {},
        "confirmed_by": raw.get("confirmed_by"),
        "confirmed_at": raw.get("confirmed_at"),
        "updated_at": raw.get("updated_at"),
    }


def update_lang_status(
    product_id: int,
    lang: str,
    *,
    domain: str | None = None,
    **updates: Any,
) -> dict[str, Any]:
    product = medias.get_product(product_id) or {}
    status_map = parse_status_map(product.get("shopify_image_status_json"))
    normalized_lang = (lang or "").strip().lower()
    normalized_domain = product_link_domains.domain_from_url(domain or "")
    status_key = (
        product_link_domains.domain_lang_key(normalized_domain, normalized_lang)
        if normalized_domain else normalized_lang
    )
    current = status_for_lang(status_map, normalized_lang, normalized_domain or None)
    current.update(updates)
    current["status_key"] = status_key
    current["domain"] = normalized_domain
    current["lang"] = normalized_lang
    current["updated_at"] = _now_iso()
    status_map[status_key] = current
    medias.update_product(product_id, shopify_image_status_json=status_map)
    return current


def _enabled_link_rows_for_product(product_id: int, lang: str) -> list[dict[str, str]]:
    try:
        product = medias.get_product(product_id) or {}
        return product_link_domains.resolve_product_page_url_rows(product, lang)
    except Exception:
        return []


def update_enabled_domain_statuses(product_id: int, lang: str, **updates: Any) -> dict[str, Any]:
    rows = _enabled_link_rows_for_product(product_id, lang)
    if not rows:
        return update_lang_status(product_id, lang, **updates)
    latest: dict[str, Any] = {}
    for row in rows:
        latest = update_lang_status(
            product_id,
            lang,
            domain=row.get("domain"),
            **updates,
        )
    return latest


def confirm_lang(
    product_id: int,
    lang: str,
    user_id: int | None = None,
    *,
    domain: str | None = None,
) -> dict[str, Any]:
    updater = update_lang_status if domain else update_enabled_domain_statuses
    kwargs: dict[str, Any] = {}
    if domain:
        kwargs["domain"] = domain
    return updater(
        product_id,
        lang,
        **kwargs,
        replace_status=REPLACE_CONFIRMED,
        link_status=LINK_NORMAL,
        confirmed_by=user_id,
        confirmed_at=_now_iso(),
        last_error="",
    )


def mark_link_unavailable(
    product_id: int,
    lang: str,
    reason: str = "",
    *,
    domain: str | None = None,
) -> dict[str, Any]:
    updater = update_lang_status if domain else update_enabled_domain_statuses
    kwargs: dict[str, Any] = {}
    if domain:
        kwargs["domain"] = domain
    return updater(
        product_id,
        lang,
        **kwargs,
        replace_status=REPLACE_FAILED,
        link_status=LINK_UNAVAILABLE,
        last_error=(reason or "链接不可用，等待负责人处理"),
    )


def reset_lang(product_id: int, lang: str, *, domain: str | None = None) -> dict[str, Any]:
    updater = update_lang_status if domain else update_enabled_domain_statuses
    kwargs: dict[str, Any] = {}
    if domain:
        kwargs["domain"] = domain
    return updater(
        product_id,
        lang,
        **kwargs,
        replace_status=REPLACE_NONE,
        link_status=LINK_UNKNOWN,
        last_error="",
        confirmed_by=None,
        confirmed_at=None,
    )


TASK_PENDING = "pending"
TASK_RUNNING = "running"
TASK_SUCCESS = "success"
TASK_FAILED = "failed"
TASK_BLOCKED = "blocked"
TASK_CANCELLED = "cancelled"


def _loads_product_links(product: dict) -> dict[str, str]:
    raw = product.get("localized_links_json")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            raw = {}
    if not isinstance(raw, dict):
        return {}
    return {
        str(key).strip().lower(): str(value).strip()
        for key, value in raw.items()
        if str(key).strip() and str(value).strip()
    }


def resolve_link_urls(product: dict, lang: str) -> list[dict[str, str]]:
    return product_link_domains.resolve_product_page_url_rows(product or {}, lang)


def resolve_link_url(product: dict, lang: str) -> str:
    rows = resolve_link_urls(product, lang)
    return rows[0]["url"] if rows else ""


def _enrich_task_link_urls(task: dict | None) -> dict | None:
    if not task:
        return task
    if task.get("link_urls"):
        return task
    product_id = int(task.get("product_id") or 0)
    lang = str(task.get("lang") or "").strip().lower()
    try:
        product = medias.get_product(product_id) or {}
    except Exception:
        product = {}
    if not product:
        product = {
            "id": product_id,
            "product_code": task.get("product_code"),
        }
    link_urls = resolve_link_urls(product, lang)
    enriched = dict(task)
    enriched["link_urls"] = link_urls
    if not enriched.get("link_url") and link_urls:
        enriched["link_url"] = link_urls[0]["url"]
    return enriched


def evaluate_candidate(product_id: int, lang: str) -> dict[str, Any]:
    normalized_lang = (lang or "").strip().lower()
    product = medias.get_product(product_id)
    if not product:
        return {"ready": False, "block_code": "product_not_found"}
    if normalized_lang == "en" or not medias.is_valid_language(normalized_lang):
        return {"ready": False, "block_code": "invalid_lang", "product": product}
    if not str(product.get("product_code") or "").strip():
        return {"ready": False, "block_code": "product_code_missing", "product": product}

    confirmed, _reason = is_confirmed_for_push(product, normalized_lang)
    if confirmed:
        return {"ready": False, "block_code": "already_confirmed", "product": product}

    shopify_product_id = medias.resolve_shopify_product_id(int(product_id))
    if not shopify_product_id:
        return {
            "ready": False,
            "block_code": "shopify_product_id_missing",
            "product": product,
        }

    if not medias.list_shopify_localizer_images(int(product_id), "en"):
        return {
            "ready": False,
            "block_code": "english_references_not_ready",
            "product": product,
            "shopify_product_id": shopify_product_id,
        }
    if not medias.list_shopify_localizer_images(int(product_id), normalized_lang):
        return {
            "ready": False,
            "block_code": "localized_images_not_ready",
            "product": product,
            "shopify_product_id": shopify_product_id,
        }

    link_urls = resolve_link_urls(product, normalized_lang)
    return {
        "ready": True,
        "product": product,
        "shopify_product_id": str(shopify_product_id).strip(),
        "link_url": link_urls[0]["url"] if link_urls else "",
        "link_urls": link_urls,
    }


def find_active_task(product_id: int, lang: str) -> dict | None:
    return query_one(
        "SELECT * FROM media_shopify_image_replace_tasks "
        "WHERE product_id=%s AND lang=%s AND status IN ('pending','running') "
        "ORDER BY id DESC LIMIT 1",
        (product_id, (lang or "").strip().lower()),
    )


def get_task(task_id: int) -> dict | None:
    return query_one(
        "SELECT * FROM media_shopify_image_replace_tasks WHERE id=%s",
        (task_id,),
    )


def create_or_reuse_task(product_id: int, lang: str) -> dict:
    normalized_lang = (lang or "").strip().lower()
    active = find_active_task(product_id, normalized_lang)
    if active:
        return _enrich_task_link_urls(active) or active

    candidate = evaluate_candidate(product_id, normalized_lang)
    if not candidate.get("ready"):
        update_enabled_domain_statuses(
            product_id,
            normalized_lang,
            replace_status=REPLACE_FAILED,
            link_status=LINK_NEEDS_REVIEW,
            last_error=candidate.get("block_code") or "not_ready",
        )
        return {"id": None, "status": TASK_BLOCKED, **candidate}

    product = candidate["product"]
    task_id = execute(
        "INSERT INTO media_shopify_image_replace_tasks "
        "(product_id, product_code, lang, shopify_product_id, link_url, status) "
        "VALUES (%s,%s,%s,%s,%s,'pending')",
        (
            product_id,
            product["product_code"],
            normalized_lang,
            candidate["shopify_product_id"],
            candidate["link_url"],
        ),
    )
    update_enabled_domain_statuses(
        product_id,
        normalized_lang,
        replace_status=REPLACE_PENDING,
        link_status=LINK_UNKNOWN,
        last_task_id=task_id,
        last_error="",
    )
    return _enrich_task_link_urls(get_task(task_id)) or {"id": task_id, "status": TASK_PENDING, **candidate}


def claim_next_task(worker_id: str, lock_seconds: int = 900) -> dict | None:
    rows = query(
        "SELECT * FROM media_shopify_image_replace_tasks "
        "WHERE status='pending' OR (status='running' AND locked_until < NOW()) "
        "ORDER BY id ASC LIMIT 1"
    )
    if not rows:
        return None
    task = rows[0]
    updated = execute(
        "UPDATE media_shopify_image_replace_tasks "
        "SET status='running', worker_id=%s, "
        "locked_until=DATE_ADD(NOW(), INTERVAL %s SECOND), "
        "claimed_at=COALESCE(claimed_at, NOW()), "
        "started_at=COALESCE(started_at, NOW()), "
        "attempt_count=attempt_count+1 "
        "WHERE id=%s AND (status='pending' OR locked_until < NOW())",
        (str(worker_id or "unknown-worker"), int(lock_seconds), task["id"]),
    )
    if not updated:
        return None
    update_enabled_domain_statuses(
        int(task["product_id"]),
        task["lang"],
        replace_status=REPLACE_RUNNING,
        link_status=LINK_NEEDS_REVIEW,
        last_task_id=task["id"],
    )
    return _enrich_task_link_urls(get_task(task["id"]) or task)


def summarize_result(result: dict[str, Any]) -> dict[str, Any]:
    domain_results = (result or {}).get("domain_results") or []
    if isinstance(domain_results, list) and domain_results:
        summary = {
            "carousel_requested": 0,
            "carousel_ok": 0,
            "carousel_skipped": 0,
            "detail_replacement_count": 0,
            "detail_skipped_existing_count": 0,
        }
        for row in domain_results:
            if not isinstance(row, dict):
                continue
            nested = summarize_result(row.get("result") or row)
            for key in summary:
                summary[key] += int(nested.get(key) or 0)
        return summary
    carousel = (result or {}).get("carousel") or {}
    detail = (result or {}).get("detail") or {}
    return {
        "carousel_requested": carousel.get("requested", 0),
        "carousel_ok": carousel.get("ok", 0),
        "carousel_skipped": carousel.get("skipped", 0),
        "detail_replacement_count": detail.get("replacement_count", 0),
        "detail_skipped_existing_count": detail.get("skipped_existing_count", 0),
    }


def complete_task(task_id: int, result: dict[str, Any]) -> dict[str, Any]:
    task = get_task(task_id)
    if not task:
        raise ValueError("task not found")
    payload = json.dumps(result or {}, ensure_ascii=False)
    execute(
        "UPDATE media_shopify_image_replace_tasks "
        "SET status='success', result_json=%s, error_code=NULL, error_message=NULL, "
        "finished_at=NOW(), locked_until=NULL "
        "WHERE id=%s",
        (payload, task_id),
    )
    return update_enabled_domain_statuses(
        int(task["product_id"]),
        task["lang"],
        replace_status=REPLACE_AUTO_DONE,
        link_status=LINK_NEEDS_REVIEW,
        last_task_id=task_id,
        last_error="",
        result_summary=summarize_result(result or {}),
    )


def fail_task(
    task_id: int,
    error_code: str,
    error_message: str,
    result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    task = get_task(task_id)
    if not task:
        raise ValueError("task not found")
    link_status = (
        LINK_UNAVAILABLE
        if str(error_code or "") in {"link_unavailable", "not_found"}
        else LINK_NEEDS_REVIEW
    )
    execute(
        "UPDATE media_shopify_image_replace_tasks "
        "SET status='failed', error_code=%s, error_message=%s, result_json=%s, "
        "finished_at=NOW(), locked_until=NULL "
        "WHERE id=%s",
        (
            str(error_code or "worker_failed"),
            str(error_message or ""),
            json.dumps(result or {}, ensure_ascii=False),
            task_id,
        ),
    )
    return update_enabled_domain_statuses(
        int(task["product_id"]),
        task["lang"],
        replace_status=REPLACE_FAILED,
        link_status=link_status,
        last_task_id=task_id,
        last_error=str(error_message or error_code or "worker_failed"),
    )


def heartbeat_task(task_id: int, worker_id: str, lock_seconds: int = 900) -> int:
    return execute(
        "UPDATE media_shopify_image_replace_tasks "
        "SET locked_until=DATE_ADD(NOW(), INTERVAL %s SECOND) "
        "WHERE id=%s AND status='running' AND worker_id=%s",
        (int(lock_seconds), task_id, str(worker_id or "")),
    )


def is_confirmed_for_push(product: dict | None, lang: str) -> tuple[bool, str]:
    normalized_lang = (lang or "en").strip().lower() or "en"
    if normalized_lang == "en":
        return True, ""

    status_map = parse_status_map((product or {}).get("shopify_image_status_json"))
    link_rows = resolve_link_urls(product or {}, normalized_lang)
    if not link_rows:
        link_rows = [{"domain": "", "status_key": normalized_lang}]
    statuses = [
        (
            row,
            status_for_lang(
                status_map,
                normalized_lang,
                row.get("domain") or None,
                fallback_legacy=(row.get("domain") == product_link_domains.DEFAULT_LINK_DOMAINS[0]),
            ),
        )
        for row in link_rows
    ]
    not_ready: tuple[dict[str, str], dict[str, Any]] | None = None
    for row, status in statuses:
        if status["replace_status"] == REPLACE_CONFIRMED and status["link_status"] == LINK_NORMAL:
            continue
        not_ready = (row, status)
        break
    if not not_ready:
        return True, ""

    row, status = not_ready
    domain_label = row.get("domain") or status.get("domain") or ""
    prefix = f"{domain_label} " if domain_label else ""
    replace_status = status["replace_status"]
    link_status = status["link_status"]

    if link_status == LINK_UNAVAILABLE:
        return False, prefix + (status["last_error"] or "链接不可用，已阻止推送")
    if replace_status == REPLACE_FAILED:
        return False, prefix + (status["last_error"] or "图片自动替换失败，需要处理")
    if replace_status == REPLACE_AUTO_DONE:
        return False, prefix + "图片已自动替换，等待人工确认"
    if replace_status == REPLACE_RUNNING:
        return False, prefix + "图片正在自动替换，暂不可推送"
    if replace_status == REPLACE_PENDING:
        return False, prefix + "图片替换任务已排队，暂不可推送"
    return False, prefix + "图片尚未完成替换确认"
