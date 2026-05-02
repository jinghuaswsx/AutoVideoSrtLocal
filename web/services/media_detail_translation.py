"""Payload builders for detail-image translation tasks."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from typing import Callable, Mapping, Sequence


@dataclass(frozen=True)
class DetailTranslateTaskPayload:
    task_id: str
    task_dir: str
    create_kwargs: dict


def build_detail_translate_task_payload(
    *,
    output_dir: str,
    user_id: int,
    product_id: int,
    product: Mapping[str, object],
    target_lang: str,
    target_language_name: str,
    prompt_template: str,
    source_rows: Sequence[Mapping[str, object]],
    model_id: str,
    concurrency_mode: str,
    compose_project_name: Callable[[str, str, str], str],
) -> DetailTranslateTaskPayload:
    task_id = uuid.uuid4().hex
    task_dir = os.path.join(output_dir, task_id)
    product_name = str(product.get("name") or "").strip()

    items = [
        {
            "idx": idx,
            "filename": os.path.basename(row.get("object_key") or "") or f"detail_{idx}.png",
            "src_tos_key": row["object_key"],
            "source_bucket": "media",
            "source_detail_image_id": row["id"],
        }
        for idx, row in enumerate(source_rows)
    ]
    medias_context = {
        "entry": "medias_edit_detail",
        "product_id": product_id,
        "source_lang": "en",
        "target_lang": target_lang,
        "source_bucket": "media",
        "source_detail_image_ids": [row["id"] for row in source_rows],
        "auto_apply_detail_images": True,
        "apply_status": "pending",
        "applied_at": "",
        "applied_detail_image_ids": [],
        "last_apply_error": "",
    }
    return DetailTranslateTaskPayload(
        task_id=task_id,
        task_dir=task_dir,
        create_kwargs={
            "user_id": user_id,
            "preset": "detail",
            "target_language": target_lang,
            "target_language_name": target_language_name,
            "model_id": model_id,
            "prompt": prompt_template.replace("{target_language_name}", target_language_name),
            "items": items,
            "product_name": product_name,
            "project_name": compose_project_name(product_name, "detail", target_language_name),
            "medias_context": medias_context,
            "concurrency_mode": concurrency_mode,
        },
    )
