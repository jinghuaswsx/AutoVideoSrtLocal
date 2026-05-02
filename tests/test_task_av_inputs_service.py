from __future__ import annotations

from web.services.task_av_inputs import (
    av_task_target_lang,
    av_step_maps,
    collect_av_source_language,
    collect_av_translate_inputs,
    merge_av_step_maps,
    validate_av_translate_inputs,
)


def test_collect_av_source_language_uses_explicit_source_language():
    updates, error = collect_av_source_language({"source_language": " EN "})

    assert error is None
    assert updates == {
        "source_language": "en",
        "user_specified_source_language": True,
    }


def test_collect_av_source_language_falls_back_to_current_task():
    updates, error = collect_av_source_language({}, current_task={"source_language": "zh"})

    assert error is None
    assert updates == {
        "source_language": "zh",
        "user_specified_source_language": True,
    }


def test_collect_av_source_language_rejects_missing_or_unsupported_language():
    missing_updates, missing_error = collect_av_source_language({})
    invalid_updates, invalid_error = collect_av_source_language({"source_language": "ru"})

    assert missing_updates == {}
    assert "source_language" in missing_error
    assert invalid_updates == {}
    assert "source_language" in invalid_error


def test_collect_av_translate_inputs_merges_flat_overrides_and_current_task():
    inputs = collect_av_translate_inputs(
        {
            "target_lang": "de",
            "target_market": "OTHER",
            "override_product_name": "Demo product",
            "override_selling_points": "Fast\nQuiet",
            "sync_granularity": "sentence",
        },
        current_task={
            "av_translate_inputs": {
                "target_language": "fr",
                "target_market": "US",
                "product_overrides": {"brand": "Existing brand"},
            }
        },
    )

    assert inputs["target_language"] == "de"
    assert inputs["target_language_name"] == "German"
    assert inputs["target_market"] == "OTHER"
    assert inputs["sync_granularity"] == "sentence"
    assert inputs["product_overrides"]["brand"] == "Existing brand"
    assert inputs["product_overrides"]["product_name"] == "Demo product"
    assert inputs["product_overrides"]["selling_points"] == ["Fast", "Quiet"]


def test_validate_av_translate_inputs_uses_injected_allowed_language_codes():
    assert (
        validate_av_translate_inputs(
            {"target_language": "de", "target_market": "OTHER"},
            available_target_language_codes={"de"},
            allowed_market_codes={"OTHER"},
        )
        is None
    )

    assert (
        validate_av_translate_inputs(
            {"target_language": "fi", "target_market": "OTHER"},
            available_target_language_codes={"de"},
            allowed_market_codes={"OTHER"},
        )
        == "target_language 非法"
    )

    assert (
        validate_av_translate_inputs(
            {"target_language": "de", "target_market": "MOON"},
            available_target_language_codes={"de"},
            allowed_market_codes={"OTHER"},
        )
        == "target_market 非法"
    )


def test_av_step_maps_builds_status_and_empty_messages_for_all_steps():
    steps, messages = av_step_maps(status="running")

    assert steps["extract"] == "running"
    assert steps["export"] == "running"
    assert messages["extract"] == ""
    assert messages["export"] == ""


def test_merge_av_step_maps_preserves_existing_values_and_fills_missing_defaults():
    steps, messages = merge_av_step_maps(
        {"extract": "done"},
        {"extract": "source ready"},
    )

    assert steps["extract"] == "done"
    assert steps["asr"] == "pending"
    assert messages["extract"] == "source ready"
    assert messages["asr"] == ""


def test_av_task_target_lang_prefers_task_field_then_inputs():
    assert (
        av_task_target_lang(
            {
                "target_lang": " JA ",
                "av_translate_inputs": {"target_language": "de"},
            }
        )
        == "ja"
    )
    assert av_task_target_lang({"av_translate_inputs": {"target_language": " DE "}}) == "de"
    assert av_task_target_lang({}) == ""
