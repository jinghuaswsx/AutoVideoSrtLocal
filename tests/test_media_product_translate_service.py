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
    assert "raw_ids" in result.error


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
