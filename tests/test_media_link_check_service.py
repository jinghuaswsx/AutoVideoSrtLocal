from __future__ import annotations

from pathlib import Path


def test_link_check_create_response_collects_references_and_starts_runner(monkeypatch, tmp_path):
    from web.services import media_link_check

    created = {}
    updated = {}
    downloads = []
    started = []

    monkeypatch.setattr(media_link_check.medias, "is_valid_language", lambda code: code == "de")
    monkeypatch.setattr(
        media_link_check.medias,
        "get_language",
        lambda code: {"code": code, "name_zh": "德语", "enabled": 1},
    )
    monkeypatch.setattr(media_link_check.medias, "get_product_covers", lambda pid: {"de": "covers/de.jpg"})
    monkeypatch.setattr(
        media_link_check.medias,
        "list_detail_images",
        lambda pid, lang: [{"id": 11, "object_key": "details/de_1.jpg"}],
    )
    monkeypatch.setattr(
        media_link_check.medias,
        "set_product_link_check_task",
        lambda pid, lang, payload: updated.update({"pid": pid, "lang": lang, "payload": payload}) or 1,
    )

    class FakeStore:
        @staticmethod
        def create_link_check(task_id, task_dir, **kwargs):
            created.update({"task_id": task_id, "task_dir": task_dir, **kwargs})
            return {"id": task_id, "type": "link_check", "_user_id": 2}

    def fake_download(object_key, target_path):
        downloads.append((object_key, Path(target_path).name))

    result = media_link_check.build_product_link_check_create_response(
        product_id=7,
        body={"lang": "de", "link_url": "https://newjoyloo.com/de/products/demo"},
        user_id=2,
        output_dir=tmp_path,
        store_obj=FakeStore(),
        start_runner_fn=lambda task_id: started.append(task_id) or True,
        download_media_object_fn=fake_download,
        task_id_factory=lambda: "lc-fixed",
    )

    assert result.status_code == 202
    assert result.payload == {"task_id": "lc-fixed", "status": "queued", "reference_count": 2}
    assert created["target_language"] == "de"
    assert created["target_language_name"] == "德语"
    assert [item["filename"] for item in created["reference_images"]] == [
        "cover_de.jpg",
        "detail_001.jpg",
    ]
    assert downloads == [("covers/de.jpg", "cover_de.jpg"), ("details/de_1.jpg", "detail_001.jpg")]
    assert updated["pid"] == 7
    assert updated["lang"] == "de"
    assert updated["payload"]["task_id"] == "lc-fixed"
    assert started == ["lc-fixed"]


def test_link_check_create_response_rejects_invalid_inputs(monkeypatch, tmp_path):
    from web.services import media_link_check

    monkeypatch.setattr(media_link_check.medias, "is_valid_language", lambda code: False)

    invalid_lang = media_link_check.build_product_link_check_create_response(
        product_id=7,
        body={"lang": "zz", "link_url": "https://example.test"},
        user_id=2,
        output_dir=tmp_path,
        store_obj=object(),
        start_runner_fn=lambda task_id: True,
        download_media_object_fn=lambda object_key, target_path: None,
    )

    assert invalid_lang.status_code == 400
    assert invalid_lang.payload == {"error": "unsupported language: zz"}

    monkeypatch.setattr(media_link_check.medias, "is_valid_language", lambda code: True)
    invalid_url = media_link_check.build_product_link_check_create_response(
        product_id=7,
        body={"lang": "de", "link_url": "ftp://example.test"},
        user_id=2,
        output_dir=tmp_path,
        store_obj=object(),
        start_runner_fn=lambda task_id: True,
        download_media_object_fn=lambda object_key, target_path: None,
    )

    assert invalid_url.status_code == 400
    assert invalid_url.payload == {"error": "valid product link_url required"}


def test_link_check_summary_response_refreshes_associated_task(monkeypatch):
    from web.services import media_link_check

    persisted = []
    product = {
        "id": 7,
        "link_check_tasks_json": {
            "de": {
                "task_id": "lc-1",
                "status": "queued",
                "link_url": "https://x",
                "checked_at": "2026-05-06T12:00:00+00:00",
                "summary": {"overall_decision": "running"},
            }
        },
    }

    monkeypatch.setattr(media_link_check.medias, "is_valid_language", lambda code: code == "de")
    monkeypatch.setattr(media_link_check.medias, "parse_link_check_tasks_json", lambda raw: raw)
    monkeypatch.setattr(
        media_link_check.medias,
        "set_product_link_check_task",
        lambda pid, lang, payload: persisted.append((pid, lang, payload)) or 1,
    )

    class FakeStore:
        @staticmethod
        def get(task_id):
            return {
                "id": task_id,
                "type": "link_check",
                "_user_id": 2,
                "status": "done",
                "summary": {"overall_decision": "done", "pass_count": 2},
                "progress": {"total": 2},
                "resolved_url": "https://x/de",
                "page_language": "de",
            }

    result = media_link_check.build_product_link_check_summary_response(
        product=product,
        lang="de",
        user_id=2,
        store_obj=FakeStore(),
    )

    assert result.status_code == 200
    assert result.payload["task"]["task_id"] == "lc-1"
    assert result.payload["task"]["status"] == "done"
    assert result.payload["task"]["has_detail"] is True
    assert persisted[0][0:2] == (7, "de")
    assert persisted[0][2]["status"] == "done"


def test_link_check_detail_response_hides_foreign_or_missing_tasks(monkeypatch):
    from web.services import media_link_check

    product = {"id": 7, "link_check_tasks_json": {"de": {"task_id": "lc-1"}}}
    monkeypatch.setattr(media_link_check.medias, "is_valid_language", lambda code: code == "de")
    monkeypatch.setattr(media_link_check.medias, "parse_link_check_tasks_json", lambda raw: raw)

    class FakeStore:
        @staticmethod
        def get(task_id):
            return {"id": task_id, "type": "link_check", "_user_id": 3}

    result = media_link_check.build_product_link_check_detail_response(
        product=product,
        lang="de",
        user_id=2,
        store_obj=FakeStore(),
        serialize_task_fn=lambda task: {"id": task["id"]},
    )

    assert result.status_code == 404
    assert result.payload == {"error": "task not found"}
