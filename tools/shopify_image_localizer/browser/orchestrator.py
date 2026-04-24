from __future__ import annotations

from playwright.sync_api import sync_playwright

from tools.shopify_image_localizer.browser import ez_flow, session, translate_flow


class FlowConflictError(RuntimeError):
    pass


def _merge_statuses(*statuses: str) -> str:
    normalized = {str(status or "").strip().lower() for status in statuses if status is not None}
    if not normalized or normalized == {"done"}:
        return "done"
    if normalized == {"failed"}:
        return "failed"
    return "partial"


def _safe_page(context, index: int):
    pages = list(context.pages)
    while len(pages) <= index:
        pages.append(context.new_page())
    return pages[index]


def run_shopify_localizer(
    *,
    browser_user_data_dir: str,
    bootstrap: dict,
    reference_images: list[dict],
    localized_images: list[dict],
    workspace,
    status_cb=None,
) -> dict:
    product = bootstrap.get("product") or {}
    language = bootstrap.get("language") or {}
    shopify_product_id = str(product.get("shopify_product_id") or "").strip()
    shop_locale = str(language.get("shop_locale") or language.get("code") or "").strip().lower()
    if not shopify_product_id:
        raise RuntimeError("missing shopify_product_id")
    if not shop_locale:
        raise RuntimeError("missing shop locale")

    with sync_playwright() as playwright:
        context = session.launch_persistent_context(playwright, browser_user_data_dir)
        context.set_default_timeout(15000)

        ez_page = _safe_page(context, 0)
        translate_page = _safe_page(context, 1)

        ez_target_url = session.build_ez_url(shopify_product_id)
        session.ensure_target_page(ez_page, ez_target_url, status_cb=status_cb, label="EZ Product Image")
        session.ensure_target_page(
            translate_page,
            session.build_translate_url(shopify_product_id, shop_locale),
            status_cb=status_cb,
            label="Translate and Adapt",
        )

        used_localized_ids: set[str] = set()
        try:
            ez_result = ez_flow.run_ez_flow(
                page=ez_page,
                shopify_product_id=shopify_product_id,
                language_code=str(language.get("code") or shop_locale),
                reference_images=reference_images,
                localized_images=localized_images,
                workspace=workspace,
                reserved_localized_ids=used_localized_ids,
                status_cb=status_cb,
            )
            used_localized_ids.update(ez_result.get("used_localized_ids") or [])
        except Exception as exc:
            ez_result = {"status": "failed", "error": str(exc), "used_localized_ids": []}

        try:
            translate_result = translate_flow.run_translate_flow(
                page=translate_page,
                shopify_product_id=shopify_product_id,
                shop_locale=shop_locale,
                reference_images=reference_images,
                localized_images=localized_images,
                workspace=workspace,
                reserved_localized_ids=used_localized_ids,
                status_cb=status_cb,
            )
        except Exception as exc:
            translate_result = {"status": "failed", "error": str(exc), "used_localized_ids": []}

        context.close()

    overall_status = _merge_statuses(
        str(ez_result.get("status") or ""),
        str(translate_result.get("status") or ""),
    )

    return {
        "status": overall_status,
        "mode": "dual_page_serial",
        "ez": ez_result,
        "translate": translate_result,
    }
