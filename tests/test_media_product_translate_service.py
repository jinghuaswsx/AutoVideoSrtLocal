from __future__ import annotations


def test_product_translate_flask_response_returns_payload_and_status(authed_client_no_db):
    from web.services import media_product_translate as svc

    result = svc.ProductTranslateResponse({"task_id": "task-1"}, 202)

    with authed_client_no_db.application.app_context():
        response, status_code = svc.product_translate_flask_response(result)

    assert status_code == 202
    assert response.get_json() == {"task_id": "task-1"}


def test_build_product_translation_tasks_response_syncs_and_projects_tasks():
    from web.services import media_product_translate as svc

    calls = []

    result = svc.build_product_translation_tasks_response(
        product_id=123,
        scope_user_id=7,
        list_product_task_ids_fn=lambda user_id, product_id: calls.append(
            ("task_ids", user_id, product_id)
        )
        or ["bt-1", "bt-2"],
        sync_task_with_children_once_fn=lambda task_id, user_id=None: calls.append(
            ("sync", task_id, user_id)
        )
        or {"actions": []},
        list_product_tasks_fn=lambda user_id, product_id: calls.append(
            ("project", user_id, product_id)
        )
        or [{"id": "bt-1"}],
    )

    assert result.status_code == 200
    assert result.payload == {"items": [{"id": "bt-1"}]}
    assert calls == [
        ("task_ids", 7, 123),
        ("sync", "bt-1", 7),
        ("sync", "bt-2", 7),
        ("project", 7, 123),
    ]


def test_build_product_translation_tasks_response_resumes_after_auto_voice(monkeypatch):
    from web.services import media_product_translate as svc

    scheduler_calls = []
    monkeypatch.setattr(
        svc,
        "start_bulk_scheduler_background",
        lambda *args, **kwargs: scheduler_calls.append((args, kwargs)) or True,
    )

    result = svc.build_product_translation_tasks_response(
        product_id=123,
        scope_user_id=7,
        list_product_task_ids_fn=lambda user_id, product_id: ["bt-1"],
        sync_task_with_children_once_fn=lambda task_id, user_id=None: {
            "actions": ["auto_confirm_voice"],
        },
        list_product_tasks_fn=lambda user_id, product_id: [{"id": "bt-1"}],
    )

    assert result.status_code == 200
    assert scheduler_calls == [
        (
            ("bt-1",),
            {
                "user_id": 7,
                "entrypoint": "medias.translation_tasks.sync",
                "action": "resume_after_auto_voice_confirm",
                "details": {"source": "medias_translation_tasks"},
            },
        )
    ]


def test_build_product_translate_response_maps_success_and_errors():
    from web.services import media_product_translate as svc

    success = svc.build_product_translate_response(
        svc.ProductTranslateResult(ok=True, status_code=202, task_id="task-xyz")
    )
    validation_error = svc.build_product_translate_response(
        svc.ProductTranslateResult(ok=False, status_code=400, error="target_langs required")
    )
    payload_error = svc.build_product_translate_response(
        svc.ProductTranslateResult(
            ok=False,
            status_code=409,
            error="product_not_listed",
            payload={"error": "product_not_listed", "message": "unlisted"},
        )
    )

    assert success.status_code == 202
    assert success.payload == {"task_id": "task-xyz"}
    assert validation_error.status_code == 400
    assert validation_error.payload == {"error": "target_langs required"}
    assert payload_error.status_code == 409
    assert payload_error.payload == {"error": "product_not_listed", "message": "unlisted"}


def test_start_product_translation_requires_raw_sources_for_video_content(monkeypatch):
    from web.services import media_product_translate as svc

    monkeypatch.setattr(svc.medias, "list_raw_sources", lambda product_id: [])

    result = svc.start_product_translation(
        user_id=1,
        user_name="admin",
        product_id=123,
        body={"raw_ids": [], "target_langs": ["de"], "content_types": ["videos"]},
        ip="127.0.0.1",
        user_agent="pytest",
    )

    assert result.ok is False
    assert result.status_code == 400
    assert result.error == "raw_ids 不能为空"


