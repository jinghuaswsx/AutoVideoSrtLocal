"""Service response builders for product material link-check routes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable
import uuid

from appcore import medias


@dataclass(frozen=True)
class MediaLinkCheckResponse:
    payload: dict
    status_code: int


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

    store_obj.create_link_check(
        task_id,
        str(task_dir),
        user_id=user_id,
        link_url=link_url,
        target_language=lang,
        target_language_name=language.get("name_zh") or lang,
        reference_images=references,
    )
    medias.set_product_link_check_task(
        product_id,
        lang,
        {
            "task_id": task_id,
            "status": "queued",
            "link_url": link_url,
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
    return MediaLinkCheckResponse(
        {"task_id": task_id, "status": "queued", "reference_count": len(references)},
        202,
    )


def build_product_link_check_summary_response(
    *,
    product: dict,
    lang: str,
    user_id: int,
    store_obj: Any,
) -> MediaLinkCheckResponse:
    if not medias.is_valid_language(lang):
        return MediaLinkCheckResponse({"error": f"不支持的语言: {lang}"}, 400)

    tasks = medias.parse_link_check_tasks_json(product.get("link_check_tasks_json"))
    meta = tasks.get(lang)
    if not meta:
        return MediaLinkCheckResponse({"task": None}, 200)

    task = store_obj.get(meta.get("task_id", ""))
    if not task or task.get("_user_id") != user_id or task.get("type") != "link_check":
        return MediaLinkCheckResponse({"task": None}, 200)

    refreshed = {
        "task_id": meta.get("task_id", ""),
        "status": task.get("status", meta.get("status", "")),
        "link_url": meta.get("link_url", ""),
        "checked_at": meta.get("checked_at", ""),
        "summary": dict(task.get("summary") or meta.get("summary") or {}),
        "progress": dict(task.get("progress") or {}),
        "has_detail": True,
        "resolved_url": task.get("resolved_url", ""),
        "page_language": task.get("page_language", ""),
    }
    medias.set_product_link_check_task(
        int(product.get("id") or 0),
        lang,
        {
            "task_id": refreshed["task_id"],
            "status": refreshed["status"],
            "link_url": refreshed["link_url"],
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
) -> MediaLinkCheckResponse:
    if not medias.is_valid_language(lang):
        return MediaLinkCheckResponse({"error": f"不支持的语言: {lang}"}, 400)

    tasks = medias.parse_link_check_tasks_json(product.get("link_check_tasks_json"))
    meta = tasks.get(lang)
    if not meta:
        return MediaLinkCheckResponse({"error": "task not found"}, 404)

    task = store_obj.get(meta.get("task_id", ""))
    if not task or task.get("_user_id") != user_id or task.get("type") != "link_check":
        return MediaLinkCheckResponse({"error": "task not found"}, 404)
    return MediaLinkCheckResponse(serialize_task_fn(task), 200)
