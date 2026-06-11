import json


def test_translate_goods_info_invokes_gemini_flash_lite_with_category_context():
    from appcore.tabcut_selection import goods_translation

    calls = []

    def fake_invoke(use_case, **kwargs):
        calls.append((use_case, kwargs))
        return {
            "json": {
                "item_name_zh": "紫色牙齿美白贴套装",
                "item_name_zh_short": "紫色美白牙贴",
                "category_name_zh": "美妆个护 / 口腔护理",
                "category_l1_name_zh": "美妆个护",
                "category_l2_name_zh": "口腔护理",
                "category_l3_name_zh": "牙贴",
            }
        }

    result = goods_translation.translate_goods_info(
        {
            "item_id": "i1",
            "item_name": "Purple Teeth Whitening Strips",
            "category_l1_name": "Beauty & Personal Care",
            "category_l2_name": "Oral Care",
        },
        user_id=7,
        invoke_fn=fake_invoke,
    )

    assert result["item_name_zh_short"] == "紫色美白牙贴"
    use_case, kwargs = calls[0]
    assert use_case == "tabcut.translate_goods_info"
    assert kwargs["provider_override"] == "openrouter"
    assert kwargs["model_override"] == "google/gemini-3.1-flash-lite"
    assert kwargs["user_id"] == 7
    assert "Purple Teeth Whitening Strips" in kwargs["prompt"]
    assert "Beauty & Personal Care" in kwargs["prompt"]


def test_translate_goods_info_repairs_json_from_text_response():
    from appcore.tabcut_selection import goods_translation

    result = goods_translation.translate_goods_info(
        {"item_name": "Door lock", "category_l1_name": "Tools"},
        invoke_fn=lambda *args, **kwargs: {
            "text": "```json\n"
            + json.dumps(
                {
                    "item_name_zh": "智能门锁",
                    "item_name_zh_short": "智能门锁",
                    "category_l1_name_zh": "工具五金",
                },
                ensure_ascii=False,
            )
            + "\n```"
        },
    )

    assert result["item_name_zh"] == "智能门锁"
    assert result["item_name_zh_short"] == "智能门锁"
    assert result["category_l1_name_zh"] == "工具五金"


def test_goods_translation_tick_records_scheduled_run(monkeypatch):
    from appcore.tabcut_selection import scheduler

    events = []

    monkeypatch.setattr(scheduler.scheduled_tasks, "start_run", lambda task_code: events.append(("start", task_code)) or 42)
    monkeypatch.setattr(
        scheduler.scheduled_tasks,
        "finish_run",
        lambda run_id, **kwargs: events.append(("finish", run_id, kwargs)),
    )
    monkeypatch.setattr(
        scheduler.goods_translation,
        "translate_pending_goods",
        lambda **kwargs: {"scanned": 1, "done": 1, "failed": 0},
    )

    summary = scheduler.goods_translation_tick_once(limit=5, user_id=7)

    assert summary == {"scanned": 1, "done": 1, "failed": 0}
    assert events[0] == ("start", "tabcut_goods_translation_tick")
    assert events[1][0:2] == ("finish", 42)
    assert events[1][2]["status"] == "success"


def test_translate_pending_goods_resets_stale_running_rows(monkeypatch):
    from appcore.tabcut_selection import goods_translation

    calls = []

    monkeypatch.setattr(
        goods_translation.store,
        "reset_stale_running_goods_translations",
        lambda: calls.append("reset"),
    )
    monkeypatch.setattr(
        goods_translation.store,
        "next_pending_goods_translations",
        lambda **kwargs: calls.append(("next", kwargs)) or [],
    )

    summary = goods_translation.translate_pending_goods(limit=4)

    assert summary == {"scanned": 0, "done": 0, "failed": 0}
    assert calls == ["reset", ("next", {"limit": 4, "max_attempts": 3})]


