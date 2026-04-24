from __future__ import annotations

"""半自动模式 orchestrator。

本工具不再通过 CDP 控制 Shopify embedded app 的 iframe（Shopify App Bridge 会
反调试，任何 automation 启动都会拒绝渲染）。改为：
- 启动普通 Chrome，保留登录态
- 把 EZ 和 TAA 两个页面开在现有 Chrome 实例里
- 把配对结果整理好返回给 controller / GUI，由用户在浏览器里点几下完成最后上传
"""

from tools.shopify_image_localizer import matcher
from tools.shopify_image_localizer.browser import session


def _pair_for_flow(
    flow_label: str,
    reference_images: list[dict],
    localized_images: list[dict],
    *,
    reserved_localized_ids: set[str],
) -> dict:
    """配对一个 flow 下的图。flow_label 是 'ez' 或 'taa'，仅用于记录。

    这里没有页面 slot 可以抓（因为不能 CDP 控制 iframe），所以把每张英文参考图直接
    当作一个 slot：slot_id = f"{flow_label}-{idx:03d}"，local_path 用英文参考图本身。
    这样 matcher 仍能按 reference 找到对应德语图，结构上和原来一致。
    """
    slot_images: list[dict] = []
    for idx, ref in enumerate(reference_images, start=1):
        slot_images.append({
            "slot_id": f"{flow_label}-{idx:03d}",
            "local_path": str(ref.get("local_path") or ""),
            "reference_id": ref.get("id"),
        })

    result = matcher.assign_images(
        slot_images,
        reference_images,
        localized_images,
        reserved_localized_ids=reserved_localized_ids,
    )
    return result


def run_shopify_localizer(
    *,
    browser_user_data_dir: str,
    bootstrap: dict,
    reference_images: list[dict],
    localized_images: list[dict],
    workspace,
    status_cb=None,
    **_legacy_kwargs,
) -> dict:
    """半自动主流程：启动 Chrome → 开两个 tab → 返回两条链路的配对清单。"""
    product = bootstrap.get("product") or {}
    language = bootstrap.get("language") or {}
    shopify_product_id = str(product.get("shopify_product_id") or "").strip()
    shop_locale = str(language.get("shop_locale") or language.get("code") or "").strip().lower()
    if not shopify_product_id:
        raise RuntimeError("missing shopify_product_id")
    if not shop_locale:
        raise RuntimeError("missing shop locale")

    if status_cb is not None:
        status_cb("正在启动 Chrome（普通用户模式）")

    ez_url = session.build_ez_url(shopify_product_id)
    translate_url = session.build_translate_url(shopify_product_id, shop_locale)

    session.ensure_chrome_started(
        browser_user_data_dir,
        initial_urls=[ez_url, translate_url],
    )

    if status_cb is not None:
        status_cb("Chrome 已打开 EZ 与 Translate and Adapt 两个页面")

    # --- 配对两条链路 ---
    used_ids: set[str] = set()
    ez_result = _pair_for_flow("ez", reference_images, localized_images, reserved_localized_ids=used_ids)
    used_ids.update(ez_result.get("used_localized_ids") or [])
    taa_result = _pair_for_flow("taa", reference_images, localized_images, reserved_localized_ids=used_ids)

    # 总状态
    ez_has_matches = bool(ez_result.get("assigned"))
    taa_has_matches = bool(taa_result.get("assigned"))
    if ez_has_matches or taa_has_matches:
        overall = "pending_user_action"
    else:
        overall = "failed"

    return {
        "status": overall,
        "mode": "semi_auto",
        "ez": {
            **ez_result,
            "page_url": ez_url,
            "language_code": str(language.get("code") or shop_locale),
        },
        "translate": {
            **taa_result,
            "page_url": translate_url,
            "shop_locale": shop_locale,
        },
    }
