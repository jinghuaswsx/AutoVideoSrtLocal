from __future__ import annotations

from tools.shopify_image_localizer import matcher
from tools.shopify_image_localizer.browser import session


EZ_IMAGE_SELECTORS = [
    "main img[src]",
    "[class*='Polaris'] img[src]",
    "img[src]",
]


def run_ez_flow(
    *,
    page,
    shopify_product_id: str,
    language_code: str,
    reference_images: list[dict],
    localized_images: list[dict],
    workspace,
    reserved_localized_ids: set[str] | None = None,
    status_cb=None,
) -> dict:
    if status_cb is not None:
        status_cb("正在处理 EZ Product Image")

    target_url = session.build_ez_url(shopify_product_id)
    session.ensure_target_page(page, target_url, status_cb=status_cb, label="EZ Product Image")
    session.save_page_snapshot(page, workspace.screenshots_ez_dir, "ez-page-before.png")

    slot_images = session.capture_visible_images(
        page,
        workspace.classify_ez_dir,
        prefix="ez",
        selectors=EZ_IMAGE_SELECTORS,
    )
    assignments = matcher.assign_images(
        slot_images,
        reference_images,
        localized_images,
        reserved_localized_ids=reserved_localized_ids,
    )

    uploads: list[dict] = []
    for row in assignments["assigned"]:
        try:
            session.click_slot(page, row["slot"])
            uploaded = session.upload_file_to_page(page, row["local_path"])
            uploads.append({
                "slot_id": row["slot_id"],
                "localized_id": row["localized_id"],
                "uploaded": bool(uploaded),
            })
        except Exception as exc:
            uploads.append({
                "slot_id": row["slot_id"],
                "localized_id": row["localized_id"],
                "uploaded": False,
                "error": str(exc),
            })

    session.save_page_snapshot(page, workspace.screenshots_ez_dir, "ez-page-after.png")
    return {
        "status": "done",
        "page_url": page.url,
        "language_code": language_code,
        "captured_slots": len(slot_images),
        "assigned": assignments["assigned"],
        "conflicts": assignments["conflicts"],
        "review": assignments["review"],
        "used_localized_ids": assignments["used_localized_ids"],
        "uploads": uploads,
    }