def test_scheduler_register_adds_goods_translation_job(monkeypatch):
    from appcore.tabcut_selection import scheduler

    jobs = []

    def fake_add_controlled_job(sched, task_code, func, trigger, **kwargs):
        jobs.append((task_code, func.__name__, trigger, kwargs))

    monkeypatch.setattr(scheduler.scheduled_tasks, "add_controlled_job", fake_add_controlled_job)

    scheduler.register(object())

    job_by_code = {job[0]: job for job in jobs}
    assert job_by_code["tabcut_goods_translation_tick"][1] == "goods_translation_tick_once"
    assert job_by_code["tabcut_goods_translation_tick"][2] == "interval"
    assert job_by_code["tabcut_goods_translation_tick"][3]["minutes"] == 10


def test_build_goods_translation_response_creates_goods_if_missing(monkeypatch):
    from appcore.tabcut_selection import service

    created = []
    goods = []

    def fake_create_goods_from_candidate(item_id):
        created.append(item_id)
        goods.append({
            "item_id": item_id,
            "region": "US",
            "item_name": "Spray Air Cushion Massage Comb",
            "item_pic_url": "http://example.com/pic.jpg",
            "category_name": "Beauty",
            "category_l1_name": "Beauty",
            "category_l2_name": None,
            "category_l3_name": None,
            "item_name_zh": None,
            "item_name_zh_short": None,
            "category_name_zh": None,
            "category_l1_name_zh": None,
            "category_l2_name_zh": None,
            "category_l3_name_zh": None,
        })
        return True

    def fake_get_goods(item_id):
        for g in goods:
            if g["item_id"] == item_id:
                return g
        return None

    def fake_translate_goods_info(row, **kwargs):
        return {
            "item_name_zh": "气垫梳",
            "item_name_zh_short": "气垫梳",
            "category_name_zh": "美容个护",
        }

    monkeypatch.setattr(service.store, "get_goods", fake_get_goods)
    monkeypatch.setattr(service.store, "create_goods_from_candidate", fake_create_goods_from_candidate)
    monkeypatch.setattr(service.store, "mark_goods_translation_running", lambda *args, **kwargs: None)
    monkeypatch.setattr(service.store, "finish_goods_translation", lambda *args, **kwargs: None)
    monkeypatch.setattr(service.goods_translation, "translate_goods_info", fake_translate_goods_info)

    resp = service.build_goods_translation_response("i_missing")
    assert resp.payload["ok"] is True
    assert resp.payload["item"]["item_name_zh_short"] == "气垫梳"
    assert "i_missing" in created


def test_create_goods_from_candidate_extracts_correct_fields(monkeypatch):
    from appcore.tabcut_selection import store
    import json

    queries = []
    executed = []

    mock_candidate_json = json.dumps({
        "itemList": [
            {
                "itemName": "Spray Air Cushion Massage Comb",
                "itemCoverUrl": "http://example.com/comb.jpg"
            }
        ]
    })

    def fake_query(sql, params):
        queries.append((sql, params))
        if "FROM tabcut_video_candidates" in sql:
            return [{
                "region": "US",
                "category_l1_name": "Beauty & Personal Care",
                "category_l2_name": "Hair Care",
                "category_l3_name": "Hair Brushes",
                "primary_item_name": "Raw Name",
                "video_cover_url": "http://example.com/video_cover.jpg",
                "video_raw_json": mock_candidate_json
            }]
        return []

    def fake_execute(sql, params):
        executed.append((sql, params))

    success = store.create_goods_from_candidate("i_test", query_fn=fake_query, execute_fn=fake_execute)
    assert success is True

    insert_sql, insert_params = executed[0]
    assert "INSERT INTO tabcut_goods" in insert_sql
    assert insert_params[0] == "i_test"
    assert insert_params[1] == "US"
    assert insert_params[2] == "Spray Air Cushion Massage Comb"
    assert insert_params[3] == "http://example.com/comb.jpg"
    assert insert_params[4] == "Beauty & Personal Care"
    assert insert_params[5] == "Hair Care"
    assert insert_params[6] == "Hair Brushes"