def test_start_product_translation_returns_readable_validation_errors(monkeypatch):
    from web.services import media_product_translate as svc

    monkeypatch.setattr(svc.medias, "list_raw_sources", lambda product_id: [{"id": 88}])
    monkeypatch.setattr(svc.medias, "is_valid_language", lambda lang: lang == "de")
    monkeypatch.setattr(
        svc.bulk_translate_runtime,
        "create_bulk_translate_task",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("create not reached")),
    )

    no_lang = svc.start_product_translation(
        user_id=1,
        user_name="admin",
        product_id=123,
        body={"raw_ids": [88], "target_langs": [], "content_types": ["copywriting"]},
        ip="",
        user_agent="",
    )
    bad_content_shape = svc.start_product_translation(
        user_id=1,
        user_name="admin",
        product_id=123,
        body={"raw_ids": [88], "target_langs": ["de"], "content_types": "copywriting"},
        ip="",
        user_agent="",
    )
    bad_raw = svc.start_product_translation(
        user_id=1,
        user_name="admin",
        product_id=123,
        body={"raw_ids": [99], "target_langs": ["de"], "content_types": ["videos"]},
        ip="",
        user_agent="",
    )
    bad_lang = svc.start_product_translation(
        user_id=1,
        user_name="admin",
        product_id=123,
        body={"raw_ids": [88], "target_langs": ["xx"], "content_types": ["copywriting"]},
        ip="",
        user_agent="",
    )
    bad_content = svc.start_product_translation(
        user_id=1,
        user_name="admin",
        product_id=123,
        body={"raw_ids": [88], "target_langs": ["de"], "content_types": ["bad"]},
        ip="",
        user_agent="",
    )

    assert no_lang.error == "target_langs 不能为空"
    assert bad_content_shape.error == "content_types 不能为空"
    assert bad_raw.error == "raw_ids 不属于该产品或已删除: [99]"
    assert bad_lang.error == "target_langs 不支持: xx"
    assert bad_content.error == "content_types 不支持: bad"


def test_start_product_translation_rejects_unlisted_product_before_listing(monkeypatch):
    from web.services import media_product_translate as svc

    list_calls = []
    monkeypatch.setattr(svc.medias, "is_product_listed", lambda product: False)
    monkeypatch.setattr(
        svc.medias,
        "list_raw_sources",
        lambda product_id: list_calls.append(product_id) or [],
    )
    monkeypatch.setattr(
        svc.bulk_translate_runtime,
        "create_bulk_translate_task",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("create not reached")),
    )

    result = svc.start_product_translation(
        user_id=1,
        user_name="admin",
        product_id=123,
        product={"id": 123, "listing_status": "下架"},
        body={"raw_ids": [88], "target_langs": ["de"], "content_types": ["videos"]},
        ip="127.0.0.1",
        user_agent="pytest",
    )

    assert result.ok is False
    assert result.status_code == 409
    assert result.error == "product_not_listed"
    assert result.payload == {
        "error": "product_not_listed",
        "message": "产品已下架，不能执行该操作",
    }
    assert list_calls == []


def test_start_product_translation_creates_starts_and_schedules_task(monkeypatch):
    from web.services import media_product_translate as svc

    created = {}
    started = []
    scheduled = []

    monkeypatch.setattr(
        svc.medias,
        "list_raw_sources",
        lambda product_id: [{"id": 88}, {"id": 89}],
    )
    monkeypatch.setattr(svc.medias, "is_valid_language", lambda lang: lang in {"de", "fr"})
    monkeypatch.setattr(
        svc.bulk_translate_runtime,
        "create_bulk_translate_task",
        lambda **kwargs: created.update(kwargs) or "task-xyz",
    )
    monkeypatch.setattr(
        svc.bulk_translate_runtime,
        "start_task",
        lambda task_id, user_id: started.append((task_id, user_id)),
    )
    monkeypatch.setattr(
        svc,
        "start_bulk_scheduler_background",
        lambda *args, **kwargs: scheduled.append((args, kwargs)) or True,
    )

    result = svc.start_product_translation(
        user_id=7,
        user_name="operator",
        product_id=123,
        body={
            "raw_ids": ["88"],
            "target_langs": ["de", "fr"],
            "content_types": ["copywriting", "videos"],
            "force_retranslate": True,
            "video_params": {"voice": "auto"},
        },
        ip="10.0.0.1",
        user_agent="pytest-UA",
    )

    assert result.ok is True
    assert result.task_id == "task-xyz"
    assert created["user_id"] == 7
    assert created["product_id"] == 123
    assert created["raw_source_ids"] == [88]
    assert created["target_langs"] == ["de", "fr"]
    assert created["content_types"] == ["copywriting", "videos"]
    assert created["force_retranslate"] is True
    assert created["video_params"] == {"voice": "auto"}
    assert created["initiator"] == {
        "user_id": 7,
        "user_name": "operator",
        "ip": "10.0.0.1",
        "user_agent": "pytest-UA",
        "source": "medias_raw_translate",
    }
    assert started == [("task-xyz", 7)]
    assert scheduled == [
        (
            ("task-xyz",),
            {
                "user_id": 7,
                "entrypoint": "medias.raw_translate",
                "action": "start",
                "details": {"source": "medias_raw_translate"},
            },
        )
    ]


