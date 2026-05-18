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


def test_build_list_response_hydrates_completed_video_copyability(monkeypatch):
    monkeypatch.setattr(
        service.store,
        "list_hot_posts",
        lambda args: {
            "items": [
                {
                    "id": 1,
                    "sku_prices_json": "[]",
                    "video_copyability_analysis_id": 8,
                    "video_copyability_overall_score": 91,
                    "video_copyability_copyability_score": 94,
                    "video_copyability_meta_us_ad_fit_score": 89,
                    "video_copyability_product_fit_score": 88,
                    "video_copyability_compliance_risk_score": 12,
                    "video_copyability_recommendation": "copy",
                    "video_copyability_summary": "Strong hook.",
                    "video_copyability_provider": "gemini_vertex_adc",
                    "video_copyability_model": "gemini-3-flash-preview",
                    "video_copyability_analyzed_at": "2026-05-18 10:00:00",
                    "video_copyability_analysis_json": '{"hook":"clear"}',
                }
            ],
            "total": 1,
        },
    )

    payload = service.build_list_response({}).payload

    copyability = payload["items"][0]["video_copyability"]
    assert copyability["analysis_id"] == 8
    assert copyability["overall_score"] == 91
    assert copyability["copyability_score"] == 94
    assert copyability["meta_us_ad_fit_score"] == 89
    assert copyability["product_fit_score"] == 88
    assert copyability["compliance_risk_score"] == 12
    assert copyability["recommendation"] == "copy"
    assert copyability["summary"] == "Strong hook."
    assert copyability["provider"] == "gemini_vertex_adc"
    assert copyability["model"] == "gemini-3-flash-preview"
    assert copyability["analyzed_at"] == "2026-05-18 10:00:00"
    assert copyability["raw"] == {"hook": "clear"}


def test_build_today_new_response_hydrates_first_seen_items(monkeypatch):
    monkeypatch.setattr(
        service.store,
        "list_today_new_hot_posts",
        lambda args: {
            "items": [
                {
                    "id": 7,
                    "category_l1": "Home Supplies",
                    "sku_prices_json": "[]",
                    "first_seen_at": "2026-05-15 07:00:53",
                }
            ],
            "total": 1,
            "page": 1,
            "page_size": 50,
        },
    )

    payload = service.build_today_new_response({"page": "1"}).payload

    item = payload["items"][0]
    assert payload["total"] == 1
    assert item["first_seen_at"] == "2026-05-15 07:00:53"
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
    assert item["message_is_translated"] is True
    assert item["message_zh_status"] == "done"


def test_build_refresh_response_runs_full_sync_by_default(monkeypatch):
    captured = {}

    def fake_sync_tick_once(**kwargs):
        captured.update(kwargs)
        return {"posts": 2307, "stop_reason": "reported_total_reached"}

    monkeypatch.setattr(
        "appcore.meta_hot_posts.scheduler.sync_tick_once",
        fake_sync_tick_once,
    )

    result = service.build_refresh_response()

    assert result.status_code == 202
    assert result.payload["ok"] is True
    assert result.payload["result"]["posts"] == 2307
    assert captured == {}


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


def test_build_europe_fit_response_runs_unified_video_queue_with_current_user(monkeypatch):
    from appcore.meta_hot_posts import scheduler

    captured = {}

    def fake_video_analysis_queue_tick_once(**kwargs):
        captured.update(kwargs)
        return {"scanned": 3, "done": 3, "failed": 0}

    monkeypatch.setattr(
        "appcore.meta_hot_posts.scheduler.video_analysis_queue_tick_once",
        fake_video_analysis_queue_tick_once,
    )

    result = service.build_europe_fit_response({"limit": "30", "user_id": "7"})

    assert result.status_code == 202
    assert result.payload["ok"] is True
    assert result.payload["result"] == {"scanned": 3, "done": 3, "failed": 0}
    assert captured == {
        "limit": scheduler.SCHEDULED_VIDEO_ANALYSIS_QUEUE_LIMIT,
        "user_id": 7,
        "respect_rate_limit_circuit": False,
    }


