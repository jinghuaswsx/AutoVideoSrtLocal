from __future__ import annotations

from web.services.media_detail_translation import apply_detail_translate_task


def test_build_detail_translate_from_en_response_creates_task_with_static_sources():
    from web.services.media_detail_translation import build_detail_translate_from_en_response

    create_calls = []
    start_calls = []

    outcome = build_detail_translate_from_en_response(
        123,
        7,
        {"name": "Lamp", "product_code": "lamp"},
        {"lang": "de", "model_id": "custom-model", "concurrency_mode": "parallel"},
        parse_lang_fn=lambda body, default="": (body["lang"], None),
        default_concurrency_mode="sequential",
        output_dir="C:/tmp/output",
        list_detail_images_fn=lambda pid, lang: [
            {"id": 11, "object_key": "1/medias/1/a.jpg"},
            {"id": 12, "object_key": "1/medias/1/b.gif"},
            {"id": 13, "object_key": "1/medias/1/c.png", "content_type": "image/jpeg"},
            {"id": 14, "object_key": "1/medias/1/d.png", "content_type": "image/gif"},
        ],
        detail_images_is_gif_fn=lambda row: (
            str(row.get("object_key") or "").endswith(".gif")
            or str(row.get("content_type") or "").startswith("image/gif")
        ),
        get_prompts_for_lang_fn=lambda lang: {"detail": "Translate to {target_language_name}"},
        get_language_name_fn=lambda lang: {"de": "German"}[lang],
        default_model_id_fn=lambda: "default-model",
        compose_project_name_fn=lambda product_name, preset, lang_name: f"{product_name}-{preset}-{lang_name}",
        create_image_translate_fn=lambda task_id, task_dir, **kwargs: (
            create_calls.append((task_id, task_dir, kwargs)) or {"id": task_id}
        ),
        start_image_translate_runner_fn=lambda task_id, user_id: start_calls.append((task_id, user_id)),
    )

    assert outcome.status_code == 201
    assert outcome.error is None
    assert outcome.payload is not None
    assert outcome.payload["detail_url"] == f"/image-translate/{outcome.payload['task_id']}"
    assert len(create_calls) == 1
    task_id, task_dir, created = create_calls[0]
    assert task_id == outcome.payload["task_id"]
    assert task_dir.endswith(task_id)
    assert created["model_id"] == "custom-model"
    assert created["concurrency_mode"] == "parallel"
    assert [item["source_detail_image_id"] for item in created["items"]] == [11, 13]
    assert created["medias_context"]["source_detail_image_ids"] == [11, 13]
    assert start_calls == [(task_id, 7)]


def test_build_detail_translate_from_en_response_rejects_invalid_mode_before_listing():
    from web.services.media_detail_translation import build_detail_translate_from_en_response

    outcome = build_detail_translate_from_en_response(
        123,
        7,
        {"name": "Lamp"},
        {"lang": "de", "concurrency_mode": "fast"},
        parse_lang_fn=lambda body, default="": (body["lang"], None),
        default_concurrency_mode="parallel",
        output_dir="C:/tmp/output",
        list_detail_images_fn=lambda pid, lang: (_ for _ in ()).throw(AssertionError("list not reached")),
        detail_images_is_gif_fn=lambda row: False,
        get_prompts_for_lang_fn=lambda lang: {"detail": "Translate"},
        get_language_name_fn=lambda lang: lang,
        default_model_id_fn=lambda: "default-model",
        compose_project_name_fn=lambda *args: "project",
        create_image_translate_fn=lambda *args, **kwargs: None,
        start_image_translate_runner_fn=lambda *args, **kwargs: None,
    )

    assert outcome.status_code == 400
    assert outcome.error == "concurrency_mode must be sequential or parallel"
    assert outcome.payload is None


