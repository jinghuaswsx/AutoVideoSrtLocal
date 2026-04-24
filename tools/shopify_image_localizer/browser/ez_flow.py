from __future__ import annotations

from tools.shopify_image_localizer import matcher
from tools.shopify_image_localizer.browser import session


EZ_IMAGE_SELECTORS = [
    ".image-card img.actual-image",
    "img.actual-image",
    ".image-card img[src^='blob:']",
    "img[src]",
]

EZ_FRAME_TITLES = ("EZ Product Translate",)


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
    scope = session.resolve_embedded_scope(
        page,
        label="EZ Product Image",
        frame_titles=EZ_FRAME_TITLES,
        status_cb=status_cb,
    )

    slot_images = session.capture_visible_images(
        scope,
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
    session.save_page_snapshot(page, workspace.screenshots_ez_dir, "ez-page-after.png")
    return {
        "status": status,
        "summary": summary,
        "page_url": page.url,
        "language_code": language_code,
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
