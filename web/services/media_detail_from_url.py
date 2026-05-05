"""Request planning for detail-image imports from product URLs."""

from __future__ import annotations

import json
import requests
from collections.abc import Callable, Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class DetailImagesFromUrlPlan:
    lang: str
    url: str
    clear_existing: bool


@dataclass(frozen=True)
class DetailImagesFromUrlPlanOutcome:
    plan: DetailImagesFromUrlPlan | None = None
    error: str | None = None
    status_code: int = 200


@dataclass(frozen=True)
class DetailImagesFromUrlResponse:
    payload: dict
    status_code: int = 200


@dataclass(frozen=True)
class DetailImagesFromUrlStatusResponse:
    payload: dict
    status_code: int = 200


def build_detail_images_from_url_plan(
    product: Mapping[str, object],
    body: Mapping[str, object],
    *,
    is_valid_language: Callable[[str], bool],
) -> DetailImagesFromUrlPlanOutcome:
    lang = str(body.get("lang") or "en").strip().lower()
    if not is_valid_language(lang):
        return DetailImagesFromUrlPlanOutcome(error=f"unsupported language: {lang}", status_code=400)

    url = str(body.get("url") or "").strip()
    if not url:
        url = _localized_link(product.get("localized_links_json"), lang)
    if not url:
        code = str(product.get("product_code") or "").strip()
        if not code:
            return DetailImagesFromUrlPlanOutcome(
                error="product_code required before inferring a default link",
                status_code=400,
            )
        url = (
            f"https://newjoyloo.com/products/{code}"
            if lang == "en"
            else f"https://newjoyloo.com/{lang}/products/{code}"
        )

    return DetailImagesFromUrlPlanOutcome(
        plan=DetailImagesFromUrlPlan(
            lang=lang,
            url=url,
            clear_existing=bool(body.get("clear_existing")),
        )
    )


def _localized_link(raw_links: object, lang: str) -> str:
    links: dict = {}
    if isinstance(raw_links, dict):
        links = raw_links
    elif isinstance(raw_links, str):
        try:
            parsed = json.loads(raw_links)
        except (json.JSONDecodeError, ValueError, TypeError):
            parsed = {}
        if isinstance(parsed, dict):
            links = parsed
    return str(links.get(lang) or "").strip()


def build_detail_images_from_url_response(
    product_id: int,
    user_id: int,
    product: Mapping[str, object],
    body: Mapping[str, object] | None,
    *,
    is_valid_language_fn: Callable[[str], bool],
    create_fetch_task_fn: Callable[..., str],
    fetch_page_fn: Callable[[str, str], object],
    download_image_to_local_media_fn: Callable[..., tuple[str | None, bytes | None, str]],
    soft_delete_detail_images_by_lang_fn: Callable[[int, str], int],
    detail_image_empty_counts_fn: Callable[[], dict],
    detail_image_existing_counts_fn: Callable[[int, str], dict],
    detail_image_kind_from_download_ext_fn: Callable[[str], str],
    detail_image_limits: Mapping[str, int],
    detail_image_kind_labels: Mapping[str, str],
    add_detail_image_fn: Callable[..., int],
    get_detail_image_fn: Callable[[int], Mapping[str, object] | None],
    serialize_detail_image_fn: Callable[[Mapping[str, object]], dict],
    max_download_candidates: int,
) -> DetailImagesFromUrlResponse:
    body = body or {}
    plan_outcome = build_detail_images_from_url_plan(
        product,
        body,
        is_valid_language=is_valid_language_fn,
    )
    if plan_outcome.error or plan_outcome.plan is None:
        return DetailImagesFromUrlResponse(
            {"error": plan_outcome.error},
            plan_outcome.status_code,
        )

    plan = plan_outcome.plan
    worker = build_detail_images_from_url_worker(
        product_id,
        user_id,
        product,
        plan,
        fetch_page_fn=fetch_page_fn,
        download_image_to_local_media_fn=download_image_to_local_media_fn,
        soft_delete_detail_images_by_lang_fn=soft_delete_detail_images_by_lang_fn,
        detail_image_empty_counts_fn=detail_image_empty_counts_fn,
        detail_image_existing_counts_fn=detail_image_existing_counts_fn,
        detail_image_kind_from_download_ext_fn=detail_image_kind_from_download_ext_fn,
        detail_image_limits=detail_image_limits,
        detail_image_kind_labels=detail_image_kind_labels,
        add_detail_image_fn=add_detail_image_fn,
        get_detail_image_fn=get_detail_image_fn,
        serialize_detail_image_fn=serialize_detail_image_fn,
        max_download_candidates=max_download_candidates,
    )
    task_id = create_fetch_task_fn(
        user_id=user_id,
        product_id=product_id,
        url=plan.url,
        lang=plan.lang,
        worker=worker,
    )
    return DetailImagesFromUrlResponse({"task_id": task_id, "url": plan.url}, 202)


