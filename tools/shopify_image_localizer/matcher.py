from __future__ import annotations

from collections import defaultdict

from link_check_desktop.image_compare import find_best_reference


def assign_images(
    slot_images: list[dict],
    reference_images: list[dict],
    localized_images: list[dict],
    *,
    min_score: float = 0.80,
    reserved_localized_ids: set[str] | None = None,
) -> dict:
    reference_paths = [row["local_path"] for row in reference_images if row.get("local_path")]
    reference_by_path = {row["local_path"]: row for row in reference_images if row.get("local_path")}
    reserved_ids = set(reserved_localized_ids or set())

    slot_reference_ids: dict[str, str] = {}
    slot_by_id = {row["slot_id"]: row for row in slot_images}
    review: list[dict] = []
    conflicts: list[dict] = []
    assigned: list[dict] = []

    if not reference_paths:
        return {
            "assigned": [],
            "conflicts": [],
            "review": [{"reason": "missing reference images"}],
            "used_localized_ids": [],
        }

    for slot in slot_images:
        best = find_best_reference(slot["local_path"], reference_paths)
        if best.get("status") != "matched" or float(best.get("score") or 0.0) < min_score:
            review.append({
                "slot_id": slot["slot_id"],
                "reason": "slot reference not matched",
                "score": float(best.get("score") or 0.0),
            })
            continue
        slot_reference_ids[slot["slot_id"]] = str(reference_by_path[best["reference_path"]]["id"])

    localized_by_reference: dict[str, list[dict]] = defaultdict(list)
    for item in localized_images:
        item_id = str(item.get("id") or "")
        if item_id in reserved_ids:
            continue
        local_path = str(item.get("local_path") or "")
        if not local_path:
            continue
        best = find_best_reference(local_path, reference_paths)
        if best.get("status") != "matched" or float(best.get("score") or 0.0) < min_score:
            review.append({
                "localized_id": item_id,
                "reason": "localized image not matched",
                "score": float(best.get("score") or 0.0),
            })
            continue
        reference_id = str(reference_by_path[best["reference_path"]]["id"])
        localized_by_reference[reference_id].append({
            **item,
            "score": float(best.get("score") or 0.0),
        })

    used_localized_ids: set[str] = set()
    for slot_id, reference_id in slot_reference_ids.items():
        candidates = sorted(
            localized_by_reference.get(reference_id) or [],
            key=lambda row: row["score"],
            reverse=True,
        )
        if not candidates:
            review.append({"slot_id": slot_id, "reason": "no localized candidate"})
            continue

        chosen = candidates[0]
        chosen_id = str(chosen.get("id") or "")
        if chosen_id in used_localized_ids:
            conflicts.append({
                "slot_id": slot_id,
                "localized_id": chosen_id,
                "reason": "localized image already used",
            })
            continue

        assigned.append({
            "slot_id": slot_id,
            "slot": slot_by_id[slot_id],
            "reference_id": reference_id,
            "localized_id": chosen_id,
            "local_path": str(chosen.get("local_path") or ""),
            "score": float(chosen.get("score") or 0.0),
        })
        used_localized_ids.add(chosen_id)

        for extra in candidates[1:]:
            conflicts.append({
                "slot_id": slot_id,
                "localized_id": str(extra.get("id") or ""),
                "reason": "duplicate localized candidate",
            })

    return {
        "assigned": assigned,
        "conflicts": conflicts,
        "review": review,
        "used_localized_ids": sorted(used_localized_ids),
    }