def test_build_video_copyability_response_runs_unified_video_queue(monkeypatch):
    from appcore.meta_hot_posts import scheduler

    captured = {}

    def fake_video_analysis_queue_tick_once(**kwargs):
        captured.update(kwargs)
        return {"scanned": 2, "done": 2, "failed": 0}

    monkeypatch.setattr(
        "appcore.meta_hot_posts.scheduler.video_analysis_queue_tick_once",
        fake_video_analysis_queue_tick_once,
    )

    result = service.build_video_copyability_response({"limit": "50", "user_id": "7"})

    assert result.status_code == 202
    assert result.payload["ok"] is True
    assert result.payload["result"] == {"scanned": 2, "done": 2, "failed": 0}
    assert captured == {
        "limit": scheduler.SCHEDULED_VIDEO_ANALYSIS_QUEUE_LIMIT,
        "user_id": 7,
        "respect_rate_limit_circuit": False,
    }


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
                "local_video_cover_path": "meta_hot_posts/video_covers/1/thumbnail.jpg",
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
    assert item["local_video_cover_url"] == "/xuanpin/api/meta-hot-posts/1/local-video-cover"
    assert item["europe_fit_score"] == 94
    assert item["europe_fit_best_countries"] == ["DE", "FR"]
    assert item["europe_fit_strengths"] == ["clear demo"]
    assert item["europe_fit_risks"] == ["minor English text"]
    assert item["europe_fit_required_changes"] == []


def test_build_list_response_prefers_persisted_local_video_metadata(monkeypatch):
    monkeypatch.setattr(
        service.store,
        "list_hot_posts",
        lambda args: {
            "items": [
                {
                    "id": 1,
                    "sku_prices_json": "[]",
                    "raw_json": '{"videoDuration": 63000}',
                    "local_video_status": "downloaded",
                    "local_video_path": "meta_hot_posts/videos/1.mp4",
                    "local_video_duration_seconds": 22.4,
                    "local_video_cover_path": "meta_hot_posts/video_covers/1/thumbnail.jpg",
                }
            ],
            "total": 1,
            "page": 1,
            "page_size": 50,
        },
    )

    payload = service.build_list_response({}).payload

    item = payload["items"][0]
    assert item["video_duration_seconds"] == 22
    assert item["local_video_url"] == "/xuanpin/api/meta-hot-posts/1/local-video"
    assert item["local_video_cover_url"] == "/xuanpin/api/meta-hot-posts/1/local-video-cover"


def test_build_list_response_can_fallback_to_raw_json_duration_for_online_only(monkeypatch):
    monkeypatch.setattr(
        service.store,
        "list_hot_posts",
        lambda args: {
            "items": [
                {
                    "id": 1,
                    "sku_prices_json": "[]",
                    "raw_json": '{"videoDuration": 63000}',
                }
            ],
            "total": 1,
            "page": 1,
            "page_size": 50,
        },
    )

    payload = service.build_list_response({}).payload

    assert payload["items"][0]["video_duration_seconds"] == 63


def test_hydrate_generates_tos_video_and_cover_urls_when_backup_enabled(monkeypatch):
    monkeypatch.setattr(service.store, "list_hot_posts", lambda args: {
        "items": [
            {
                "id": 9,
                "sku_prices_json": "[]",
                "local_video_status": "downloaded",
                "local_video_path": "meta_hot_posts/videos/9.mp4",
                "local_video_cover_path": "meta_hot_posts/video_covers/9/thumbnail.jpg",
            }
        ],
        "total": 1,
    })
    monkeypatch.setattr("config.TOS_BACKUP_ENABLED", True)
    monkeypatch.setattr(
        "appcore.tos_backup_storage.generate_signed_download_url",
        lambda key: f"https://tos.example/{key}",
    )
    monkeypatch.setattr(
        "appcore.meta_hot_posts.tos_sync.backup_object_key_for_relative_path",
        lambda path: f"FILES/output/{path}",
    )

    item = service.build_list_response({}).payload["items"][0]

    assert item["tos_video_url"] == "https://tos.example/FILES/output/meta_hot_posts/videos/9.mp4"
    assert item["tos_video_cover_url"] == "https://tos.example/FILES/output/meta_hot_posts/video_covers/9/thumbnail.jpg"
