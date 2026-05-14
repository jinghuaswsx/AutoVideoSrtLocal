from appcore.meta_hot_posts import service


def test_category_options_use_chinese_labels_with_english_values(monkeypatch):
    monkeypatch.setattr(service.store, "list_category_options", lambda: [])

    options = service.category_options()

    kitchenware = next(item for item in options if item["value"] == "Kitchenware")
    assert kitchenware["label"] == "厨房用品"
    assert kitchenware["label_en"] == "Kitchenware"
    assert kitchenware["label_zh"] == "厨房用品"


def test_build_list_response_adds_chinese_category_label(monkeypatch):
    monkeypatch.setattr(
        service.store,
        "list_hot_posts",
        lambda args: {
            "items": [
                {
                    "id": 1,
                    "category_l1": "Home Supplies",
                    "sku_prices_json": "[]",
                }
            ],
            "total": 1,
        },
    )

    payload = service.build_list_response({}).payload

    item = payload["items"][0]
    assert item["category_l1"] == "Home Supplies"
    assert item["category_l1_zh"] == "家居用品"


def test_build_list_response_prefers_translated_chinese_message(monkeypatch):
    monkeypatch.setattr(
        service.store,
        "list_hot_posts",
        lambda args: {
            "items": [
                {
                    "id": 1,
                    "message_html": "<p>Deep Clean. Zero Chemicals.</p>",
                    "message_zh_html": "深度清洁，无需化学剂。",
                    "message_zh_status": "done",
                    "sku_prices_json": "[]",
                }
            ],
            "total": 1,
        },
    )

    payload = service.build_list_response({}).payload

    item = payload["items"][0]
    assert item["message_html"] == "深度清洁，无需化学剂。"
    assert item["message_source_html"] == "<p>Deep Clean. Zero Chemicals.</p>"
    assert item["message_zh_status"] == "done"


def test_build_translate_response_runs_message_translation_tick(monkeypatch):
    captured = {}

    def fake_translation_tick_once(**kwargs):
        captured.update(kwargs)
        return {"scanned": 2, "done": 2, "failed": 0}

    monkeypatch.setattr(
        "appcore.meta_hot_posts.scheduler.translation_tick_once",
        fake_translation_tick_once,
    )

    result = service.build_translate_response(
        {"limit": "80", "per_item_delay_seconds": "1.5", "user_id": "7"}
    )

    assert result.status_code == 202
    assert result.payload["ok"] is True
    assert result.payload["result"] == {"scanned": 2, "done": 2, "failed": 0}
    assert captured == {"limit": 80, "user_id": 7, "per_item_delay_seconds": 1.5}
