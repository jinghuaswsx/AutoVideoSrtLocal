from __future__ import annotations

"""Run carousel and detail-image replacement for one Shopify product.

This is the production batch path:
1. fetch localized material from the production bootstrap API;
2. replace carousel images in EZ Product Image Translate;
3. replace detail-description images in Translate & Adapt by updating the
   whole translated body_html value in one save.
"""

import argparse
import json
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from appcore.payment_screenshot_filter import is_payment_screenshot
from link_check_desktop.image_compare import find_best_reference, run_binary_quick_check
from playwright.sync_api import sync_playwright

from tools.shopify_image_localizer import api_client, cancellation, downloader, locales, settings, storage
from tools.shopify_image_localizer.browser import session
from tools.shopify_image_localizer.rpa import ez_cdp, taa_cdp


DEFAULT_STORE_DOMAIN = "newjoyloo.com"
LANGUAGE_LABELS = locales.ISO_TO_ENGLISH_NAME
VISUAL_MATCH_MIN_SCORE = 0.80
VisualPairConfirmCallback = Callable[[list[dict[str, Any]]], bool]


def _normalize_src(src: str) -> str:
    value = str(src or "").strip()
    if value.startswith("//"):
        return f"https:{value}"
    return value


def _storefront_json_url(product_code: str, *, locale: str = "", store_domain: str = DEFAULT_STORE_DOMAIN) -> str:
    normalized_locale = str(locale or "").strip().strip("/")
    prefix = f"/{normalized_locale}" if normalized_locale else ""
    return f"https://{store_domain}{prefix}/products/{product_code}.js"


