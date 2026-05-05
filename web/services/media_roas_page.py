"""Service helpers for medias ROAS page context."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from appcore import product_roas


def build_roas_page_context(
    product: dict[str, Any],
    *,
    serialize_product_fn: Callable[[dict[str, Any]], dict[str, Any]],
    get_rmb_per_usd_fn: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    rate_fn = get_rmb_per_usd_fn or product_roas.get_configured_rmb_per_usd
    return {
        "product": serialize_product_fn(product),
        "roas_rmb_per_usd": rate_fn(),
    }
