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


def test_build_list_response_hydrates_user_favorite_state(monkeypatch):
    captured = {}

    def fake_list(args, user_id=None):
        captured["args"] = args
        captured["user_id"] = user_id
        return {
            "items": [
                {
                    "id": 7,
                    "sku_prices_json": "[]",
                    "favorited_at": "2026-05-19 10:00:00",
                }
            ],
            "total": 1,
        }

    monkeypatch.setattr(service.store, "list_hot_posts", fake_list)

    payload = service.build_list_response({"page": "1"}, user_id=88).payload

    assert captured["user_id"] == 88
    item = payload["items"][0]
    assert item["is_favorited"] is True
    assert item["favorited_at"] == "2026-05-19 10:00:00"


def test_build_favorites_response_lists_current_user_favorites(monkeypatch):
    captured = {}

    def fake_list(args, user_id=None):
        captured["args"] = args
        captured["user_id"] = user_id
        return {
            "items": [{"id": 9, "sku_prices_json": "[]", "favorited_at": "2026-05-19"}],
            "total": 1,
            "page": 1,
            "page_size": 50,
            "sort": "creation_time",
        }

    monkeypatch.setattr(service.store, "list_favorite_hot_posts", fake_list)

    result = service.build_favorites_response({"sort": "creation_time"}, user_id=88)

    assert result.status_code == 200
    assert captured["user_id"] == 88
    assert result.payload["sort"] == "creation_time"
    assert result.payload["items"][0]["is_favorited"] is True


def test_build_favorite_response_toggles_current_user_favorite(monkeypatch):
    captured = {}

    def fake_set(post_id, *, user_id, favorited):
        captured["post_id"] = post_id
        captured["user_id"] = user_id
        captured["favorited"] = favorited
        return 1

    monkeypatch.setattr(service.store, "set_hot_post_favorite", fake_set)

    result = service.build_favorite_response(7, {"favorited": True}, user_id=88)

    assert result.status_code == 200
    assert result.payload == {"ok": True, "id": 7, "is_favorited": True}
    assert captured == {"post_id": 7, "user_id": 88, "favorited": True}


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


def test_build_ai_analysis_request_preview_for_europe_translation(monkeypatch):
    monkeypatch.setattr(
        service.store,
        "get_hot_post_ai_analysis_row",
        lambda post_id: {
            "id": post_id,
            "product_url": "https://example.com/products/socket",
            "product_title": "Flexible Socket Extender",
            "product_main_image_url": "https://cdn.example/socket.jpg",
            "local_video_status": "downloaded",
            "local_video_path": "meta_hot_posts/videos/7.mp4",
            "local_video_cover_path": "meta_hot_posts/video_covers/7/thumbnail.jpg",
            "message_html": "<p>Power every corner.</p>",
            "message_zh_html": "每个角落都能供电。",
            "sku_prices_json": "[]",
        },
    )

    result = service.build_ai_analysis_request_preview_response(7, "europe_translation")

    payload = result.payload["payload"]
    assert result.status_code == 200
    assert payload["mode"] == "europe_translation"
    assert payload["label"] == "欧洲AI分析"
    assert payload["use_case"] == "meta_hot_posts.europe_fit"
    assert payload["product"]["title"] == "Flexible Socket Extender"
    assert payload["media"][0]["role"] == "product_main_image"
    assert payload["media"][0]["url"] == "https://cdn.example/socket.jpg"
    assert payload["media"][1]["role"] == "video"
    assert payload["media"][1]["url"] == "/xuanpin/api/meta-hot-posts/7/local-video"
    assert "每个角落都能供电" in payload["prompts"]["user"]
    assert "translation_fit_score" in payload["response_schema"]["properties"]
    assert payload["full_payload_url"].endswith("/ai-analysis/europe_translation/request-payload")


def test_ai_analysis_mode_labels_match_market_categories():
    assert service._ai_analysis_mode_meta("us_copyability")["label"] == "美国AI分析"
    assert service._ai_analysis_mode_meta("europe_translation")["label"] == "欧洲AI分析"


