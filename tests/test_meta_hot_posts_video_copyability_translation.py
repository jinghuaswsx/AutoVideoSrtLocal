from appcore.meta_hot_posts import video_copyability_translation


def test_translate_summary_invokes_google_adc_flash_lite():
    calls = []

    def fake_invoke(use_case_code, **kwargs):
        calls.append((use_case_code, kwargs))
        return {"text": "强钩子，清楚展示了产品效果。"}

    translated = video_copyability_translation.translate_summary(
        {
            "analysis_id": 8,
            "recommendation": "copy",
            "summary": "Strong hook and clear product demonstration.",
            "analysis_json": '{"risk_notes":["avoid exaggerated claims"]}',
        },
        user_id=7,
        invoke_chat_fn=fake_invoke,
    )

    use_case_code, kwargs = calls[0]
    assert translated == "强钩子，清楚展示了产品效果。"
    assert use_case_code == "meta_hot_posts.video_copyability_translate"
    assert kwargs["provider_override"] == "gemini_vertex_adc"
    assert kwargs["model_override"] == "gemini-3.1-flash-lite"
    assert kwargs["user_id"] == 7
    assert kwargs["temperature"] == 0.0
    assert kwargs["max_tokens"] == 512
    assert "Strong hook" in kwargs["messages"][1]["content"]


def test_translate_summary_accepts_manual_openrouter_flash_lite_override():
    calls = []

    def fake_invoke(use_case_code, **kwargs):
        calls.append((use_case_code, kwargs))
        return {"text": "强钩子，清楚展示了产品效果。"}

    translated = video_copyability_translation.translate_summary(
        {
            "analysis_id": 8,
            "recommendation": "copy",
            "summary": "Strong hook and clear product demonstration.",
            "analysis_json": '{"copy_notes":["simple demo structure"]}',
        },
        user_id=7,
        provider_override="openrouter",
        model_override="google/gemini-3.1-flash-lite",
        billing_source="meta_hot_posts_manual_us_ai_translate_zh",
        invoke_chat_fn=fake_invoke,
    )

    use_case_code, kwargs = calls[0]
    assert translated == "强钩子，清楚展示了产品效果。"
    assert use_case_code == "meta_hot_posts.video_copyability_translate"
    assert kwargs["provider_override"] == "openrouter"
    assert kwargs["model_override"] == "google/gemini-3.1-flash-lite"
    assert kwargs["billing_extra"] == {"source": "meta_hot_posts_manual_us_ai_translate_zh"}


def test_run_pending_summary_translations_persists_success_failure_and_sleeps(monkeypatch):
    events = []
    sleeps = []

    monkeypatch.setattr(
        video_copyability_translation.store,
        "next_pending_video_copyability_summary_translations",
        lambda limit: [
            {"analysis_id": 1, "summary": "Strong hook."},
            {"analysis_id": 2, "summary": "Weak demo."},
        ],
    )
    monkeypatch.setattr(
        video_copyability_translation.store,
        "mark_video_copyability_summary_translation_running",
        lambda analysis_id: events.append(("mark", analysis_id)),
    )
    monkeypatch.setattr(
        video_copyability_translation.store,
        "finish_video_copyability_summary_translation",
        lambda analysis_id, **kwargs: events.append(("finish", analysis_id, kwargs)),
    )

    def fake_translate(row, **kwargs):
        if row["analysis_id"] == 2:
            raise RuntimeError("quota exhausted")
        return "强钩子。"

    summary = video_copyability_translation.run_pending_summary_translations(
        limit=120,
        user_id=9,
        per_item_delay_seconds=5,
        sleep_fn=lambda seconds: sleeps.append(seconds),
        translate_fn=fake_translate,
    )

    assert summary == {"scanned": 2, "done": 1, "failed": 1, "rate_limited": 0}
    assert events[0] == ("mark", 1)
    assert events[1][0:2] == ("finish", 1)
    assert events[1][2]["translated_summary"] == "强钩子。"
    assert events[2] == ("mark", 2)
    assert events[3][0:2] == ("finish", 2)
    assert "quota exhausted" in events[3][2]["error_message"]
    assert sleeps == [5]


def test_run_pending_summary_translations_stops_on_rate_limit(monkeypatch):
    events = []

    monkeypatch.setattr(
        video_copyability_translation.store,
        "next_pending_video_copyability_summary_translations",
        lambda limit: [
            {"analysis_id": 1, "summary": "Strong hook."},
            {"analysis_id": 2, "summary": "Second item."},
        ],
    )
    monkeypatch.setattr(
        video_copyability_translation.store,
        "mark_video_copyability_summary_translation_running",
        lambda analysis_id: events.append(("mark", analysis_id)),
    )
    monkeypatch.setattr(
        video_copyability_translation.store,
        "finish_video_copyability_summary_translation",
        lambda analysis_id, **kwargs: events.append(("finish", analysis_id, kwargs)),
    )

    def fail_429(row, **kwargs):
        raise RuntimeError("429 resource exhausted")

    summary = video_copyability_translation.run_pending_summary_translations(
        limit=120,
        user_id=9,
        per_item_delay_seconds=0,
        translate_fn=fail_429,
        stop_on_rate_limit=True,
    )

    assert summary["scanned"] == 1
    assert summary["done"] == 0
    assert summary["failed"] == 1
    assert summary["rate_limited"] == 1
    assert summary["stop_reason"] == "rate_limited"
    assert events[0] == ("mark", 1)
    assert events[1][0:2] == ("finish", 1)
