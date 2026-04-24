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


def _open_worker_page(context):
    """在当前 context 里新开一个属于本工具的 tab。"""
    return context.new_page()


def run_shopify_localizer(
    *,
    browser_user_data_dir: str,
    bootstrap: dict,
    reference_images: list[dict],
    localized_images: list[dict],
    workspace,
    status_cb=None,
    cdp_port: int = session.DEFAULT_CDP_PORT,
) -> dict:
    product = bootstrap.get("product") or {}
    language = bootstrap.get("language") or {}
    shopify_product_id = str(product.get("shopify_product_id") or "").strip()
    shop_locale = str(language.get("shop_locale") or language.get("code") or "").strip().lower()
    if not shopify_product_id:
        raise RuntimeError("missing shopify_product_id")
    if not shop_locale:
        raise RuntimeError("missing shop locale")

    # 先保证用户那台 Chrome 在 CDP 端口上就位（没起就启动一个普通 Chrome）
    started_chrome = session.ensure_chrome_running(
        browser_user_data_dir, port=cdp_port
    )
    if status_cb is not None:
        status_cb(
            "本地 Chrome 已就绪"
            + ("（本次由工具启动）" if started_chrome else "（复用已在运行的 Chrome）")
        )

    ez_result: dict = {"status": "failed", "used_localized_ids": []}
    translate_result: dict = {"status": "failed", "used_localized_ids": []}
    ez_page = None
    translate_page = None

    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{cdp_port}")
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        context.set_default_timeout(15000)

        try:
            ez_page = _open_worker_page(context)
            translate_page = _open_worker_page(context)

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
        finally:
            # 工具自己开的 tab 跑完关掉；不要关用户的 tabs、不要关 context、不要杀 Chrome
            for page in (ez_page, translate_page):
                if page is None:
                    continue
                try:
                    page.close()
                except Exception:
                    pass
            try:
                browser.close()  # 仅断开 CDP
            except Exception:
                pass

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
