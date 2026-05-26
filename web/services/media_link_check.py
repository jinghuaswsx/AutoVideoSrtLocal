"""Service response builders for product material link-check routes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable
import uuid

from flask import jsonify

from appcore import link_availability, medias, product_link_domains


@dataclass(frozen=True)
class MediaLinkCheckResponse:
    payload: dict
    status_code: int


def media_link_check_flask_response(result: MediaLinkCheckResponse):
    return jsonify(result.payload), result.status_code


def _collect_link_check_reference_images(
    product_id: int,
    lang: str,
    task_dir: Path,
    *,
    download_media_object_fn: Callable[[str, Path], Any],
) -> list[dict]:
    references: list[dict] = []
    ref_dir = task_dir / "reference"
    ref_dir.mkdir(parents=True, exist_ok=True)

    cover_key = medias.get_product_covers(product_id).get(lang)
    if cover_key:
        cover_suffix = Path(cover_key).suffix or ".jpg"
        cover_local = ref_dir / f"cover_{lang}{cover_suffix}"
        download_media_object_fn(cover_key, cover_local)
        references.append(
            {
                "id": f"cover-{lang}",
                "filename": f"cover_{lang}{cover_suffix}",
                "local_path": str(cover_local),
            }
        )

    for idx, row in enumerate(medias.list_detail_images(product_id, lang), start=1):
        object_key = row.get("object_key") or ""
        detail_suffix = Path(object_key).suffix or ".jpg"
        detail_local = ref_dir / f"detail_{idx:03d}{detail_suffix}"
        download_media_object_fn(object_key, detail_local)
        references.append(
            {
                "id": f"detail-{row['id']}",
                "filename": f"detail_{idx:03d}{detail_suffix}",
                "local_path": str(detail_local),
            }
        )

    return references


def _collect_link_check_original_images(
    product_id: int,
    task_dir: Path,
    *,
    download_media_object_fn: Callable[[str, Path], Any],
) -> list[dict]:
    originals: list[dict] = []
    orig_dir = task_dir / "original"
    orig_dir.mkdir(parents=True, exist_ok=True)

    cover_key = medias.get_product_covers(product_id).get("en")
    if cover_key:
        cover_suffix = Path(cover_key).suffix or ".jpg"
        cover_local = orig_dir / f"cover_en{cover_suffix}"
        download_media_object_fn(cover_key, cover_local)
        originals.append(
            {
                "id": "cover-en",
                "filename": f"cover_en{cover_suffix}",
                "local_path": str(cover_local),
            }
        )

    for idx, row in enumerate(medias.list_detail_images(product_id, "en"), start=1):
        object_key = row.get("object_key") or ""
        detail_suffix = Path(object_key).suffix or ".jpg"
        detail_local = orig_dir / f"detail_{idx:03d}{detail_suffix}"
        download_media_object_fn(object_key, detail_local)
        originals.append(
            {
                "id": f"detail-{row['id']}",
                "filename": f"detail_{idx:03d}{detail_suffix}",
                "local_path": str(detail_local),
            }
        )

    return originals



def _link_check_domain_metadata(link_url: str, lang: str) -> tuple[str, str, str]:
    domain = product_link_domains.domain_from_url(link_url)
    status_key = product_link_domains.domain_lang_key(domain, lang) if domain else lang
    storage_key = (
        status_key
        if domain and domain != product_link_domains.DEFAULT_LINK_DOMAINS[0]
        else lang
    )
    return domain, status_key, storage_key


def _task_key(lang: str, domain: str | None) -> str:
    normalized_domain = product_link_domains.domain_from_url(domain or "")
    if normalized_domain and normalized_domain != product_link_domains.DEFAULT_LINK_DOMAINS[0]:
        return product_link_domains.domain_lang_key(normalized_domain, lang)
    return lang


def build_product_link_check_create_response(
    *,
    product_id: int,
    body: dict | None,
    user_id: int,
    output_dir: str | Path,
    store_obj: Any,
    start_runner_fn: Callable[[str], Any],
    download_media_object_fn: Callable[[str, Path], Any],
    task_id_factory: Callable[[], Any] = uuid.uuid4,
    now_fn: Callable[[Any], datetime] = datetime.now,
) -> MediaLinkCheckResponse:
    body = body if isinstance(body, dict) else {}
    lang = (body.get("lang") or "").strip().lower()
    if not lang or not medias.is_valid_language(lang):
        return MediaLinkCheckResponse({"error": f"unsupported language: {lang}"}, 400)

    link_url = (body.get("link_url") or "").strip()
    if not link_url.startswith(("http://", "https://")):
        return MediaLinkCheckResponse({"error": "valid product link_url required"}, 400)

    language = medias.get_language(lang)
    if not language or not language.get("enabled"):
        return MediaLinkCheckResponse({"error": "target language is invalid"}, 400)

    task_id = str(task_id_factory())
    domain, status_key, storage_key = _link_check_domain_metadata(link_url, lang)
    task_dir = Path(output_dir) / "link_check" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    references = _collect_link_check_reference_images(
        product_id,
        lang,
        task_dir,
        download_media_object_fn=download_media_object_fn,
    )
    if not references:
        return MediaLinkCheckResponse({"error": "当前语种缺少参考图"}, 400)

    original_images = []
    if lang != "en":
        # 仅当产品确实配置了 en 主图或 en 详情图时才提取英文原图比较参考
        has_en_content = bool(medias.get_product_covers(product_id).get("en")) or \
                         any(row.get("lang") == "en" for row in medias.list_detail_images(product_id, "en"))
        if has_en_content:
            original_images = _collect_link_check_original_images(
                product_id,
                task_dir,
                download_media_object_fn=download_media_object_fn,
            )

    store_obj.create_link_check(
        task_id,
        str(task_dir),
        user_id=user_id,
        link_url=link_url,
        target_language=lang,
        target_language_name=language.get("name_zh") or lang,
        domain=domain,
        status_key=status_key,
        reference_images=references,
    )
    if original_images and hasattr(store_obj, "update"):
        store_obj.update(task_id, original_images=original_images)
    medias.set_product_link_check_task(
        product_id,
        storage_key,
        {
            "task_id": task_id,
            "status": "queued",
            "link_url": link_url,
            "domain": domain,
            "status_key": status_key,
            "checked_at": now_fn(UTC).isoformat(),
            "summary": {
                "overall_decision": "running",
                "pass_count": 0,
                "replace_count": 0,
                "review_count": 0,
            },
        },
    )
    start_runner_fn(task_id)
    if hasattr(store_obj, "update"):
        store_obj.update(task_id, product_id=product_id)
    payload = {"task_id": task_id, "status": "queued", "reference_count": len(references)}
    if storage_key != lang:
        payload.update({"domain": domain, "status_key": status_key})
    return MediaLinkCheckResponse(
        payload,
        202,
    )


def build_product_link_check_summary_response(
    *,
    product: dict,
    lang: str,
    user_id: int,
    store_obj: Any,
    domain: str | None = None,
) -> MediaLinkCheckResponse:
    if not medias.is_valid_language(lang):
        return MediaLinkCheckResponse({"error": f"不支持的语言: {lang}"}, 400)

    tasks = medias.parse_link_check_tasks_json(product.get("link_check_tasks_json"))
    storage_key = _task_key(lang, domain)
    meta = tasks.get(storage_key) or (tasks.get(lang) if storage_key != lang else None)
    if not meta:
        return MediaLinkCheckResponse({"task": None}, 200)

    task = store_obj.get(meta.get("task_id", ""))
    if not task or task.get("_user_id") != user_id or task.get("type") != "link_check":
        return MediaLinkCheckResponse({"task": None}, 200)

    refreshed = {
        "task_id": meta.get("task_id", ""),
        "status": task.get("status", meta.get("status", "")),
        "link_url": meta.get("link_url", ""),
        "domain": meta.get("domain") or product_link_domains.domain_from_url(meta.get("link_url", "")),
        "status_key": meta.get("status_key") or storage_key,
        "checked_at": meta.get("checked_at", ""),
        "summary": dict(task.get("summary") or meta.get("summary") or {}),
        "progress": dict(task.get("progress") or {}),
        "steps": dict(task.get("steps") or {}),
        "step_messages": dict(task.get("step_messages") or {}),
        "has_detail": True,
        "resolved_url": task.get("resolved_url", ""),
        "page_language": task.get("page_language", ""),
    }
    medias.set_product_link_check_task(
        int(product.get("id") or 0),
        storage_key,
        {
            "task_id": refreshed["task_id"],
            "status": refreshed["status"],
            "link_url": refreshed["link_url"],
            "domain": refreshed["domain"],
            "status_key": refreshed["status_key"],
            "checked_at": refreshed["checked_at"],
            "summary": refreshed["summary"],
        },
    )
    return MediaLinkCheckResponse({"task": refreshed}, 200)


def build_product_link_check_detail_response(
    *,
    product: dict,
    lang: str,
    user_id: int,
    store_obj: Any,
    serialize_task_fn: Callable[[dict], dict],
    domain: str | None = None,
) -> MediaLinkCheckResponse:
    if not medias.is_valid_language(lang):
        return MediaLinkCheckResponse({"error": f"不支持的语言: {lang}"}, 400)

    tasks = medias.parse_link_check_tasks_json(product.get("link_check_tasks_json"))
    storage_key = _task_key(lang, domain)
    meta = tasks.get(storage_key) or (tasks.get(lang) if storage_key != lang else None)
    if not meta:
        return MediaLinkCheckResponse({"error": "task not found"}, 404)

    task = store_obj.get(meta.get("task_id", ""))
    if not task or task.get("_user_id") != user_id or task.get("type") != "link_check":
        return MediaLinkCheckResponse({"error": "task not found"}, 404)
    return MediaLinkCheckResponse(serialize_task_fn(task), 200)


def _availability_payload(
    product: dict,
    lang: str,
    items: list[dict],
) -> dict[str, Any]:
    return {
        "product_id": int(product.get("id") or 0),
        "lang": lang,
        "items": items,
    }


def _resolve_lang_or_error(lang: str) -> tuple[str, MediaLinkCheckResponse | None]:
    lang_code = (lang or "").strip().lower()
    if not lang_code or not medias.is_valid_language(lang_code):
        return "", MediaLinkCheckResponse({"error": f"不支持的语言: {lang}"}, 400)
    return lang_code, None


def build_product_link_availability_get_response(
    *,
    product: dict,
    lang: str,
    list_results_fn: Callable[[int, str], list[dict]] | None = None,
) -> MediaLinkCheckResponse:
    lang_code, err = _resolve_lang_or_error(lang)
    if err is not None:
        return err

    list_fn = list_results_fn or link_availability.list_results
    rows = product_link_domains.resolve_product_page_url_rows(product, lang_code)
    by_domain = {row["domain"]: row for row in rows}

    cached = list_fn(int(product.get("id") or 0), lang_code) or []
    cached_by_domain = {item["domain"]: item for item in cached}

    items: list[dict[str, Any]] = []
    for row in rows:
        cached_item = cached_by_domain.get(row["domain"])
        if cached_item:
            # Update link_url to current resolved value if it has changed.
            cached_item["link_url"] = row["url"]
            items.append(cached_item)
        else:
            items.append({
                "product_id": int(product.get("id") or 0),
                "lang": lang_code,
                "domain": row["domain"],
                "link_url": row["url"],
                "http_status": None,
                "ok": False,
                "error": None,
                "elapsed_ms": None,
                "checked_at": "",
            })

    # Surface any persisted rows whose domain is no longer enabled, so the
    # user can see stale leftovers (and they remain queryable for audits).
    for cached_item in cached:
        if cached_item["domain"] not in by_domain:
            items.append({**cached_item, "stale": True})

    return MediaLinkCheckResponse(_availability_payload(product, lang_code, items), 200)


def build_product_link_availability_run_response(
    *,
    product: dict,
    lang: str,
    body: dict | None,
    probe_and_record_fn: Callable[..., list[dict]] | None = None,
    list_results_fn: Callable[[int, str], list[dict]] | None = None,
) -> MediaLinkCheckResponse:
    lang_code, err = _resolve_lang_or_error(lang)
    if err is not None:
        return err

    probe_fn = probe_and_record_fn or link_availability.probe_and_record
    list_fn = list_results_fn or link_availability.list_results

    body = body if isinstance(body, dict) else {}
    only_domain = (body.get("domain") or "").strip().lower()

    rows = product_link_domains.resolve_product_page_url_rows(product, lang_code)
    if only_domain:
        try:
            normalized = product_link_domains.normalize_domain(only_domain)
        except ValueError:
            return MediaLinkCheckResponse({"error": "invalid domain"}, 400)
        rows = [row for row in rows if row["domain"] == normalized]
        if not rows:
            return MediaLinkCheckResponse(
                {"error": "domain not enabled for product"}, 404
            )

    targets = [{"domain": row["domain"], "url": row["url"]} for row in rows]
    if not targets:
        return MediaLinkCheckResponse(
            _availability_payload(product, lang_code, []), 200
        )

    manual_confirm = body.get("manual_confirm") is True
    manual_abnormal = body.get("manual_abnormal") is True
    if manual_confirm or manual_abnormal:
        if not only_domain:
            return MediaLinkCheckResponse({"error": "domain required"}, 400)
        target = targets[0]
        if manual_confirm:
            link_availability.manual_confirm_result(
                product_id=int(product.get("id") or 0),
                lang=lang_code,
                domain=target["domain"],
                link_url=target["url"],
            )
        else:
            link_availability.manual_abnormal_result(
                product_id=int(product.get("id") or 0),
                lang=lang_code,
                domain=target["domain"],
                link_url=target["url"],
            )
        cached = list_fn(int(product.get("id") or 0), lang_code) or []
        cached_by_domain = {item["domain"]: item for item in cached}
        items: list[dict[str, Any]] = []
        for row in product_link_domains.resolve_product_page_url_rows(product, lang_code):
            item = cached_by_domain.get(row["domain"])
            if item:
                item["link_url"] = row["url"]
                items.append(item)
            else:
                items.append({
                    "product_id": int(product.get("id") or 0),
                    "lang": lang_code,
                    "domain": row["domain"],
                    "link_url": row["url"],
                    "http_status": None,
                    "ok": False,
                    "error": None,
                    "elapsed_ms": None,
                    "checked_at": "",
                })
        return MediaLinkCheckResponse(
            _availability_payload(product, lang_code, items), 200
        )

    probed = probe_fn(
        product_id=int(product.get("id") or 0),
        lang=lang_code,
        rows=targets,
    )

    # If a single domain was probed, merge with the cached list for the rest.
    if only_domain:
        cached = list_fn(int(product.get("id") or 0), lang_code) or []
        by_domain = {item["domain"]: item for item in cached}
        for item in probed:
            by_domain[item["domain"]] = item
        merged = [by_domain[row["domain"]] for row in product_link_domains.resolve_product_page_url_rows(product, lang_code) if row["domain"] in by_domain]
        return MediaLinkCheckResponse(
            _availability_payload(product, lang_code, merged), 200
        )

    return MediaLinkCheckResponse(_availability_payload(product, lang_code, probed), 200)