def build_detail_images_from_url_status_response(
    product_id: int,
    task_id: str,
    user_id: int,
    *,
    get_fetch_task_fn: Callable[..., Mapping[str, object] | None],
) -> DetailImagesFromUrlStatusResponse:
    task = get_fetch_task_fn(task_id, user_id=int(user_id))
    if not task or task.get("product_id") != int(product_id):
        return DetailImagesFromUrlStatusResponse({"error": "task not found"}, 404)
    return DetailImagesFromUrlStatusResponse(dict(task))


def build_detail_images_from_url_worker(
    product_id: int,
    user_id: int,
    product: Mapping[str, object],
    plan: DetailImagesFromUrlPlan,
    *,
    fetch_page_fn: Callable[[str, str], object],
    download_image_to_local_media_fn: Callable[..., tuple[str | None, bytes | None, str]],
    soft_delete_detail_images_by_lang_fn: Callable[[int, str], int],
    detail_image_empty_counts_fn: Callable[[], dict],
    detail_image_existing_counts_fn: Callable[[int, str], dict],
    detail_image_kind_from_download_ext_fn: Callable[[str], str],
    detail_image_limits: Mapping[str, int],
    detail_image_kind_labels: Mapping[str, str],
    add_detail_image_fn: Callable[..., int],
    get_detail_image_fn: Callable[[int], Mapping[str, object] | None],
    serialize_detail_image_fn: Callable[[Mapping[str, object]], dict],
    max_download_candidates: int,
) -> Callable[[str, Callable[..., None]], None]:
    url = plan.url
    lang = plan.lang
    clear_existing = plan.clear_existing

    def _worker(task_id: str, update: Callable[..., None]) -> None:
        del task_id
        update(status="fetching", message=f"fetching page {url}")
        try:
            page = fetch_page_fn(url, lang)
        except Exception as exc:
            if exc.__class__.__name__ == "LocaleLockError":
                update(status="failed", error=str(exc), message=f"locale lock failed: {exc}")
                return
            if isinstance(exc, requests.HTTPError):
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status == 404:
                    update(
                        status="failed",
                        error=f"link returned 404: {url}",
                        message=(
                            f"link returned 404: {url}\n"
                            f"product_code={product.get('product_code')} may not match the storefront handle.\n"
                            "Please fill a real product link and retry."
                        ),
                    )
                else:
                    update(status="failed", error=f"HTTP {status}", message=f"fetch failed: HTTP {status}")
                return
            if isinstance(exc, requests.RequestException):
                update(status="failed", error=str(exc), message=f"fetch failed: {exc}")
                return
            raise

        images = list(getattr(page, "images", None) or [])
        if not images:
            update(
                status="failed",
                error="no images found",
                message="no carousel/detail images detected on the page",
            )
            return
        if len(images) > max_download_candidates:
            images = images[:max_download_candidates]

        if clear_existing:
            try:
                cleared = soft_delete_detail_images_by_lang_fn(product_id, lang)
            except Exception as exc:
                update(
                    status="failed",
                    error=str(exc),
                    message=f"failed to clear existing detail images: {exc}",
                )
                return
            limit_counts = dict(detail_image_empty_counts_fn())
            update(
                status="downloading",
                total=len(images),
                message=(
                    f"cleared {cleared} existing detail images; "
                    f"found {len(images)} images, starting download"
                ),
            )
        else:
            limit_counts = dict(detail_image_existing_counts_fn(product_id, lang))
            update(
                status="downloading",
                total=len(images),
                message=f"found {len(images)} images, starting download",
            )

        created: list[dict] = []
        errors: list[str] = []
        for idx, image in enumerate(images):
            src = _image_source_url(image)
            update(
                progress=idx,
                current_url=src,
                message=f"downloading image {idx + 1}/{len(images)}",
            )
            filename = f"from_url_{lang}_{idx:02d}"
            try:
                object_key, data, ext = download_image_to_local_media_fn(
                    src,
                    product_id,
                    filename,
                    user_id=user_id,
                )
                if ext and not object_key:
                    errors.append(f"{src}: {ext}")
                    continue

                kind = detail_image_kind_from_download_ext_fn(ext)
                current_count = int(limit_counts.get(kind) or 0)
                if current_count >= int(detail_image_limits[kind]):
                    errors.append(
                        f"{src}: skipped, {detail_image_kind_labels[kind]} limit reached "
                        f"(max {detail_image_limits[kind]})"
                    )
                    continue

                new_id = add_detail_image_fn(
                    product_id,
                    lang,
                    object_key,
                    content_type=None,
                    file_size=len(data) if data else None,
                    origin_type="from_url",
                )
                limit_counts[kind] = current_count + 1
                row = get_detail_image_fn(new_id)
                if row:
                    created.append(serialize_detail_image_fn(row))
            except Exception as exc:
                errors.append(f"{src}: {exc}")

        update(
            status="done",
            progress=len(images),
            inserted=created,
            errors=errors,
            current_url="",
            message=(
                f"done: detected {len(images)} images, inserted {len(created)}"
                + (f", failed {len(errors)}" if errors else "")
            ),
        )

    return _worker


def _image_source_url(image: object) -> str:
    if isinstance(image, Mapping):
        return str(image.get("source_url") or "")
    return ""