def fetch_storefront_product(
    product_code: str,
    *,
    locale: str = "",
    store_domain: str = DEFAULT_STORE_DOMAIN,
    timeout_s: int = 20,
) -> dict[str, Any]:
    url = _storefront_json_url(product_code, locale=locale, store_domain=store_domain)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json,*/*"},
    )
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        payload = json.loads(response.read().decode("utf-8"))
    product = payload.get("product") if isinstance(payload, dict) and isinstance(payload.get("product"), dict) else payload
    if not isinstance(product, dict) or not product.get("id"):
        raise RuntimeError(f"failed to fetch storefront product JSON: {url}")
    return product


def product_image_sources(product: dict[str, Any]) -> list[str]:
    images = product.get("images") or []
    srcs: list[str] = []
    for image in images:
        if isinstance(image, str):
            src = image
        elif isinstance(image, dict):
            src = image.get("src") or image.get("url") or ""
        else:
            src = ""
        src = _normalize_src(src)
        if src:
            srcs.append(src)
    return srcs


def _localized_by_token(localized_images: list[dict]) -> dict[str, list[dict[str, Any]]]:
    return taa_cdp.build_localized_candidates(localized_images)


def _localized_by_source_index(localized_images: list[dict]) -> dict[int, list[dict[str, Any]]]:
    return taa_cdp.build_localized_candidates_by_source_index(localized_images)


def _choose_carousel_candidate(
    slot_idx: int,
    src: str,
    candidates_by_token: dict[str, list[dict[str, Any]]],
    candidates_by_source_index: dict[int, list[dict[str, Any]]] | None = None,
) -> dict[str, Any] | None:
    token = ez_cdp.md5_token(src)
    if token:
        candidates = candidates_by_token.get(token) or []
        if candidates:
            exact = [row for row in candidates if row.get("source_index") == slot_idx]
            if exact:
                return exact[0]
            if len(candidates) == 1:
                return candidates[0]
            no_index = [row for row in candidates if row.get("source_index") is None]
            if len(no_index) == 1:
                return no_index[0]
            options = [f"{row.get('source_index')}:{row.get('filename')}" for row in candidates]
            raise ValueError(f"ambiguous carousel source for slot {slot_idx} token {token}: {options}")
    source_index_candidates = (candidates_by_source_index or {}).get(slot_idx) or []
    if not source_index_candidates:
        return None
    src_key = taa_cdp.source_name_key(src)
    if src_key:
        exact_name = [row for row in source_index_candidates if row.get("source_name_key") == src_key]
        if exact_name:
            return exact_name[0]
    if len(source_index_candidates) == 1:
        return source_index_candidates[0]
    options = [str(row.get("filename") or "") for row in source_index_candidates]
    raise ValueError(f"ambiguous carousel source for slot {slot_idx}: {options}")


def pair_carousel_images(localized_images: list[dict], product_images: list[dict] | list[str]) -> list[tuple[int, str]]:
    candidates_by_token = _localized_by_token(localized_images)
    candidates_by_source_index = _localized_by_source_index(localized_images)
    pairs: list[tuple[int, str]] = []
    for idx, image in enumerate(product_images):
        if isinstance(image, str):
            src = image
        else:
            src = str(image.get("src") or image.get("url") or "")
        src = _normalize_src(src)
        if not src or src.lower().split("?", 1)[0].endswith(".gif"):
            continue
        candidate = _choose_carousel_candidate(idx, src, candidates_by_token, candidates_by_source_index)
        if candidate is None:
            continue
        pairs.append((idx, str(candidate["local_path"])))
    return pairs


def _image_row_id(row: dict[str, Any], fallback: str) -> str:
    return str(row.get("id") or row.get("filename") or row.get("local_path") or fallback)


def _local_image_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows or []:
        local_path = str(row.get("local_path") or "")
        if local_path and Path(local_path).is_file():
            result.append(row)
    return result


def _best_reference_for_image(
    image_path: str,
    reference_rows: list[dict[str, Any]],
    *,
    min_score: float,
) -> dict[str, Any] | None:
    reference_paths = [str(row.get("local_path") or "") for row in reference_rows if row.get("local_path")]
    if not reference_paths:
        return None
    best = find_best_reference(image_path, reference_paths)
    score = float(best.get("score") or 0.0)
    if best.get("status") != "matched" or score < min_score:
        return {
            "accepted": False,
            "compare": best,
            "reason": "visual score below threshold",
        }
    reference_by_path = {str(row.get("local_path") or ""): row for row in reference_rows}
    reference = reference_by_path.get(str(best.get("reference_path") or ""))
    if reference is None:
        return {
            "accepted": False,
            "compare": best,
            "reason": "matched reference row missing",
        }
    binary = run_binary_quick_check(image_path, str(reference.get("local_path") or ""))
    return {
        "accepted": True,
        "compare": best,
        "binary": binary,
        "reference": reference,
        "confidence": "high" if binary.get("status") == "pass" else "needs_review",
    }


def build_visual_carousel_pair_plan(
    *,
    slot_images: list[dict[str, Any]],
    reference_images: list[dict[str, Any]],
    localized_images: list[dict[str, Any]],
    min_score: float = VISUAL_MATCH_MIN_SCORE,
    reserved_localized_paths: set[str] | None = None,
) -> dict[str, Any]:
    reference_rows = _local_image_rows(reference_images)
    localized_rows = _local_image_rows(localized_images)
    reserved_paths = {str(path) for path in (reserved_localized_paths or set()) if path}
    review: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    pairs: list[tuple[int, str]] = []
    confirmation_pairs: list[dict[str, Any]] = []
    used_localized_paths: set[str] = set()

    if not reference_rows:
        return {
            "pairs": [],
            "confirmation_pairs": [],
            "review": [{"reason": "missing reference images"}],
            "conflicts": [],
        }

    localized_by_reference: dict[str, list[dict[str, Any]]] = {}
    for idx, row in enumerate(localized_rows):
        local_path = str(row.get("local_path") or "")
        if local_path in reserved_paths:
            continue
        match = _best_reference_for_image(local_path, reference_rows, min_score=min_score)
        if not match or not match.get("accepted"):
            review.append({
                "kind": "localized",
                "localized_id": _image_row_id(row, f"localized-{idx}"),
                "filename": row.get("filename"),
                "reason": (match or {}).get("reason") or "no reference matched",
                "compare": (match or {}).get("compare"),
                "binary": (match or {}).get("binary"),
            })
            continue
        reference = match["reference"]
        reference_id = _image_row_id(reference, str(match["compare"].get("reference_path") or "reference"))
        localized_by_reference.setdefault(reference_id, []).append({
            **row,
            "reference": reference,
            "visual_compare": match["compare"],
            "binary_check": match["binary"],
            "visual_score": float(match["compare"].get("score") or 0.0),
            "confidence": match.get("confidence") or "needs_review",
        })

    for idx, slot in enumerate(slot_images):
        local_path = str(slot.get("local_path") or "")
        slot_index = int(slot.get("slot_index") if slot.get("slot_index") is not None else idx)
        if not local_path or not Path(local_path).is_file():
            review.append({"kind": "slot", "slot_index": slot_index, "reason": "missing slot local image"})
            continue
        match = _best_reference_for_image(local_path, reference_rows, min_score=min_score)
        if not match or not match.get("accepted"):
            review.append({
                "kind": "slot",
                "slot_index": slot_index,
                "src": slot.get("src"),
                "reason": (match or {}).get("reason") or "no reference matched",
                "compare": (match or {}).get("compare"),
                "binary": (match or {}).get("binary"),
            })
            continue
        reference = match["reference"]
        reference_id = _image_row_id(reference, str(match["compare"].get("reference_path") or "reference"))
        candidates = sorted(
            localized_by_reference.get(reference_id) or [],
            key=lambda item: float(item.get("visual_score") or 0.0),
            reverse=True,
        )
        candidates = [item for item in candidates if str(item.get("local_path") or "") not in used_localized_paths]
        if not candidates:
            review.append({
                "kind": "slot",
                "slot_index": slot_index,
                "src": slot.get("src"),
                "reference_filename": reference.get("filename"),
                "reason": "no localized candidate for visual reference",
            })
            continue
        chosen = candidates[0]
        replacement_path = str(chosen.get("local_path") or "")
        if not replacement_path:
            review.append({"kind": "slot", "slot_index": slot_index, "reason": "chosen localized path missing"})
            continue
        pairs.append((slot_index, replacement_path))
        used_localized_paths.add(replacement_path)
        confirmation_pairs.append({
            "match_method": "visual",
            "target_kind": "carousel",
            "slot_index": slot_index,
            "current_src": slot.get("src"),
            "current_local_path": local_path,
            "reference_id": reference_id,
            "reference_filename": reference.get("filename"),
            "reference_local_path": reference.get("local_path"),
            "replacement_filename": chosen.get("filename"),
            "replacement_local_path": replacement_path,
            "slot_score": float(match["compare"].get("score") or 0.0),
            "localized_score": float(chosen.get("visual_score") or 0.0),
            "binary_status": match["binary"].get("status"),
            "binary_similarity": match["binary"].get("binary_similarity"),
            "foreground_overlap": match["binary"].get("foreground_overlap"),
            "confidence": (
                "high"
                if match.get("confidence") == "high" and chosen.get("confidence") == "high"
                else "needs_review"
            ),
        })
        for extra in candidates[1:]:
            conflicts.append({
                "slot_index": slot_index,
                "localized_id": _image_row_id(extra, "localized"),
                "reason": "duplicate localized visual candidate",
            })

    return {
        "pairs": pairs,
        "confirmation_pairs": confirmation_pairs,
        "review": review,
        "conflicts": conflicts,
    }


def build_visual_detail_replacement_plan(
    *,
    slot_images: list[dict[str, Any]],
    reference_images: list[dict[str, Any]],
    localized_images: list[dict[str, Any]],
    min_score: float = VISUAL_MATCH_MIN_SCORE,
) -> dict[str, Any]:
    plan = build_visual_carousel_pair_plan(
        slot_images=slot_images,
        reference_images=reference_images,
        localized_images=localized_images,
        min_score=min_score,
    )
    localized_by_path = {
        str(row.get("local_path") or ""): row
        for row in localized_images or []
        if row.get("local_path")
    }
    forced_replacements_by_src: dict[str, dict[str, Any]] = {}
    for row in plan.get("confirmation_pairs") or []:
        row["target_kind"] = "detail"
        src = str(row.get("current_src") or "")
        replacement_path = str(row.get("replacement_local_path") or "")
        if not src or not replacement_path:
            continue
        candidate = dict(localized_by_path.get(replacement_path) or {})
        candidate["local_path"] = replacement_path
        if row.get("replacement_filename") and not candidate.get("filename"):
            candidate["filename"] = row.get("replacement_filename")
        candidate["match_method"] = "visual"
        forced_replacements_by_src[src] = candidate
    return {
        **plan,
        "forced_replacements_by_src": forced_replacements_by_src,
    }


def confirm_visual_carousel_pairs(
    confirmation_pairs: list[dict[str, Any]],
    *,
    confirm_cb: VisualPairConfirmCallback | None,
) -> None:
    if not confirmation_pairs:
        return
    if confirm_cb is None:
        raise RuntimeError("visual carousel fallback requires user confirmation")
    if not confirm_cb(confirmation_pairs):
        raise cancellation.OperationCancelled("用户取消视觉兜底配对确认")


def _image_url(row: dict[str, Any]) -> str:
    return str(row.get("url") or row.get("download_url") or row.get("src") or "").strip()


def _filename_from_url(url: str, fallback: str) -> str:
    name = Path(urlparse(str(url or "")).path).name
    return name or fallback


def _download_visual_rows(
    items: list[dict[str, Any]],
    output_dir: Path,
    *,
    prefix: str,
    cancel_token: cancellation.CancellationToken | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for idx, item in enumerate(items):
        url = _image_url(item)
        if not url or url.lower().split("?", 1)[0].endswith(".gif"):
            continue
        original_filename = str(item.get("filename") or _filename_from_url(url, f"image-{idx:02d}.jpg"))
        rows.append({
            **item,
            "url": url,
            "original_filename": original_filename,
            "filename": f"{prefix}_{idx:02d}_{original_filename}",
        })
    output_dir.mkdir(parents=True, exist_ok=True)
    return downloader.download_images(rows, output_dir, retries=1, status_cb=print, cancel_token=cancel_token)


def download_visual_carousel_sources(
    *,
    workspace: storage.Workspace,
    product_images: list[dict] | list[str],
    reference_images: list[dict],
    unmatched_slot_indices: list[int],
    cancel_token: cancellation.CancellationToken | None = None,
) -> dict[str, list[dict[str, Any]]]:
    unmatched = set(unmatched_slot_indices)
    slot_rows: list[dict[str, Any]] = []
    for idx, image in enumerate(product_images):
        if idx not in unmatched:
            continue
        if isinstance(image, str):
            src = image
        else:
            src = str(image.get("src") or image.get("url") or "")
        src = _normalize_src(src)
        if not src or src.lower().split("?", 1)[0].endswith(".gif"):
            continue
        slot_rows.append({
            "id": f"carousel-slot-{idx:02d}",
            "slot_id": f"carousel-{idx:02d}",
            "slot_index": idx,
            "src": src,
            "url": src,
            "filename": _filename_from_url(src, f"carousel-slot-{idx:02d}.jpg"),
        })

    current_dir = workspace.source_en_dir / "shopify_current"
    reference_dir = workspace.source_en_dir / "server_reference"
    downloaded_slots = _download_visual_rows(
        slot_rows,
        current_dir,
        prefix="slot",
        cancel_token=cancel_token,
    )
    downloaded_references = _download_visual_rows(
        list(reference_images or []),
        reference_dir,
        prefix="reference",
        cancel_token=cancel_token,
    )
    return {
        "slot_images": downloaded_slots,
        "reference_images": downloaded_references,
    }


def download_visual_detail_sources(
    *,
    workspace: storage.Workspace,
    detail_html: str,
    reference_images: list[dict],
    candidate_srcs: list[str],
    cancel_token: cancellation.CancellationToken | None = None,
) -> dict[str, list[dict[str, Any]]]:
    candidate_set = {_normalize_src(src) for src in candidate_srcs}
    slot_rows: list[dict[str, Any]] = []
    for idx, ref in enumerate(taa_cdp.extract_image_refs(detail_html)):
        src = _normalize_src(ref.get("src") or "")
        if is_payment_screenshot(src, ref.get("alt") or ""):
            print(f"详情图：跳过收款截图视觉兜底：{src}")
            continue
        if src not in candidate_set:
            continue
        if not src or src.lower().split("?", 1)[0].endswith(".gif"):
            continue
        slot_rows.append({
            "id": f"detail-slot-{idx:02d}",
            "slot_id": f"detail-{idx:02d}",
            "slot_index": idx,
            "src": src,
            "url": src,
            "filename": _filename_from_url(src, f"detail-slot-{idx:02d}.jpg"),
        })

    current_dir = workspace.source_en_dir / "shopify_detail_current"
    reference_dir = workspace.source_en_dir / "server_reference"
    downloaded_slots = _download_visual_rows(
        slot_rows,
        current_dir,
        prefix="detail",
        cancel_token=cancel_token,
    )
    downloaded_references = _download_visual_rows(
        list(reference_images or []),
        reference_dir,
        prefix="reference",
        cancel_token=cancel_token,
    )
    return {
        "slot_images": downloaded_slots,
        "reference_images": downloaded_references,
    }


def should_attempt_detail_visual_fallback(src: str, *, replace_shopify_cdn: bool) -> bool:
    normalized = _normalize_src(src)
    if not normalized:
        return False
    if is_payment_screenshot(normalized, ""):
        return False
    if normalized.lower().split("?", 1)[0].endswith(".gif"):
        return False
    if "cdn.shopify.com/s/files/" in normalized and not replace_shopify_cdn:
        return False
    return True


def build_detail_source_index_map(
    body_html: str,
    reference_images: list[dict],
    *,
    carousel_image_count: int,
) -> dict[str, int]:
    reference_by_token: dict[str, list[int]] = {}
    reference_by_name: dict[str, list[int]] = {}
    for item in reference_images:
        filename = str(item.get("filename") or "")
        token = ez_cdp.md5_token(filename)
        source_index = taa_cdp.source_index_from_filename(filename)
        if token and source_index is not None:
            reference_by_token.setdefault(token, []).append(source_index)
        name_key = taa_cdp.source_name_key(filename)
        if name_key and source_index is not None:
            reference_by_name.setdefault(name_key, []).append(source_index)

    mapping: dict[str, int] = {}
    used_indices: set[int] = set()
    for ref in taa_cdp.extract_image_refs(body_html):
        src = _normalize_src(ref.get("src") or "")
        if is_payment_screenshot(src, ref.get("alt") or ""):
            continue
        token = ez_cdp.md5_token(src)
        name_key = taa_cdp.source_name_key(src)
        key = token or name_key
        if not key or key in mapping:
            continue
        candidates = sorted(set((reference_by_token.get(token or "") if token else None) or reference_by_name.get(name_key or "") or []))
        if not candidates:
            continue
        detail_side = [idx for idx in candidates if idx >= carousel_image_count and idx not in used_indices]
        if detail_side:
            source_index = detail_side[0]
        else:
            unused = [idx for idx in candidates if idx not in used_indices]
            if len(unused) != 1:
                continue
            source_index = unused[0]
        mapping[key] = source_index
        used_indices.add(source_index)
    return mapping


def fetch_bootstrap_ready(
    *,
    product_code: str,
    lang: str,
    timeout_s: int,
    shopify_product_id: str = "",
    cancel_token: cancellation.CancellationToken | None = None,
) -> dict[str, Any]:
    cfg = settings.load_runtime_config()
    deadline = time.time() + timeout_s
    attempt = 0
    last_error: Exception | None = None
    while True:
        cancellation.throw_if_cancelled(cancel_token)
        attempt += 1
        try:
            payload = api_client.fetch_bootstrap(
                cfg["base_url"],
                cfg["api_key"],
                product_code,
                lang,
                shopify_product_id=shopify_product_id,
            )
            localized_count = len(payload.get("localized_images") or [])
            if localized_count > 0:
                print(f"bootstrap 已就绪（第 {attempt} 次尝试，本地化记录 {localized_count} 条）")
                return payload
            last_error = RuntimeError("bootstrap returned no localized images")
        except api_client.ApiError as exc:
            last_error = exc
            error_code = str(exc.payload.get("error") or "")
            print(f"bootstrap 接口异常 {exc.status_code} {error_code}：{exc}")
            if error_code == "shopify_product_id_missing":
                raise
        except Exception as exc:
            last_error = exc
            print(f"bootstrap 第 {attempt} 次尝试失败：{exc}")
        if time.time() >= deadline:
            break
        cancellation.cancellable_sleep(cancel_token, 5)
    raise TimeoutError(f"bootstrap not ready for {product_code}/{lang}: {last_error}") from last_error


def download_localized(
    product_code: str,
    lang: str,
    bootstrap: dict[str, Any],
    *,
    cancel_token: cancellation.CancellationToken | None = None,
) -> tuple[storage.Workspace, list[dict]]:
    workspace = storage.create_workspace(product_code, lang)
    localized_images = bootstrap.get("localized_images") or []
    print(f"开始下载本地化图片 {len(localized_images)} 张到 {workspace.source_localized_dir}")
    downloaded = downloader.download_images(
        localized_images,
        workspace.source_localized_dir,
        retries=2,
        status_cb=print,
        cancel_token=cancel_token,
    )
    print(f"本地化图片下载完成，共 {len(downloaded)} 张")
    return workspace, downloaded


def _extension_from_url(src: str) -> str:
    suffix = Path(urlparse(src).path).suffix.lower().lstrip(".")
    if suffix in {"jpg", "jpeg", "png", "webp"}:
        return suffix
    return "jpg"


def add_original_detail_fallbacks(
    *,
    workspace: storage.Workspace,
    body_html: str,
    localized_images: list[dict],
    cancel_token: cancellation.CancellationToken | None = None,
) -> list[dict]:
    candidates_by_token = _localized_by_token(localized_images)
    added: list[dict] = []
    for idx, ref in enumerate(taa_cdp.extract_image_refs(body_html)):
        cancellation.throw_if_cancelled(cancel_token)
        src = _normalize_src(ref.get("src") or "")
        if is_payment_screenshot(src, ref.get("alt") or ""):
            print(f"详情图：跳过收款截图原图兜底：{src}")
            continue
        token = ez_cdp.md5_token(src)
        if not token or token in candidates_by_token:
            continue
        if src.lower().split("?", 1)[0].endswith(".gif"):
            continue
        ext = _extension_from_url(src)
        filename = f"fallback_original_from_url_en_{idx:02d}_{token}.{ext}"
        output_path = workspace.source_localized_dir / filename
        print(f"详情图：使用原图兜底（token={token}）：{src}")
        request = urllib.request.Request(src, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=30) as response:
            output_path.write_bytes(response.read())
        row = {
            "id": f"fallback-{token}",
            "kind": "detail",
            "filename": filename,
            "url": src,
            "local_path": str(output_path),
            "fallback_original": True,
        }
        localized_images.append(row)
        candidates_by_token.setdefault(token, []).append(row)
        added.append(row)
    return added


def verify_storefront_body(
    product_code: str,
    *,
    locale: str,
    expected_urls: list[str],
    store_domain: str = DEFAULT_STORE_DOMAIN,
) -> dict[str, Any]:
    product = fetch_storefront_product(product_code, locale=locale, store_domain=store_domain)
    body_html = str(product.get("description") or product.get("body_html") or "")
    srcs = taa_cdp.extract_image_srcs(body_html)
    return {
        "product_id": str(product.get("id") or ""),
        "title": product.get("title"),
        "image_count": len(srcs),
        "expected_total": len(expected_urls),
        "expected_present": sum(1 for url in expected_urls if url in body_html),
        "old_non_shopify_count": sum(1 for src in srcs if "cdn.shopify.com/s/files/" not in src),
    }


def fetch_storefront_image_display_sizes(
    *,
    product_code: str,
    locale: str,
    store_domain: str,
    user_data_dir: str,
    port: int,
    cancel_token: cancellation.CancellationToken | None = None,
) -> dict[str, dict[str, Any]]:
    cancellation.throw_if_cancelled(cancel_token)
    normalized_locale = str(locale or "").strip().strip("/")
    prefix = f"/{normalized_locale}" if normalized_locale else ""
    url = f"https://{store_domain}{prefix}/products/{product_code}"
    ez_cdp.ensure_cdp_chrome(user_data_dir, port=port)
    sizes: dict[str, dict[str, Any]] = {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(ez_cdp._cdp_ws_endpoint(port))
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.new_page()
        try:
            cancellation.throw_if_cancelled(cancel_token)
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            cancellation.throw_if_cancelled(cancel_token)
            page.wait_for_timeout(4000)
            cancellation.throw_if_cancelled(cancel_token)
            rows = page.evaluate(
                """() => Array.from(document.images).map((img) => {
                    const rect = img.getBoundingClientRect();
                    return {
                        src: img.currentSrc || img.src || '',
                        width: Math.round(rect.width || 0),
                        height: Math.round(rect.height || 0),
                        naturalWidth: img.naturalWidth || 0,
                        naturalHeight: img.naturalHeight || 0,
                    };
                })"""
            )
            for row in rows or []:
                src = _normalize_src(str(row.get("src") or ""))
                if src and int(row.get("width") or 0) > 0:
                    sizes[src] = row
        finally:
            try:
                page.close()
            except Exception:
                pass
            try:
                browser.close()
            except Exception:
                pass
    cancellation.throw_if_cancelled(cancel_token)
    return sizes


def _preload_chrome_tab_to_url(
    *,
    user_data_dir: str,
    port: int,
    target_url: str,
    label: str,
) -> None:
    print(f"{label}：正在浏览器中打开目标页面 {target_url}")
    try:
        ez_cdp.ensure_cdp_chrome(user_data_dir, port=port)
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(ez_cdp._cdp_ws_endpoint(port))
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            pages = list(context.pages)
            page = pages[0] if pages else context.new_page()
            try:
                page.bring_to_front()
            except Exception:
                pass
            page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
    except Exception as exc:
        print(f"{label}：预先打开页面失败（不影响后续）：{exc}")


def run(
    args: argparse.Namespace,
    *,
    cancel_token: cancellation.CancellationToken | None = None,
    visual_pair_confirm_cb: VisualPairConfirmCallback | None = None,
) -> dict[str, Any]:
    cfg = settings.load_runtime_config()
    cancellation.throw_if_cancelled(cancel_token)
    source_product = fetch_storefront_product(args.product_code, store_domain=args.store_domain)
    cancellation.throw_if_cancelled(cancel_token)
    target_product = fetch_storefront_product(
        args.product_code,
        locale=args.shop_locale,
        store_domain=args.store_domain,
    )
    cancellation.throw_if_cancelled(cancel_token)
    product_id = str(args.product_id or source_product.get("id") or target_product.get("id") or "").strip()
    if not product_id:
        raise RuntimeError("Shopify product id not found")
    if str(target_product.get("id") or "") and str(target_product.get("id")) != product_id:
        raise RuntimeError(f"source/target product id mismatch: {product_id} vs {target_product.get('id')}")

    bootstrap = fetch_bootstrap_ready(
        product_code=args.product_code,
        lang=args.lang,
        timeout_s=args.bootstrap_timeout_s,
        shopify_product_id=product_id,
        cancel_token=cancel_token,
    )
    workspace, downloaded = download_localized(
        args.product_code,
        args.lang,
        bootstrap,
        cancel_token=cancel_token,
    )
    cancellation.throw_if_cancelled(cancel_token)

    result: dict[str, Any] = {
        "product_code": args.product_code,
        "lang": args.lang,
        "shop_locale": args.shop_locale,
        "taa_shop_locale": getattr(args, "taa_shop_locale", args.shop_locale),
        "shopify_product_id": product_id,
        "workspace": str(workspace.root),
        "download_dir": str(workspace.source_localized_dir),
        "carousel": None,
        "detail": None,
        "storefront": None,
    }

    product_images = product_image_sources(source_product)
    if not args.skip_carousel:
        cancellation.throw_if_cancelled(cancel_token)
        _preload_chrome_tab_to_url(
            user_data_dir=cfg["browser_user_data_dir"],
            port=args.port,
            target_url=session.build_ez_url(product_id),
            label="轮播图",
        )
        print("轮播图：正在按文件名/哈希比对位置与本地化图片")
        pairs = pair_carousel_images(downloaded, product_images)
        print(f"轮播图：文件名匹配完成，共 {len(pairs)} 对")
        eligible_indices = [
            idx
            for idx, src in enumerate(product_images)
            if src and not str(src).lower().split("?", 1)[0].endswith(".gif")
        ]
        paired_indices = {slot_idx for slot_idx, _path in pairs}
        unmatched_indices = [idx for idx in eligible_indices if idx not in paired_indices]
        visual_fallback_plan: dict[str, Any] = {"pairs": [], "confirmation_pairs": [], "review": [], "conflicts": []}
        if unmatched_indices and bootstrap.get("reference_images"):
            try:
                print(f"轮播图：剩余 {len(unmatched_indices)} 个位置进入视觉兜底（位置 {unmatched_indices}）")
                print(f"轮播图：视觉兜底——开始下载 {len(unmatched_indices)} 个位置的参考图")
                visual_sources = download_visual_carousel_sources(
                    workspace=workspace,
                    product_images=product_images,
                    reference_images=bootstrap.get("reference_images") or [],
                    unmatched_slot_indices=unmatched_indices,
                    cancel_token=cancel_token,
                )
                print("轮播图：视觉识别中——正在用图像比对算法生成配对方案（耗时较长，请耐心等待）")
                visual_fallback_plan = build_visual_carousel_pair_plan(
                    slot_images=visual_sources.get("slot_images") or [],
                    reference_images=visual_sources.get("reference_images") or [],
                    localized_images=downloaded,
                    reserved_localized_paths={path for _slot_idx, path in pairs},
                )
                visual_pairs = list(visual_fallback_plan.get("pairs") or [])
                if visual_pairs:
                    print(f"轮播图：视觉识别完成，得到 {len(visual_pairs)} 对候选；等待用户确认配对")
                    confirm_visual_carousel_pairs(
                        list(visual_fallback_plan.get("confirmation_pairs") or []),
                        confirm_cb=visual_pair_confirm_cb,
                    )
                    pairs.extend(visual_pairs)
                    pairs.sort(key=lambda row: row[0])
                    print(f"轮播图：视觉兜底已确认 {len(visual_pairs)} 对")
                else:
                    print(f"轮播图：视觉兜底未能生成可用配对：{visual_fallback_plan.get('review')}")
            except cancellation.OperationCancelled:
                raise
            except Exception as exc:
                print(f"轮播图：视觉兜底不可用：{exc}")
        if not pairs:
            print("轮播图：未找到可替换的配对，跳过轮播图替换")
            result["carousel"] = {
                "requested": 0,
                "results": [],
                "ok": 0,
                "skipped": 0,
                "visual_fallback_count": 0,
                "skipped_reason": "no matched image pairs",
                "visual_review": visual_fallback_plan.get("review") or [],
            }
        else:
            print(f"轮播图：开始替换 {len(pairs)} 个位置")
            ez_url = session.build_ez_url(product_id)
            carousel_results = ez_cdp.replace_many(
                ez_url=ez_url,
                user_data_dir=cfg["browser_user_data_dir"],
                pairs=pairs,
                language=args.language,
                replace_existing=not args.skip_existing_carousel,
                port=args.port,
                limit=args.carousel_limit if args.carousel_limit > 0 else None,
                cancel_token=cancel_token,
            )
            cancellation.throw_if_cancelled(cancel_token)
            result["carousel"] = {
                "requested": len(pairs),
                "results": carousel_results,
                "ok": sum(1 for row in carousel_results if row.get("status") == "ok"),
                "skipped": sum(1 for row in carousel_results if row.get("status") == "skipped"),
                "visual_fallback_count": len(visual_fallback_plan.get("pairs") or []),
                "visual_confirmation_pairs": visual_fallback_plan.get("confirmation_pairs") or [],
                "visual_review": visual_fallback_plan.get("review") or [],
                "visual_conflicts": visual_fallback_plan.get("conflicts") or [],
            }

    if not args.skip_detail:
        cancellation.throw_if_cancelled(cancel_token)
        detail_html = str(target_product.get("description") or target_product.get("body_html") or "")
        fallback_images: list[dict] = []
        if not args.no_original_detail_fallback:
            fallback_images = add_original_detail_fallbacks(
                workspace=workspace,
                body_html=detail_html,
                localized_images=downloaded,
                cancel_token=cancel_token,
            )
        display_size_by_src: dict[str, dict[str, Any]] = {}
        if not args.no_preserve_detail_size:
            display_size_by_src = fetch_storefront_image_display_sizes(
                product_code=args.product_code,
                locale=args.shop_locale,
                store_domain=args.store_domain,
                user_data_dir=cfg["browser_user_data_dir"],
                port=args.port,
                cancel_token=cancel_token,
            )
            print(f"详情图：已采集 {len(display_size_by_src)} 张图片的显示尺寸")
        source_index_map = taa_cdp.parse_source_index_map(args.source_index_map)
        if not source_index_map:
            source_index_map = build_detail_source_index_map(
                detail_html,
                bootstrap.get("reference_images") or [],
                carousel_image_count=len(product_images),
            )
        print(f"详情图：使用源图序号映射 {source_index_map}")
        _preload_chrome_tab_to_url(
            user_data_dir=cfg["browser_user_data_dir"],
            port=args.port,
            target_url=session.build_translate_url(
                product_id,
                getattr(args, "taa_shop_locale", args.shop_locale),
            ),
            label="详情图",
        )
        detail_visual_plan: dict[str, Any] = {
            "forced_replacements_by_src": {},
            "confirmation_pairs": [],
            "review": [],
            "conflicts": [],
        }
        try:
            preliminary_detail_plan = taa_cdp.plan_body_html_replacements(
                detail_html,
                downloaded,
                source_index_by_token=source_index_map,
                replace_shopify_cdn=args.replace_shopify_cdn,
            )
            missing_detail_srcs = [
                str(row.get("src") or "")
                for row in preliminary_detail_plan.get("skipped_missing") or []
                if row.get("src")
                and should_attempt_detail_visual_fallback(
                    str(row.get("src") or ""),
                    replace_shopify_cdn=args.replace_shopify_cdn,
                )
            ]
            skipped_visual_missing = len(preliminary_detail_plan.get("skipped_missing") or []) - len(missing_detail_srcs)
            if skipped_visual_missing > 0:
                print(
                    f"详情图：视觉兜底跳过 {skipped_visual_missing} 张不参与自动替换的图片"
                )
            if missing_detail_srcs and bootstrap.get("reference_images"):
                print(f"详情图：视觉兜底——开始下载 {len(missing_detail_srcs)} 张位置参考图")
                detail_visual_sources = download_visual_detail_sources(
                    workspace=workspace,
                    detail_html=detail_html,
                    reference_images=bootstrap.get("reference_images") or [],
                    candidate_srcs=missing_detail_srcs,
                    cancel_token=cancel_token,
                )
                print("详情图：视觉识别中——正在用图像比对算法生成替换方案（耗时较长，请耐心等待）")
                detail_visual_plan = build_visual_detail_replacement_plan(
                    slot_images=detail_visual_sources.get("slot_images") or [],
                    reference_images=detail_visual_sources.get("reference_images") or [],
                    localized_images=downloaded,
                )
                detail_confirmations = list(detail_visual_plan.get("confirmation_pairs") or [])
                if detail_confirmations:
                    print(f"详情图：视觉识别完成，得到 {len(detail_confirmations)} 对候选；等待用户确认配对")
                    confirm_visual_carousel_pairs(detail_confirmations, confirm_cb=visual_pair_confirm_cb)
                    print(f"详情图：视觉兜底已确认 {len(detail_confirmations)} 对")
                else:
                    print(f"详情图：视觉兜底未能生成可用配对：{detail_visual_plan.get('review')}")
        except cancellation.OperationCancelled:
            raise
        except Exception as exc:
            print(f"详情图：视觉兜底不可用：{exc}")
        detail_result = taa_cdp.replace_detail_images(
            product_id=product_id,
            shop_locale=getattr(args, "taa_shop_locale", args.shop_locale),
            user_data_dir=cfg["browser_user_data_dir"],
            localized_images=downloaded,
            source_index_by_token=source_index_map,
            forced_replacements_by_src=detail_visual_plan.get("forced_replacements_by_src") or {},
            display_size_by_src=display_size_by_src,
            port=args.port,
            replace_shopify_cdn=args.replace_shopify_cdn,
            verify_reload=not args.no_detail_reload_verify,
            cancel_token=cancel_token,
        )
        cancellation.throw_if_cancelled(cancel_token)
        result["detail"] = {key: value for key, value in detail_result.items() if key != "verify"}
        result["detail"]["visual_fallback_count"] = len(detail_visual_plan.get("forced_replacements_by_src") or {})
        result["detail"]["visual_confirmation_pairs"] = detail_visual_plan.get("confirmation_pairs") or []
        result["detail"]["visual_review"] = detail_visual_plan.get("review") or []
        result["detail"]["visual_conflicts"] = detail_visual_plan.get("conflicts") or []
        result["detail"]["fallback_original_count"] = len(fallback_images)
        result["detail"]["fallback_originals"] = [
            {
                "token": row.get("token") or ez_cdp.md5_token(str(row.get("filename") or "")),
                "local_path": row.get("local_path"),
                "url": row.get("url"),
            }
            for row in fallback_images
        ]
        result["detail"]["verify"] = {
            key: value for key, value in detail_result.get("verify", {}).items() if key != "html"
        }
        expected_urls = [row["new"] for row in detail_result.get("replacements") or [] if row.get("new")]
        result["storefront"] = verify_storefront_body(
            args.product_code,
            locale=args.shop_locale,
            expected_urls=expected_urls,
            store_domain=args.store_domain,
        )
        cancellation.throw_if_cancelled(cancel_token)

    output_path = workspace.root / f"shopify_batch_{args.lang}_result.json"
    storage.write_json(output_path, result)
    print(f"已写入结果文件：{output_path}")
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--product-code", required=True)
    parser.add_argument("--lang", default="it")
    parser.add_argument("--shop-locale", default="")
    parser.add_argument("--taa-shop-locale", default="")
    parser.add_argument("--language", default="")
    parser.add_argument("--product-id", default="")
    parser.add_argument("--store-domain", default=DEFAULT_STORE_DOMAIN)
    parser.add_argument("--bootstrap-timeout-s", type=int, default=60)
    parser.add_argument("--port", type=int, default=ez_cdp.DEFAULT_CDP_PORT)
    parser.add_argument("--carousel-limit", type=int, default=0)
    parser.add_argument("--skip-carousel", action="store_true")
    parser.add_argument("--skip-detail", action="store_true")
    parser.add_argument("--skip-existing-carousel", action="store_true")
    parser.add_argument("--source-index-map", default="")
    parser.add_argument("--replace-shopify-cdn", action="store_true")
    parser.add_argument("--no-preserve-detail-size", action="store_true")
    parser.add_argument("--no-original-detail-fallback", action="store_true")
    parser.add_argument("--no-detail-reload-verify", action="store_true")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.lang = str(args.lang or "").strip().lower()
    args.shop_locale = str(args.shop_locale or args.lang).strip().lower()
    args.taa_shop_locale = locales.translate_and_adapt_locale_for(
        str(args.taa_shop_locale or args.shop_locale).strip()
    )
    args.language = str(args.language or locales.english_name_for(args.lang)).strip()
    try:
        run(args)
    except api_client.ApiError as exc:
        print(f"已阻塞：bootstrap 接口 {exc.status_code}：{json.dumps(exc.payload, ensure_ascii=False)}")
        raise SystemExit(2) from exc
    except TimeoutError as exc:
        print(f"已阻塞：{exc}")
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
