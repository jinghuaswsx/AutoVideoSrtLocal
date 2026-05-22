"""Product and asset snapshot builders for fine AI evaluation."""

from __future__ import annotations

import logging
import mimetypes
from pathlib import Path
from typing import Any

from appcore import material_evaluation, medias, pushes

log = logging.getLogger(__name__)


class ProductNotFoundError(LookupError):
    pass


class ProductSnapshotService:
    def build_snapshot(
        self,
        product_id: int | str,
        *,
        include_assets: bool = True,
        include_videos: bool = True,
        product_url_override: str | None = None,
    ) -> dict[str, Any]:
        pid = int(product_id)
        product = medias.get_product(pid)
        if not product:
            raise ProductNotFoundError("Product not found")

        skus = _safe_call(medias.list_product_skus, pid, default=[])
        items = _safe_call(medias.list_items, pid, default=[]) if include_videos else []
        covers = _safe_call(medias.get_product_covers, pid, default={}) if include_assets else {}
        copywritings = _safe_call(medias.list_copywritings, pid, default=[])
        product_url = str(product_url_override or "").strip() or _resolve_product_url(product)
        price, currency, compare_at_price = _price_from_product_or_skus(product, skus)

        return {
            "product_id": str(pid),
            "product_name": product.get("name") or product.get("shopify_title") or "",
            "brand": product.get("brand") or "",
            "category": product.get("category") or product.get("source") or "",
            "product_url": product_url,
            "landing_page_url": product_url,
            "description": product.get("remark") or "",
            "sku_options": skus or [],
            "price": price,
            "currency": currency,
            "compare_at_price": compare_at_price,
            "inventory_status": _inventory_status(skus),
            "dimensions": {
                "length_cm": _number_or_none(product.get("package_length_cm")),
                "width_cm": _number_or_none(product.get("package_width_cm")),
                "height_cm": _number_or_none(product.get("package_height_cm")),
            },
            "weight": _first_sku_weight(skus),
            "materials": [],
            "claims": [],
            "selling_points": _split_text(product.get("selling_points")),
            "usage_scenarios": [],
            "target_customers": _split_text(product.get("color_people")),
            "cost": _number_or_none(product.get("purchase_price")),
            "shipping_cost_by_country": {},
            "delivery_days_by_country": {},
            "return_policy": "",
            "product_images": [
                {"lang": lang, "object_key": key}
                for lang, key in (covers or {}).items()
                if key
            ],
            "cover_images": [
                {"lang": lang, "object_key": key}
                for lang, key in (covers or {}).items()
                if key
            ],
            "videos": [
                _asset_meta_from_item(item)
                for item in (items or [])
                if _looks_video(item)
            ],
            "existing_ad_copy": copywritings or [],
            "existing_landing_page_copy": copywritings or [],
            "product_code": product.get("product_code") or "",
            "sku_count": len(skus or []),
            "asset_count": {
                "images": len([key for key in (covers or {}).values() if key]),
                "videos": len([item for item in (items or []) if _looks_video(item)]),
            },
        }


class AssetSnapshotService:
    def build_snapshot(
        self,
        product_id: int | str,
        *,
        include_assets: bool = True,
        include_videos: bool = True,
    ) -> dict[str, Any]:
        pid = int(product_id)
        covers = _safe_call(medias.get_product_covers, pid, default={}) if include_assets else {}
        items = _safe_call(medias.list_items, pid, default=[]) if include_videos else []
        cover_images = []
        product_images = []
        videos = []
        asset_paths: list[str] = []
        warnings: list[str] = []

        for lang, object_key in (covers or {}).items():
            item = _asset_entry("cover_image", object_key, lang=lang)
            cover_images.append(item)
            product_images.append(item)
            _append_materialized_path(asset_paths, warnings, object_key)

        for item in items or []:
            object_key = str(item.get("object_key") or "").strip()
            if not object_key:
                continue
            if _looks_video(item):
                video = _asset_meta_from_item(item)
                videos.append(video)
                _append_materialized_path(asset_paths, warnings, object_key, video_item=item)
            elif _looks_image_key(object_key):
                image = _asset_entry("product_image", object_key, lang=item.get("lang") or "")
                product_images.append(image)
                _append_materialized_path(asset_paths, warnings, object_key)

        return {
            "cover_images": cover_images[:5],
            "product_images": product_images[:8],
            "videos": videos[:3],
            "asset_paths": asset_paths[:8],
            "warnings": warnings,
        }


