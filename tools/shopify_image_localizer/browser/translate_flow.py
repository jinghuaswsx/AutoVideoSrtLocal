from __future__ import annotations

import re

from tools.shopify_image_localizer import matcher
from tools.shopify_image_localizer.browser import session


TRANSLATE_IMAGE_SELECTORS = [
    "[contenteditable='true'] img[src]",
    "[class*='RichTextEditor'] img[src]",
    "img[src*='cdn.shopify.com/s/files/']",
    "img[src*='wxalbum-']",
    "img[src]",
]

TRANSLATE_FRAME_TITLES = ("Translate & Adapt",)


def _dismiss_onboarding_if_needed(scope, *, status_cb=None) -> None:
    buttons = [
        scope.get_by_role("button", name=re.compile(r"close", re.I)).first,
        scope.get_by_role("button", name=re.compile(r"skip", re.I)).first,
    ]
    for button in buttons:
        try:
            if button.count() <= 0:
                continue
            button.click(timeout=2000)
            if status_cb is not None:
                status_cb("已尝试关闭 Translate and Adapt 引导弹窗")
            return
        except Exception:
            continue


def _resolve_flow_status(
    *,
    slot_images: list[dict],
    assigned: list[dict],
    uploads: list[dict],
    review: list[dict],
    conflicts: list[dict],
) -> tuple[str, str]:
    if not slot_images:
        return "failed", "no visible image slots captured"
    if not assigned:
        return "failed", "no localized images assigned"

    uploaded_count = sum(1 for row in uploads if row.get("uploaded"))
    if uploaded_count <= 0:
        return "failed", "no uploads succeeded"
    if uploaded_count < len(assigned):
        return "partial", "some uploads failed"
    if review or conflicts:
        return "partial", "completed with review items"
    return "done", "all assigned uploads succeeded"


def run_translate_flow(
    *,
    page,
    shopify_product_id: str,
    shop_locale: str,
    reference_images: list[dict],
    localized_images: list[dict],
    workspace,
    reserved_localized_ids: set[str] | None = None,
    status_cb=None,
) -> dict:
    if status_cb is not None:
        status_cb("正在处理 Translate and Adapt")

    target_url = session.build_translate_url(shopify_product_id, shop_locale)
    session.ensure_target_page(page, target_url, status_cb=status_cb, label="Translate and Adapt")
    session.save_page_snapshot(page, workspace.screenshots_taa_dir, "translate-page-before.png")
    scope = session.resolve_embedded_scope(
        page,
        label="Translate and Adapt",
        frame_titles=TRANSLATE_FRAME_TITLES,
        status_cb=status_cb,
    )
    _dismiss_onboarding_if_needed(scope, status_cb=status_cb)

    slot_images = session.capture_visible_images(
        scope,
        workspace.classify_taa_dir,
        prefix="taa",
        selectors=TRANSLATE_IMAGE_SELECTORS,
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
            session.click_slot(scope, row["slot"], post_click_page=page)
            uploaded = session.upload_file_to_page(scope, row["local_path"], host_page=page)
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

    status, summary = _resolve_flow_status(
        slot_images=slot_images,
        assigned=assignments["assigned"],
        uploads=uploads,
        review=assignments["review"],
        conflicts=assignments["conflicts"],
    )
    session.save_page_snapshot(page, workspace.screenshots_taa_dir, "translate-page-after.png")
    return {
        "status": status,
        "summary": summary,
        "page_url": page.url,
        "shop_locale": shop_locale,
        "captured_slots": len(slot_images),
        "assigned_count": len(assignments["assigned"]),
        "uploaded_count": sum(1 for row in uploads if row.get("uploaded")),
        "failed_upload_count": sum(1 for row in uploads if not row.get("uploaded")),
        "assigned": assignments["assigned"],
        "conflicts": assignments["conflicts"],
        "review": assignments["review"],
        "used_localized_ids": assignments["used_localized_ids"],
        "uploads": uploads,
    }