def test_start_product_translation_passes_task_center_child_id(monkeypatch):
    from web.services import media_product_translate as svc

    created = {}
    resolved = {}
    monkeypatch.setattr(svc.medias, "list_raw_sources", lambda product_id: [])
    monkeypatch.setattr(svc.medias, "is_valid_language", lambda lang: lang == "de")
    monkeypatch.setattr(
        svc.bulk_translate_runtime,
        "create_bulk_translate_task",
        lambda **kwargs: created.update(kwargs) or "task-task-center",
    )
    monkeypatch.setattr(svc.bulk_translate_runtime, "start_task", lambda *args, **kwargs: None)
    monkeypatch.setattr(svc, "start_bulk_scheduler_background", lambda *args, **kwargs: True)

    result = svc.start_product_translation(
        user_id=7,
        user_name="operator",
        product_id=123,
        body={
            "raw_ids": [],
            "target_langs": ["de"],
            "content_types": ["copywriting"],
            "task_center_task_id": "456",
        },
        ip="10.0.0.1",
        user_agent="pytest-UA",
        resolve_child_task_id_fn=lambda **kwargs: resolved.update(kwargs) or 456,
    )

    assert result.ok is True
    assert resolved == {
        "task_id": 456,
        "product_id": 123,
        "lang": "de",
        "actor_user_id": 7,
        "is_admin": False,
    }
    assert created["task_center_task_id"] == 456


def test_start_product_translation_task_center_recreates_video_covers(monkeypatch):
    from web.services import media_product_translate as svc

    created = {}
    monkeypatch.setattr(svc.medias, "list_raw_sources", lambda product_id: [{"id": 88}])
    monkeypatch.setattr(svc.medias, "is_valid_language", lambda lang: lang == "it")
    monkeypatch.setattr(
        svc.bulk_translate_runtime,
        "create_bulk_translate_task",
        lambda **kwargs: created.update(kwargs) or "task-task-center-cover",
    )
    monkeypatch.setattr(svc.bulk_translate_runtime, "start_task", lambda *args, **kwargs: None)
    monkeypatch.setattr(svc, "start_bulk_scheduler_background", lambda *args, **kwargs: True)

    result = svc.start_product_translation(
        user_id=7,
        user_name="operator",
        product_id=123,
        body={
            "raw_ids": ["88"],
            "target_langs": ["it"],
            "content_types": ["video_covers", "videos"],
            "force_retranslate": False,
            "task_center_task_id": "456",
        },
        ip="10.0.0.1",
        user_agent="pytest-UA",
        resolve_child_task_id_fn=lambda **kwargs: 456,
    )

    assert result.ok is True
    assert created["task_center_task_id"] == 456
    assert created["force_retranslate"] is True


