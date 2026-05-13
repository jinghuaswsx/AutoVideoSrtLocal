from __future__ import annotations

"""Compatibility helper for legacy payment/trust-badge image checks.

Payment, after-sale, and trust-badge images are now treated like normal product
detail images by Shopify Image Localizer, so callers should not filter them.
"""


def is_payment_screenshot(source_url: str, alt: str | None = None) -> bool:
    _ = (source_url, alt)
    return False