def test_build_ai_analysis_run_short_circuits_existing_result(monkeypatch):
    calls = []

    monkeypatch.setattr(
        service.store,
        "get_hot_post_ai_analysis_row",
        lambda post_id: {
            "id": post_id,
            "sku_prices_json": "[]",
            "video_copyability_analysis_id": 8,
            "video_copyability_status": "done",
            "video_copyability_overall_score": 91,
            "video_copyability_summary": "Strong hook.",
            "video_copyability_analysis_json": '{"overall_score":91}',
        },
    )
    monkeypatch.setattr(
        "appcore.meta_hot_posts.video_copyability.analyze_video_copyability",
        lambda *args, **kwargs: calls.append(args) or {"overall_score": 1},
    )

    result = service.build_ai_analysis_run_response(7, "us_copyability", {"force": False}, user_id=3)

    assert result.status_code == 200
    assert result.payload["cached"] is True
    assert result.payload["has_result"] is True
    assert result.payload["result"]["summary"] == "Strong hook."
    assert calls == []


def test_build_ai_analysis_run_restores_state_on_rate_limit(monkeypatch):
    calls = []

    monkeypatch.setattr(
        service.store,
        "get_hot_post_ai_analysis_row",
        lambda post_id: {
            "id": post_id,
            "hot_post_id": post_id,
            "product_url": "https://example.com/products/socket",
            "local_video_status": "downloaded",
            "local_video_path": "meta_hot_posts/videos/7.mp4",
            "sku_prices_json": "[]",
        },
    )
    monkeypatch.setattr(service.store, "get_video_copyability_analysis_state", lambda post_id: None)
    monkeypatch.setattr(
        service.store,
        "ensure_video_copyability_candidate_for_post",
        lambda post_id: calls.append(("ensure_us", post_id)) or 1,
    )
    monkeypatch.setattr(
        service.store,
        "get_video_copyability_analysis_state",
        lambda post_id: {"id": 9, "status": "pending", "attempts": 0, "last_error": None},
    )
    monkeypatch.setattr(
        service.store,
        "mark_video_copyability_running",
        lambda analysis_id: calls.append(("running", analysis_id)) or 1,
    )
    monkeypatch.setattr(
        service.store,
        "restore_video_copyability_analysis_state",
        lambda analysis_id, **kwargs: calls.append(("restore", analysis_id, kwargs)) or 1,
    )
    monkeypatch.setattr(
        "appcore.meta_hot_posts.scheduler.resolve_billing_user_id",
        lambda user_id=None: user_id or 1,
    )

    def raise_rate_limit(*args, **kwargs):
        raise RuntimeError("429 rate limit exceeded")

    monkeypatch.setattr(
        "appcore.meta_hot_posts.video_copyability.analyze_video_copyability",
        raise_rate_limit,
    )

    result = service.build_ai_analysis_run_response(7, "us_copyability", {"force": True}, user_id=3)

    assert result.status_code == 429
    assert result.payload["rate_limited"] is True
    assert ("restore", 9, {"status": "pending", "attempts": 0, "last_error": None}) in calls


def test_build_ai_analysis_run_deletes_new_manual_candidate_on_rate_limit(monkeypatch):
    calls = []
    state_calls = iter(
        [
            None,
            {"id": 9, "status": "pending", "attempts": 0, "last_error": None},
        ]
    )

    monkeypatch.setattr(
        service.store,
        "get_hot_post_ai_analysis_row",
        lambda post_id: {
            "id": post_id,
            "hot_post_id": post_id,
            "product_url": "https://example.com/products/socket",
            "local_video_status": "downloaded",
            "local_video_path": "meta_hot_posts/videos/7.mp4",
            "sku_prices_json": "[]",
        },
    )
    monkeypatch.setattr(
        service.store,
        "get_video_copyability_analysis_state",
        lambda post_id: next(state_calls),
    )
    monkeypatch.setattr(
        service.store,
        "ensure_video_copyability_candidate_for_post",
        lambda post_id: calls.append(("ensure_us", post_id)) or 1,
    )
    monkeypatch.setattr(
        service.store,
        "mark_video_copyability_running",
        lambda analysis_id: calls.append(("running", analysis_id)) or 1,
    )
    monkeypatch.setattr(
        service.store,
        "delete_video_copyability_analysis_for_post",
        lambda post_id: calls.append(("delete_us", post_id)) or 1,
    )
    monkeypatch.setattr(
        "appcore.meta_hot_posts.scheduler.resolve_billing_user_id",
        lambda user_id=None: user_id or 1,
    )

    def raise_rate_limit(*args, **kwargs):
        raise RuntimeError("429 quota exhausted")

    monkeypatch.setattr(
        "appcore.meta_hot_posts.video_copyability.analyze_video_copyability",
        raise_rate_limit,
    )

    result = service.build_ai_analysis_run_response(7, "us_copyability", {"force": True}, user_id=3)

    assert result.status_code == 429
    assert result.payload["rate_limited"] is True
    assert ("delete_us", 7) in calls


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
