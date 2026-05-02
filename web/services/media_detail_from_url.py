"""Request planning for detail-image imports from product URLs."""

from __future__ import annotations

import json
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
