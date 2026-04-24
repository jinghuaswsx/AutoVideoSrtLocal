from appcore import shopify_image_tasks as sit


def test_parse_status_map_accepts_dict_json_and_empty():
    assert sit.parse_status_map(None) == {}
    assert sit.parse_status_map("") == {}
    assert sit.parse_status_map({"it": {"replace_status": "confirmed"}}) == {
        "it": {"replace_status": "confirmed"}
    }
    assert sit.parse_status_map('{"it":{"replace_status":"confirmed"}}') == {
        "it": {"replace_status": "confirmed"}
    }


def test_status_for_lang_applies_defaults():
    status = sit.status_for_lang({}, "it")

    assert status["replace_status"] == "none"
    assert status["link_status"] == "unknown"
    assert status["last_error"] == ""
    assert status["confirmed_by"] is None
    assert status["confirmed_at"] is None


def test_update_lang_status_serializes_json(monkeypatch):
    captured = {}

    monkeypatch.setattr(
        sit.medias,
        "get_product",
        lambda product_id: {
            "id": product_id,
            "shopify_image_status_json": '{"it":{"replace_status":"failed"}}',
        },
    )

    def fake_update_product(product_id, **fields):
        captured["product_id"] = product_id
        captured["fields"] = fields
        return 1

    monkeypatch.setattr(sit.medias, "update_product", fake_update_product)

    sit.update_lang_status(
        7,
        "it",
        replace_status="auto_done",
        link_status="needs_review",
        last_error="",
    )

    assert captured["product_id"] == 7
    payload = captured["fields"]["shopify_image_status_json"]
    assert payload["it"]["replace_status"] == "auto_done"
    assert payload["it"]["link_status"] == "needs_review"
    assert payload["it"]["last_error"] == ""


def test_evaluate_candidate_requires_material_and_shopify_id(monkeypatch):
    monkeypatch.setattr(
        sit.medias,
        "get_product",
        lambda pid: {"id": pid, "product_code": "demo-rjc"},
    )
    monkeypatch.setattr(sit.medias, "is_valid_language", lambda lang: lang == "it")
    monkeypatch.setattr(sit.medias, "resolve_shopify_product_id", lambda pid: "855")
    monkeypatch.setattr(
        sit.medias,
        "list_shopify_localizer_images",
        lambda pid, lang: [{"id": f"{lang}-1"}] if lang == "en" else [],
    )

    result = sit.evaluate_candidate(7, "it")

    assert result["ready"] is False
    assert result["block_code"] == "localized_images_not_ready"


def test_create_or_reuse_pending_task_inserts_ready_task(monkeypatch):
    calls = []
    monkeypatch.setattr(sit, "find_active_task", lambda product_id, lang: None)
    monkeypatch.setattr(
        sit,
        "evaluate_candidate",
        lambda product_id, lang: {
            "ready": True,
            "product": {"id": product_id, "product_code": "demo-rjc"},
            "shopify_product_id": "855",
            "link_url": "https://newjoyloo.com/it/products/demo-rjc",
        },
    )
    monkeypatch.setattr(sit, "get_task", lambda task_id: None)
    monkeypatch.setattr(
        sit,
        "execute",
        lambda sql, args=(): calls.append((sql, args)) or 44,
    )
    monkeypatch.setattr(
        sit,
        "update_lang_status",
        lambda *args, **kwargs: calls.append(("status", args, kwargs)) or {},
    )

    task = sit.create_or_reuse_task(7, "it")

    assert task["id"] == 44
    assert calls[0][1][:5] == (
        7,
        "demo-rjc",
        "it",
        "855",
        "https://newjoyloo.com/it/products/demo-rjc",
    )
    assert calls[1][2]["replace_status"] == sit.REPLACE_PENDING


def test_claim_next_task_marks_running(monkeypatch):
    rows = [
        {
            "id": 9,
            "product_id": 7,
            "product_code": "demo-rjc",
            "lang": "it",
            "shopify_product_id": "855",
            "link_url": "url",
        }
    ]
    monkeypatch.setattr(sit, "query", lambda sql, args=(): rows if "SELECT" in sql else [])
    updates = []
    monkeypatch.setattr(
        sit,
        "execute",
        lambda sql, args=(): updates.append((sql, args)) or 1,
    )
    monkeypatch.setattr(
        sit,
        "update_lang_status",
        lambda *args, **kwargs: updates.append(("status", args, kwargs)) or {},
    )
    monkeypatch.setattr(sit, "get_task", lambda task_id: None)

    task = sit.claim_next_task("worker-1", lock_seconds=600)

    assert task["id"] == 9
    assert updates[0][1][0] == "worker-1"
    assert updates[1][2]["replace_status"] == sit.REPLACE_RUNNING


def test_complete_task_sets_auto_done_needs_review(monkeypatch):
    monkeypatch.setattr(
        sit,
        "get_task",
        lambda task_id: {"id": task_id, "product_id": 7, "lang": "it"},
    )
    calls = []
    monkeypatch.setattr(
        sit,
        "execute",
        lambda sql, args=(): calls.append((sql, args)) or 1,
    )
    monkeypatch.setattr(
        sit,
        "update_lang_status",
        lambda *args, **kwargs: calls.append(("status", args, kwargs)) or {},
    )

    sit.complete_task(9, {"carousel": {"ok": 11}, "detail": {"replacement_count": 4}})

    assert calls[0][1][0] == '{"carousel": {"ok": 11}, "detail": {"replacement_count": 4}}'
    assert calls[1][2]["replace_status"] == sit.REPLACE_AUTO_DONE
    assert calls[1][2]["link_status"] == sit.LINK_NEEDS_REVIEW