def _safe_call(fn, *args, default):
    try:
        return fn(*args)
    except Exception:
        log.debug("snapshot helper failed: %s", getattr(fn, "__name__", fn), exc_info=True)
        return default


def _resolve_product_url(product: dict[str, Any]) -> str:
    try:
        return pushes.resolve_product_page_url("en", product) or ""
    except Exception:
        return ""


def _price_from_product_or_skus(product: dict[str, Any], skus: list[dict[str, Any]]) -> tuple[Any, str, Any]:
    price = _number_or_none(product.get("standalone_price") or product.get("tk_sale_price"))
    currency = "USD" if price is not None else ""
    compare_at = None
    for sku in skus or []:
        if price is None and sku.get("shopify_price") is not None:
            price = _number_or_none(sku.get("shopify_price"))
            currency = sku.get("shopify_currency") or currency or "USD"
        if compare_at is None and sku.get("shopify_compare_at_price") is not None:
            compare_at = _number_or_none(sku.get("shopify_compare_at_price"))
    return price, currency, compare_at


def _number_or_none(value: Any):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _inventory_status(skus: list[dict[str, Any]]) -> str:
    quantities = [
        int(sku.get("shopify_inventory_quantity") or 0)
        for sku in skus or []
        if sku.get("shopify_inventory_quantity") is not None
    ]
    if not quantities:
        return ""
    return "in_stock" if any(value > 0 for value in quantities) else "out_of_stock"


def _first_sku_weight(skus: list[dict[str, Any]]):
    for sku in skus or []:
        weight = _number_or_none(sku.get("shopify_weight_grams"))
        if weight is not None:
            return {"value": weight, "unit": "g"}
    return None


def _split_text(value: Any) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    for sep in ("\n", "；", ";", "，", ","):
        if sep in text:
            return [part.strip() for part in text.split(sep) if part.strip()]
    return [text]


def _looks_image_key(object_key: str) -> bool:
    return Path(str(object_key or "").split("?", 1)[0]).suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}


def _looks_video(item: dict[str, Any] | None) -> bool:
    if not item:
        return False
    key = str(item.get("object_key") or item.get("filename") or "")
    return Path(key.split("?", 1)[0]).suffix.lower() in {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"}


def _asset_meta_from_item(item: dict[str, Any]) -> dict[str, Any]:
    object_key = str(item.get("object_key") or "").strip()
    return {
        "asset_id": str(item.get("id") or ""),
        "asset_type": "video",
        "lang": item.get("lang") or "",
        "filename": item.get("display_name") or item.get("filename") or Path(object_key).name,
        "object_key": object_key,
        "duration_seconds": _number_or_none(item.get("duration_seconds")),
        "file_size": item.get("file_size"),
        "mime_type": mimetypes.guess_type(object_key)[0] or "video/mp4",
    }


def _asset_entry(asset_type: str, object_key: str, *, lang: str = "") -> dict[str, Any]:
    return {
        "asset_id": object_key,
        "asset_type": asset_type,
        "lang": lang,
        "object_key": object_key,
        "filename": Path(str(object_key or "")).name,
        "mime_type": mimetypes.guess_type(str(object_key or ""))[0] or "application/octet-stream",
    }


def _append_materialized_path(
    asset_paths: list[str],
    warnings: list[str],
    object_key: str,
    *,
    video_item: dict[str, Any] | None = None,
) -> None:
    key = str(object_key or "").strip()
    if not key:
        return
    try:
        if video_item:
            path = material_evaluation._make_eval_clip_15s(int(video_item.get("product_id") or 0), video_item)
        else:
            path = material_evaluation._materialize_media(key)
        if path and Path(path).is_file():
            asset_paths.append(str(path))
        else:
            warnings.append(f"素材文件不存在：{key}")
    except Exception as exc:
        warnings.append(f"素材读取失败：{key} ({str(exc)[:120]})")
