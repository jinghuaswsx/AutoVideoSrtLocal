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
