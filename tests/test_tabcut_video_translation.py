import json


def test_translate_video_info_invokes_openrouter_gemini_15_flash():
    from appcore.tabcut_selection import video_translation

    calls = []

    def fake_invoke(use_case, **kwargs):
        calls.append((use_case, kwargs))
        return {
            "json": {
                "video_desc_zh": "这款收纳架适合厨房台面整理，安装简单。",
                "primary_item_name_zh": "厨房台面多功能收纳架",
            }
        }

    result = video_translation.translate_video_info(
        {
            "video_id": "v1",
            "video_desc": "This rack keeps your kitchen counter organized.",
            "primary_item_name": "Kitchen Counter Organizer Rack",
            "author_name": "demo_creator",
            "primary_item_id": "i1",
        },
        user_id=7,
        invoke_fn=fake_invoke,
    )

    assert result["video_desc_zh"] == "这款收纳架适合厨房台面整理，安装简单。"
    assert result["primary_item_name_zh"] == "厨房台面多功能收纳架"
    use_case, kwargs = calls[0]
    assert use_case == "tabcut.translate_video_info"
    assert kwargs["provider_override"] == "openrouter"
    assert kwargs["model_override"] == "google/gemini-1.5-flash"
    assert kwargs["user_id"] == 7
    assert "This rack keeps your kitchen counter organized." in kwargs["prompt"]
    assert "Kitchen Counter Organizer Rack" in kwargs["prompt"]


def test_translate_video_info_repairs_json_text_response():
    from appcore.tabcut_selection import video_translation

    result = video_translation.translate_video_info(
        {"video_id": "v1", "video_desc": "Best gadget for camping", "primary_item_name": "Camping Light"},
        invoke_fn=lambda *args, **kwargs: {
            "text": "```json\n"
            + json.dumps(
                {
                    "video_desc_zh": "露营时很好用的小工具",
                    "primary_item_name_zh": "露营灯",
                },
                ensure_ascii=False,
            )
            + "\n```"
        },
    )

    assert result == {
        "video_desc_zh": "露营时很好用的小工具",
        "primary_item_name_zh": "露营灯",
    }


def test_translate_pending_videos_resets_stale_and_processes_batch(monkeypatch):
    from appcore.tabcut_selection import video_translation

    calls = []
    rows = [
        {
            "video_id": "v1",
            "video_desc": "Demo video copy",
            "primary_item_name": "Demo product title",
            "zh_translation_attempts": 0,
        }
    ]

    monkeypatch.setattr(
        video_translation.store,
        "reset_stale_running_video_translations",
        lambda: calls.append("reset"),
    )
    monkeypatch.setattr(
        video_translation.store,
        "next_pending_video_translations",
        lambda **kwargs: calls.append(("next", kwargs)) or rows,
    )
    monkeypatch.setattr(
        video_translation.store,
        "mark_video_translation_running",
        lambda video_id: calls.append(("running", video_id)),
    )
    monkeypatch.setattr(
        video_translation,
        "translate_video_info",
        lambda row, **kwargs: calls.append(("translate", row["video_id"], kwargs["user_id"]))
        or {"video_desc_zh": "中文文案", "primary_item_name_zh": "中文商品名"},
    )
    monkeypatch.setattr(
        video_translation.store,
        "finish_video_translation",
        lambda video_id, **kwargs: calls.append(("finish", video_id, kwargs)),
    )

    summary = video_translation.translate_pending_videos(limit=10, user_id=9)

    assert summary == {"scanned": 1, "done": 1, "failed": 0}
    assert calls[0] == "reset"
    assert calls[1] == ("next", {"limit": 10, "max_attempts": 3})
    assert calls[2] == ("running", "v1")
    assert calls[3] == ("translate", "v1", 9)
    assert calls[4][0:2] == ("finish", "v1")
    assert calls[4][2]["payload"]["video_desc_zh"] == "中文文案"


def test_video_translation_tick_records_scheduled_run(monkeypatch):
    from appcore.tabcut_selection import scheduler

    events = []

    monkeypatch.setattr(scheduler.scheduled_tasks, "start_run", lambda task_code: events.append(("start", task_code)) or 42)
    monkeypatch.setattr(
        scheduler.scheduled_tasks,
        "finish_run",
        lambda run_id, **kwargs: events.append(("finish", run_id, kwargs)),
    )
    monkeypatch.setattr(
        scheduler.video_translation,
        "translate_pending_videos",
        lambda **kwargs: {"scanned": 1, "done": 1, "failed": 0},
    )

    summary = scheduler.video_translation_tick_once(limit=10, user_id=7)

    assert summary == {"scanned": 1, "done": 1, "failed": 0}
    assert events[0] == ("start", "tabcut_video_translation_tick")
    assert events[1][0:2] == ("finish", 42)
    assert events[1][2]["status"] == "success"


def test_scheduler_register_adds_video_translation_job(monkeypatch):
    from appcore.tabcut_selection import scheduler

    jobs = []

    def fake_add_controlled_job(sched, task_code, func, trigger, **kwargs):
        jobs.append((task_code, func.__name__, trigger, kwargs))

    monkeypatch.setattr(scheduler.scheduled_tasks, "add_controlled_job", fake_add_controlled_job)

    scheduler.register(object())

    job_by_code = {job[0]: job for job in jobs}
    assert job_by_code["tabcut_video_translation_tick"][1] == "video_translation_tick_once"
    assert job_by_code["tabcut_video_translation_tick"][2] == "interval"
    assert job_by_code["tabcut_video_translation_tick"][3]["minutes"] == 10
