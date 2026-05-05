"""Service response builders for medias page routes."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from appcore import medias, product_roas, shopify_image_localizer_release


def build_medias_page_context(
    args: Mapping[str, Any] | None = None,
    extra: Mapping[str, Any] | None = None,
    *,
    get_release_info_fn: Callable[[], Any] | None = None,
    get_rmb_per_usd_fn: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    args = args or {}
    extra_context = dict(extra or {})
    initial_query = (
        args.get("q")
        or args.get("keyword")
        or extra_context.get("initial_query")
        or ""
    )
    release_info_fn = get_release_info_fn or shopify_image_localizer_release.get_release_info
    rmb_per_usd_fn = get_rmb_per_usd_fn or product_roas.get_configured_rmb_per_usd
    return {
        "shopify_image_localizer_release": release_info_fn(),
        "material_roas_rmb_per_usd": float(rmb_per_usd_fn()),
        "medias_initial_query": str(initial_query).strip(),
        **extra_context,
    }


def build_active_users_response(
    *,
    list_active_users_fn: Callable[[], list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    list_fn = list_active_users_fn or medias.list_active_users
    return {"users": list_fn()}


def build_languages_response(
    *,
    list_languages_fn: Callable[[], list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    list_fn = list_languages_fn or medias.list_languages
    return {"items": list_fn()}