def test_start_product_translation_task_center_uses_bound_raw_source(monkeypatch):
    from web.services import media_product_translate as svc

    created = {}
    resolved = {}
    monkeypatch.setattr(
        svc.medias,
        "list_raw_sources",
        lambda product_id: [{"id": 88}, {"id": 999}],
    )
    monkeypatch.setattr(svc.medias, "is_valid_language", lambda lang: lang == "it")
    monkeypatch.setattr(
        svc.bulk_translate_runtime,
        "create_bulk_translate_task",
        lambda **kwargs: created.update(kwargs) or "task-bound-source",
    )
    monkeypatch.setattr(svc.bulk_translate_runtime, "start_task", lambda *args, **kwargs: None)
    monkeypatch.setattr(svc, "start_bulk_scheduler_background", lambda *args, **kwargs: True)

    result = svc.start_product_translation(
        user_id=7,
        user_name="operator",
        product_id=123,
        body={
            "raw_ids": ["999"],
            "target_langs": ["it"],
            "content_types": ["video_covers", "videos"],
            "task_center_task_id": "456",
        },
        ip="10.0.0.1",
        user_agent="pytest-UA",
        resolve_child_task_media_source_fn=lambda **kwargs: resolved.update(kwargs)
        or {
            "task_id": 456,
            "media_item_id": 1679,
            "source_raw_id": 88,
            "cover_object_key": "77/medias/123/item-cover.png",
        },
    )

    assert result.ok is True
    assert resolved == {
        "task_id": 456,
        "product_id": 123,
        "lang": "it",
        "actor_user_id": 7,
        "is_admin": False,
    }
    assert created["raw_source_ids"] == [88]
    assert created["task_center_task_id"] == 456


def test_start_product_translation_rejects_task_center_child_id_language_mismatch(monkeypatch):
    from web.services import media_product_translate as svc

    monkeypatch.setattr(svc.medias, "list_raw_sources", lambda product_id: [])
    monkeypatch.setattr(svc.medias, "is_valid_language", lambda lang: lang in {"de", "fr"})
    monkeypatch.setattr(
        svc.bulk_translate_runtime,
        "create_bulk_translate_task",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("create not reached")),
    )

    def reject_mismatch(**kwargs):
        raise svc.tasks_svc.StateError("child task language mismatch")

    result = svc.start_product_translation(
        user_id=238,
        user_name="operator",
        product_id=532,
        body={
            "raw_ids": [],
            "target_langs": ["fr"],
            "content_types": ["copywriting"],
            "task_center_task_id": 247,
        },
        ip="10.0.0.1",
        user_agent="pytest-UA",
        resolve_child_task_id_fn=reject_mismatch,
    )

    assert result.ok is False
    assert result.status_code == 400
    assert result.error == "child task language mismatch"


def test_start_product_translation_rejects_task_center_id_with_multiple_langs(monkeypatch):
    from web.services import media_product_translate as svc

    monkeypatch.setattr(svc.medias, "list_raw_sources", lambda product_id: [])
    monkeypatch.setattr(svc.medias, "is_valid_language", lambda lang: lang in {"de", "fr"})
    monkeypatch.setattr(
        svc.bulk_translate_runtime,
        "create_bulk_translate_task",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("create not reached")),
    )

    result = svc.start_product_translation(
        user_id=7,
        user_name="operator",
        product_id=123,
        body={
            "raw_ids": [],
            "target_langs": ["de", "fr"],
            "content_types": ["copywriting"],
            "task_center_task_id": 456,
        },
        ip="10.0.0.1",
        user_agent="pytest-UA",
    )

    assert result.ok is False
    assert result.status_code == 400
    assert result.error == "task_center_task_id requires exactly one target_lang"


def test_start_product_translation_keeps_default_content_types(monkeypatch):
    from web.services import media_product_translate as svc

    created = {}
    monkeypatch.setattr(svc.medias, "list_raw_sources", lambda product_id: [{"id": 88}])
    monkeypatch.setattr(svc.medias, "is_valid_language", lambda lang: lang == "de")
    monkeypatch.setattr(
        svc.bulk_translate_runtime,
        "create_bulk_translate_task",
        lambda **kwargs: created.update(kwargs) or "task-default",
    )
    monkeypatch.setattr(svc.bulk_translate_runtime, "start_task", lambda *args, **kwargs: None)
    monkeypatch.setattr(svc, "start_bulk_scheduler_background", lambda *args, **kwargs: True)

    result = svc.start_product_translation(
        user_id=1,
        user_name="admin",
        product_id=123,
        body={"raw_ids": [88], "target_langs": ["de"]},
        ip="",
        user_agent="",
    )

    assert result.ok is True
    assert created["content_types"] == ["copywriting", "detail_images", "video_covers", "videos"]
