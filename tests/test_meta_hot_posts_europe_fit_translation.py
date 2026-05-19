from appcore.meta_hot_posts import europe_fit_translation


def test_translate_europe_fit_invokes_google_adc_flash_lite_and_parses_json():
    calls = []

    def fake_invoke(use_case_code, **kwargs):
        calls.append((use_case_code, kwargs))
        return {
            "text": (
                '{"strengths":["演示清晰"],"risks":["英文字幕需要本土化"],'
                '"required_changes":["翻译字幕"],"reasoning":"适合欧洲投放，但需要字幕本土化。"}'
            )
        }

    translated = europe_fit_translation.translate_assessment(
        {
            "post_id": 8,
            "recommendation": "adapt_before_use",
            "best_countries_json": '["DE","FR"]',
            "strengths_json": '["clear demo"]',
            "risks_json": '["English captions"]',
            "required_changes_json": '["translate captions"]',
            "reasoning": "Good fit after localization.",
        },
        user_id=7,
        invoke_chat_fn=fake_invoke,
    )

    use_case_code, kwargs = calls[0]
    assert use_case_code == "meta_hot_posts.europe_fit_translate"
    assert kwargs["provider_override"] == "gemini_vertex_adc"
    assert kwargs["model_override"] == "gemini-3.1-flash-lite"
    assert kwargs["user_id"] == 7
    assert kwargs["temperature"] == 0.0
    assert kwargs["max_tokens"] == 700
    assert "clear demo" in kwargs["messages"][1]["content"]
    assert translated == {
        "strengths": ["演示清晰"],
        "risks": ["英文字幕需要本土化"],
        "required_changes": ["翻译字幕"],
        "reasoning": "适合欧洲投放，但需要字幕本土化。",
    }


def test_run_pending_europe_fit_translations_persists_success_failure_and_sleeps(monkeypatch):
    events = []
    sleeps = []

    monkeypatch.setattr(
        europe_fit_translation.store,
        "next_pending_europe_fit_translations",
        lambda limit: [
            {"post_id": 1, "strengths_json": '["clear demo"]'},
            {"post_id": 2, "strengths_json": '["weak demo"]'},
        ],
    )
    monkeypatch.setattr(
        europe_fit_translation.store,
        "mark_europe_fit_translation_running",
        lambda post_id: events.append(("mark", post_id)),
    )
    monkeypatch.setattr(
        europe_fit_translation.store,
        "finish_europe_fit_translation",
        lambda post_id, **kwargs: events.append(("finish", post_id, kwargs)),
    )

    def fake_translate(row, **kwargs):
        if row["post_id"] == 2:
            raise RuntimeError("quota exhausted")
        return {"strengths": ["演示清晰"], "risks": [], "required_changes": [], "reasoning": "适合测试。"}

    summary = europe_fit_translation.run_pending_europe_fit_translations(
        limit=120,
        user_id=9,
        per_item_delay_seconds=2,
        sleep_fn=lambda seconds: sleeps.append(seconds),
        translate_fn=fake_translate,
    )

    assert summary == {"scanned": 2, "done": 1, "failed": 1, "rate_limited": 0}
    assert events[0] == ("mark", 1)
    assert events[1][0:2] == ("finish", 1)
    assert events[1][2]["translated"]["strengths"] == ["演示清晰"]
    assert events[2] == ("mark", 2)
    assert events[3][0:2] == ("finish", 2)
    assert "quota exhausted" in events[3][2]["error_message"]
    assert sleeps == [2]


def test_run_pending_europe_fit_translations_stops_on_rate_limit(monkeypatch):
    events = []

    monkeypatch.setattr(
        europe_fit_translation.store,
        "next_pending_europe_fit_translations",
        lambda limit: [
            {"post_id": 1, "strengths_json": '["clear demo"]'},
            {"post_id": 2, "strengths_json": '["second"]'},
        ],
    )
    monkeypatch.setattr(
        europe_fit_translation.store,
        "mark_europe_fit_translation_running",
        lambda post_id: events.append(("mark", post_id)),
    )
    monkeypatch.setattr(
        europe_fit_translation.store,
        "finish_europe_fit_translation",
        lambda post_id, **kwargs: events.append(("finish", post_id, kwargs)),
    )

    def fail_429(row, **kwargs):
        raise RuntimeError("429 resource exhausted")

    summary = europe_fit_translation.run_pending_europe_fit_translations(
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
