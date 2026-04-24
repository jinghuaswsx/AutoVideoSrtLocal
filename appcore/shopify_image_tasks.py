"""Shopify 图片替换任务中心的状态与队列服务。"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any

from appcore import medias
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
) -> dict[str, Any]:
    normalized_lang = (lang or "").strip().lower()
    raw = dict((status_map or {}).get(normalized_lang) or {})
    return {
        "replace_status": raw.get("replace_status") or REPLACE_NONE,
        "link_status": raw.get("link_status") or LINK_UNKNOWN,
        "last_task_id": raw.get("last_task_id"),
        "last_error": raw.get("last_error") or "",
        "result_summary": raw.get("result_summary") or {},
        "confirmed_by": raw.get("confirmed_by"),
        "confirmed_at": raw.get("confirmed_at"),
        "updated_at": raw.get("updated_at"),
    }


def update_lang_status(product_id: int, lang: str, **updates: Any) -> dict[str, Any]:
    product = medias.get_product(product_id) or {}
    status_map = parse_status_map(product.get("shopify_image_status_json"))
    normalized_lang = (lang or "").strip().lower()
    current = status_for_lang(status_map, normalized_lang)
    current.update(updates)
    current["updated_at"] = _now_iso()
    status_map[normalized_lang] = current
    medias.update_product(product_id, shopify_image_status_json=status_map)
    return current


def confirm_lang(product_id: int, lang: str, user_id: int | None = None) -> dict[str, Any]:
    return update_lang_status(
        product_id,
        lang,
        replace_status=REPLACE_CONFIRMED,
        link_status=LINK_NORMAL,
        confirmed_by=user_id,
        confirmed_at=_now_iso(),
        last_error="",
    )


def mark_link_unavailable(product_id: int, lang: str, reason: str = "") -> dict[str, Any]:
    return update_lang_status(
        product_id,
        lang,
        replace_status=REPLACE_FAILED,
        link_status=LINK_UNAVAILABLE,
        last_error=(reason or "链接不可用，等待负责人处理"),
    )


def reset_lang(product_id: int, lang: str) -> dict[str, Any]:
    return update_lang_status(
        product_id,
        lang,
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


def resolve_link_url(product: dict, lang: str) -> str:
    normalized_lang = (lang or "en").strip().lower() or "en"
    links = _loads_product_links(product or {})
    if links.get(normalized_lang):
        return links[normalized_lang]
    product_code = str((product or {}).get("product_code") or "").strip()
    if normalized_lang == "en":
        return f"https://newjoyloo.com/products/{product_code}"
    return f"https://newjoyloo.com/{normalized_lang}/products/{product_code}"


def evaluate_candidate(product_id: int, lang: str) -> dict[str, Any]:
    normalized_lang = (lang or "").strip().lower()
    product = medias.get_product(product_id)
    if not product:
        return {"ready": False, "block_code": "product_not_found"}
    if normalized_lang == "en" or not medias.is_valid_language(normalized_lang):
        return {"ready": False, "block_code": "invalid_lang", "product": product}
    if not str(product.get("product_code") or "").strip():
        return {"ready": False, "block_code": "product_code_missing", "product": product}

    current = status_for_lang(
        parse_status_map(product.get("shopify_image_status_json")),
        normalized_lang,
    )
    if current["replace_status"] == REPLACE_CONFIRMED and current["link_status"] == LINK_NORMAL:
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

    return {
        "ready": True,
        "product": product,
        "shopify_product_id": str(shopify_product_id).strip(),
        "link_url": resolve_link_url(product, normalized_lang),
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
        return active

    candidate = evaluate_candidate(product_id, normalized_lang)
    if not candidate.get("ready"):
        update_lang_status(
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
    update_lang_status(
        product_id,
        normalized_lang,
        replace_status=REPLACE_PENDING,
        link_status=LINK_UNKNOWN,
        last_task_id=task_id,
        last_error="",
    )
    return get_task(task_id) or {"id": task_id, "status": TASK_PENDING, **candidate}


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
    update_lang_status(
        int(task["product_id"]),
        task["lang"],
        replace_status=REPLACE_RUNNING,
        link_status=LINK_NEEDS_REVIEW,
        last_task_id=task["id"],
    )
    return get_task(task["id"]) or task


def summarize_result(result: dict[str, Any]) -> dict[str, Any]:
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
    return update_lang_status(
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
    return update_lang_status(
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

    status = status_for_lang(
        parse_status_map((product or {}).get("shopify_image_status_json")),
        normalized_lang,
    )
    replace_status = status["replace_status"]
    link_status = status["link_status"]

    if replace_status == REPLACE_CONFIRMED and link_status == LINK_NORMAL:
        return True, ""
    if link_status == LINK_UNAVAILABLE:
        return False, status["last_error"] or "链接不可用，已阻止推送"
    if replace_status == REPLACE_FAILED:
        return False, status["last_error"] or "图片自动替换失败，需要处理"
    if replace_status == REPLACE_AUTO_DONE:
        return False, "图片已自动替换，等待人工确认"
    if replace_status == REPLACE_RUNNING:
        return False, "图片正在自动替换，暂不可推送"
    if replace_status == REPLACE_PENDING:
        return False, "图片替换任务已排队，暂不可推送"
    return False, "图片尚未完成替换确认"
