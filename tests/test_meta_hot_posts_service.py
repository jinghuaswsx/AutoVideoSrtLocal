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


def test_build_europe_fit_response_runs_scheduler_with_current_user(monkeypatch):
    captured = {}

    def fake_europe_fit_tick_once(**kwargs):
        captured.update(kwargs)
        return {"scanned": 3, "done": 3, "failed": 0}

    monkeypatch.setattr(
        "appcore.meta_hot_posts.scheduler.europe_fit_tick_once",
        fake_europe_fit_tick_once,
    )

    result = service.build_europe_fit_response({"limit": "30", "user_id": "7"})

    assert result.status_code == 202
    assert result.payload["ok"] is True
    assert result.payload["result"] == {"scanned": 3, "done": 3, "failed": 0}
    assert captured == {"limit": 30, "user_id": 7}


def test_build_europe_top_response_hydrates_assessment_fields(monkeypatch):
    monkeypatch.setattr(
        service.store,
        "list_top_europe_fit_materials",
        lambda limit=50: [
            {
                "id": 1,
                "sku_prices_json": "[]",
                "local_video_status": "downloaded",
                "local_video_path": "meta_hot_posts/videos/1.mp4",
                "europe_fit_score": 94,
                "europe_fit_recommendation": "direct_reuse",
                "europe_fit_best_countries_json": '["DE", "FR"]',
                "europe_fit_strengths_json": '["clear demo"]',
                "europe_fit_risks_json": '["minor English text"]',
                "europe_fit_required_changes_json": "[]",
            }
        ],
    )

    payload = service.build_europe_top_response({"limit": "50"}).payload

    item = payload["items"][0]
    assert payload["total"] == 1
    assert item["local_video_url"] == "/xuanpin/api/meta-hot-posts/1/local-video"
    assert item["europe_fit_score"] == 94
    assert item["europe_fit_best_countries"] == ["DE", "FR"]
    assert item["europe_fit_strengths"] == ["clear demo"]
    assert item["europe_fit_risks"] == ["minor English text"]
    assert item["europe_fit_required_changes"] == []
