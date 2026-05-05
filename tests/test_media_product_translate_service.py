from __future__ import annotations


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
