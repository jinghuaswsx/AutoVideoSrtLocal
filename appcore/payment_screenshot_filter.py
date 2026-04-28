from __future__ import annotations

"""Detect Shopify payment/trust-badge screenshots that should not be translated."""

import re
from urllib.parse import urlparse


PAYMENT_IMAGE_HOSTS: set[str] = {
    "cdn.techcloudly.com",
}
PAYMENT_IMAGE_ALT_PATTERN = re.compile(
    r"\b(payment\s*methods?|secure\s*(checkout|payment)"
    r"|trust\s*badge|all\s*transactions\s*are\s*secure)\b",
    re.IGNORECASE,
)


def is_payment_screenshot(source_url: str, alt: str | None = None) -> bool:
    parsed = urlparse(str(source_url or ""))
    host = (parsed.hostname or parsed.netloc).lower()
    if host in PAYMENT_IMAGE_HOSTS:
        return True
    if alt and PAYMENT_IMAGE_ALT_PATTERN.search(str(alt)):
        return True
    return False