def test_build_detail_translate_from_en_response_rejects_only_gif_sources():
    from web.services.media_detail_translation import build_detail_translate_from_en_response

    create_calls = []
    outcome = build_detail_translate_from_en_response(
        123,
        7,
        {"name": "Lamp"},
        {"lang": "de"},
        parse_lang_fn=lambda body, default="": (body["lang"], None),
        default_concurrency_mode="parallel",
        output_dir="C:/tmp/output",
        list_detail_images_fn=lambda pid, lang: [{"id": 11, "object_key": "1/medias/1/a.gif"}],
        detail_images_is_gif_fn=lambda row: True,
        get_prompts_for_lang_fn=lambda lang: {"detail": "Translate"},
        get_language_name_fn=lambda lang: lang,
        default_model_id_fn=lambda: "default-model",
        compose_project_name_fn=lambda *args: "project",
        create_image_translate_fn=lambda *args, **kwargs: create_calls.append((args, kwargs)),
        start_image_translate_runner_fn=lambda *args, **kwargs: None,
    )

    assert outcome.status_code == 409
    assert "GIF" in outcome.error
    assert create_calls == []


def test_build_detail_translate_from_en_response_rejects_missing_prompt():
    from web.services.media_detail_translation import build_detail_translate_from_en_response

    create_calls = []
    outcome = build_detail_translate_from_en_response(
        123,
        7,
        {"name": "Lamp"},
        {"lang": "de"},
        parse_lang_fn=lambda body, default="": (body["lang"], None),
        default_concurrency_mode="parallel",
        output_dir="C:/tmp/output",
        list_detail_images_fn=lambda pid, lang: [{"id": 11, "object_key": "1/medias/1/a.jpg"}],
        detail_images_is_gif_fn=lambda row: False,
        get_prompts_for_lang_fn=lambda lang: {"detail": ""},
        get_language_name_fn=lambda lang: lang,
        default_model_id_fn=lambda: "default-model",
        compose_project_name_fn=lambda *args: "project",
        create_image_translate_fn=lambda *args, **kwargs: create_calls.append((args, kwargs)),
        start_image_translate_runner_fn=lambda *args, **kwargs: None,
    )

    assert outcome.status_code == 409
    assert "prompt" in outcome.error
    assert create_calls == []


def test_build_detail_translate_tasks_response_validates_queries_and_projects_rows():
    from web.services.media_detail_translation import build_detail_translate_tasks_response

    calls = []
    rows = [
        {
            "id": "img-1",
            "created_at": None,
            "state_json": '{"preset":"detail","status":"done","medias_context":{"entry":"medias_edit_detail","product_id":123,"target_lang":"de","apply_status":"applied"}}',
        },
        {
            "id": "img-2",
            "created_at": None,
            "state_json": '{"preset":"cover","medias_context":{"entry":"medias_edit_detail","product_id":123,"target_lang":"de"}}',
        },
    ]

    outcome = build_detail_translate_tasks_response(
        123,
        7,
        " DE ",
        is_valid_language_fn=lambda lang: lang == "de",
        query_tasks_fn=lambda sql, args: calls.append((sql, args)) or rows,
    )

    assert outcome.status_code == 200
    assert outcome.error is None
    assert outcome.payload is not None
    assert [item["task_id"] for item in outcome.payload["items"]] == ["img-1"]
    assert outcome.payload["items"][0]["detail_url"] == "/image-translate/img-1"
    assert calls == [
        (
            "SELECT id, created_at, state_json "
            "FROM projects "
            "WHERE user_id=%s AND type='image_translate' AND deleted_at IS NULL "
            "ORDER BY created_at DESC LIMIT 50",
            (7,),
        )
    ]


def test_build_detail_translate_tasks_response_rejects_invalid_language_before_query():
    from web.services.media_detail_translation import build_detail_translate_tasks_response

    outcome = build_detail_translate_tasks_response(
        123,
        7,
        "xx",
        is_valid_language_fn=lambda lang: False,
        query_tasks_fn=lambda sql, args: (_ for _ in ()).throw(AssertionError("query not reached")),
    )

    assert outcome.status_code == 400
    assert outcome.payload is None
    assert outcome.error == "涓嶆敮鎸佺殑璇: xx"


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
