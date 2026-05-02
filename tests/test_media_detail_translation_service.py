from __future__ import annotations

from web.services.media_detail_translation import apply_detail_translate_task


def test_apply_detail_translate_task_rejects_foreign_product_without_applying():
    calls = []
    outcome = apply_detail_translate_task(
        {
            "type": "image_translate",
            "_user_id": 1,
            "status": "done",
            "medias_context": {"product_id": 456, "target_lang": "de"},
        },
        task_id="img-apply",
        product_id=123,
        target_lang="de",
        user_id=1,
        is_running=lambda task_id: False,
        apply_translated_detail_images=lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    assert calls == []
    assert outcome.not_found is False
    assert outcome.status_code == 400
    assert outcome.error == "task does not belong to this product"
    assert outcome.payload is None


def test_apply_detail_translate_task_applies_done_task_and_builds_payload():
    apply_calls = []

    def fake_apply(task, *, allow_partial, user_id):
        apply_calls.append((task, allow_partial, user_id))
        return {
            "applied_ids": [11, 12],
            "skipped_failed_indices": [2],
            "apply_status": "partial_applied",
        }

    task = {
        "type": "image_translate",
        "_user_id": "1",
        "status": "done",
        "medias_context": {"product_id": "123", "target_lang": "de"},
    }
    outcome = apply_detail_translate_task(
        task,
        task_id="img-apply",
        product_id=123,
        target_lang="DE",
        user_id=1,
        is_running=lambda task_id: False,
        apply_translated_detail_images=fake_apply,
    )

    assert apply_calls == [(task, True, 1)]
    assert outcome.error is None
    assert outcome.status_code == 200
    assert outcome.payload == {
        "ok": True,
        "applied": 2,
        "skipped_failed": 1,
        "apply_status": "partial_applied",
        "applied_detail_image_ids": [11, 12],
    }
