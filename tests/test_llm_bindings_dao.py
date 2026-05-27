from unittest.mock import patch

import pytest

from appcore import llm_bindings


def test_resolve_uses_default_when_db_empty_and_seeds():
    """DB 无记录时返回默认值，并 seed 写回 DB（对齐 llm_prompt_configs 行为）。"""
    with patch("appcore.llm_bindings.query_one", return_value=None), \
         patch("appcore.llm_bindings.execute") as m_exec:
        result = llm_bindings.resolve("video_score.run")
    assert result["provider"] == "gemini_aistudio"
    assert result["model"] == "gemini-3.5-flash"
    assert result["source"] == "default"
    # 被 seed 回 DB
    assert m_exec.called
    sql = m_exec.call_args[0][0]
    assert "INSERT INTO llm_use_case_bindings" in sql
    assert "ON DUPLICATE KEY UPDATE" in sql


def test_resolve_returns_db_value_when_present():
    row = {
        "provider_code": "doubao",
        "model_id": "doubao-custom-model",
        "extra_config": None,
        "enabled": 1,
    }
    with patch("appcore.llm_bindings.query_one", return_value=row), \
         patch("appcore.llm_bindings.execute") as m_exec:
        result = llm_bindings.resolve("copywriting.generate")
    assert result["provider"] == "doubao"
    assert result["model"] == "doubao-custom-model"
    assert result["source"] == "db"
    # DB 命中不应再 seed
    assert not m_exec.called


def test_resolve_normalizes_non_meta_video_adc_binding_to_aistudio():
    row = {
        "provider_code": "gemini_vertex_adc",
        "model_id": "google/gemini-3.5-flash",
        "extra_config": None,
        "enabled": 1,
    }
    with patch("appcore.llm_bindings.query_one", return_value=row), \
         patch("appcore.llm_bindings.execute") as m_exec:
        result = llm_bindings.resolve("material_evaluation.evaluate")
    assert result["provider"] == "gemini_aistudio"
    assert result["model"] == "gemini-3.5-flash"
    assert result["source"] == "db"
    assert not m_exec.called


def test_resolve_maps_meta_video_analysis_adc_binding_to_openrouter():
    row = {
        "provider_code": "gemini_vertex_adc",
        "model_id": "gemini-3.5-flash",
        "extra_config": None,
        "enabled": 1,
    }
    with patch("appcore.llm_bindings.query_one", return_value=row):
        result = llm_bindings.resolve("meta_hot_posts.video_copyability")
    assert result["provider"] == "openrouter"
    assert result["model"] == "google/gemini-3.5-flash"


def test_resolve_keeps_voice_selection_adc_binding():
    row = {
        "provider_code": "gemini_vertex_adc",
        "model_id": "google/gemini-3.5-flash",
        "extra_config": None,
        "enabled": 1,
    }
    with patch("appcore.llm_bindings.query_one", return_value=row):
        result = llm_bindings.resolve("voice_selection.assess")
    assert result["provider"] == "gemini_vertex_adc"
    assert result["model"] == "gemini-3.5-flash"


@pytest.mark.parametrize("use_case", ["video_score.run", "video_csk.analyze"])
def test_resolve_keeps_video_analysis_adc_binding(use_case):
    row = {
        "provider_code": "gemini_vertex_adc",
        "model_id": "google/gemini-3.5-flash",
        "extra_config": None,
        "enabled": 1,
    }
    with patch("appcore.llm_bindings.query_one", return_value=row):
        result = llm_bindings.resolve(use_case)
    assert result["provider"] == "gemini_vertex_adc"
    assert result["model"] == "gemini-3.5-flash"


def test_resolve_maps_meta_message_translation_adc_binding_to_openrouter():
    row = {
        "provider_code": "gemini_vertex_adc",
        "model_id": "gemini-3-flash-preview",
        "extra_config": None,
        "enabled": 1,
    }
    with patch("appcore.llm_bindings.query_one", return_value=row):
        result = llm_bindings.resolve("meta_hot_posts.translate_message")
    assert result["provider"] == "openrouter"
    assert result["model"] == "google/gemini-3-flash-preview"


def test_resolve_maps_meta_video_copyability_translate_adc_binding_to_openrouter():
    row = {
        "provider_code": "gemini_vertex_adc",
        "model_id": "gemini-3.1-flash-lite",
        "extra_config": None,
        "enabled": 1,
    }
    with patch("appcore.llm_bindings.query_one", return_value=row):
        result = llm_bindings.resolve("meta_hot_posts.video_copyability_translate")
    assert result["provider"] == "openrouter"
    assert result["model"] == "google/gemini-3.1-flash-lite"


def test_resolve_maps_meta_europe_fit_translate_adc_binding_to_openrouter():
    row = {
        "provider_code": "gemini_vertex_adc",
        "model_id": "gemini-3.1-flash-lite",
        "extra_config": None,
        "enabled": 1,
    }
    with patch("appcore.llm_bindings.query_one", return_value=row):
        result = llm_bindings.resolve("meta_hot_posts.europe_fit_translate")
    assert result["provider"] == "openrouter"
    assert result["model"] == "google/gemini-3.1-flash-lite"


def test_resolve_disabled_falls_back_to_default_without_reseeding():
    """enabled=0 视为无绑定，返回默认，但不 seed（避免覆盖管理员意图）。"""
    row = {
        "provider_code": "doubao",
        "model_id": "custom",
        "extra_config": None,
        "enabled": 0,
    }
    with patch("appcore.llm_bindings.query_one", return_value=row), \
         patch("appcore.llm_bindings.execute") as m_exec:
        result = llm_bindings.resolve("video_score.run")
    assert result["provider"] == "gemini_aistudio"
    assert result["model"] == "gemini-3.5-flash"
    assert result["source"] == "default"
    assert not m_exec.called


def test_resolve_parses_extra_config_json_string():
    row = {
        "provider_code": "openrouter",
        "model_id": "openai/gpt-4o",
        "extra_config": '{"max_retries": 5}',
        "enabled": 1,
    }
    with patch("appcore.llm_bindings.query_one", return_value=row):
        result = llm_bindings.resolve("copywriting.generate")
    assert result["extra"] == {"max_retries": 5}


def test_resolve_unknown_use_case_raises():
    with patch("appcore.llm_bindings.query_one", return_value=None):
        with pytest.raises(KeyError):
            llm_bindings.resolve("nonexistent.case")


def test_upsert_calls_insert_on_duplicate_update():
    with patch("appcore.llm_bindings.execute") as m_exec:
        llm_bindings.upsert(
            "video_score.run",
            provider="gemini_aistudio", model="gemini-3.5-flash",
            updated_by=1,
        )
    assert m_exec.called
    sql = m_exec.call_args[0][0]
    assert "ON DUPLICATE KEY UPDATE" in sql


def test_upsert_rejects_unknown_use_case():
    with pytest.raises(KeyError):
        llm_bindings.upsert("nonexistent.case", provider="openrouter",
                            model="x", updated_by=1)


def test_upsert_serializes_extra_dict():
    with patch("appcore.llm_bindings.execute") as m_exec:
        llm_bindings.upsert(
            "video_score.run",
            provider="gemini_vertex", model="gemini-3.5-flash",
            extra={"k": "v"}, updated_by=7,
        )
    args = m_exec.call_args[0][1]
    # (use_case_code, provider, model, extra_json, enabled, updated_by)
    assert args[3] == '{"k": "v"}'
    assert args[4] == 1
    assert args[5] == 7


def test_upsert_normalizes_non_meta_video_adc_binding_to_aistudio():
    with patch("appcore.llm_bindings.execute") as m_exec:
        llm_bindings.upsert(
            "video_cover.video_analysis",
            provider="gemini_vertex_adc",
            model="google/gemini-3.5-flash",
            updated_by=7,
        )
    args = m_exec.call_args[0][1]
    assert args[1] == "gemini_aistudio"
    assert args[2] == "gemini-3.5-flash"


def test_upsert_maps_meta_message_translation_adc_binding_to_openrouter():
    with patch("appcore.llm_bindings.execute") as m_exec:
        llm_bindings.upsert(
            "meta_hot_posts.translate_message",
            provider="gemini_vertex_adc",
            model="gemini-3-flash-preview",
            updated_by=7,
        )
    args = m_exec.call_args[0][1]
    assert args[1] == "openrouter"
    assert args[2] == "google/gemini-3-flash-preview"


def test_upsert_keeps_voice_selection_adc_binding():
    with patch("appcore.llm_bindings.execute") as m_exec:
        llm_bindings.upsert(
            "voice_selection.assess",
            provider="gemini_vertex_adc",
            model="google/gemini-3.5-flash",
            updated_by=7,
        )
    args = m_exec.call_args[0][1]
    assert args[1] == "gemini_vertex_adc"
    assert args[2] == "gemini-3.5-flash"


@pytest.mark.parametrize("use_case", ["video_score.run", "video_csk.analyze"])
def test_upsert_keeps_video_analysis_adc_binding(use_case):
    with patch("appcore.llm_bindings.execute") as m_exec:
        llm_bindings.upsert(
            use_case,
            provider="gemini_vertex_adc",
            model="google/gemini-3.5-flash",
            updated_by=7,
        )
    args = m_exec.call_args[0][1]
    assert args[1] == "gemini_vertex_adc"
    assert args[2] == "gemini-3.5-flash"


def test_delete_removes_binding():
    with patch("appcore.llm_bindings.execute") as m_exec:
        llm_bindings.delete("video_score.run")
    assert m_exec.called
    assert "DELETE FROM llm_use_case_bindings" in m_exec.call_args[0][0]


def test_list_all_merges_db_overrides_and_defaults():
    db_rows = [
        {"use_case_code": "video_score.run",
         "provider_code": "openrouter",
         "model_id": "openai/gpt-4o-mini",
         "extra_config": None, "enabled": 1, "updated_at": None,
         "updated_by": None},
        {"use_case_code": "copywriting.generate",
         "provider_code": "doubao",
         "model_id": "doubao-seed-2-0-pro",
         "extra_config": None, "enabled": 0,  # disabled → 当成无覆盖
         "updated_at": None, "updated_by": None},
    ]
    with patch("appcore.llm_bindings.query", return_value=db_rows):
        result = llm_bindings.list_all()
    from appcore.llm_use_cases import USE_CASES
    codes = {r["code"] for r in result}
    assert codes == set(USE_CASES.keys())

    by_code = {r["code"]: r for r in result}
    overridden = by_code["video_score.run"]
    assert overridden["provider"] == "openrouter"
    assert overridden["is_custom"] is True

    disabled = by_code["copywriting.generate"]
    # enabled=0 视为无覆盖，应走默认
    assert disabled["provider"] == USE_CASES["copywriting.generate"]["default_provider"]
    assert disabled["model"] == USE_CASES["copywriting.generate"]["default_model"]
    assert disabled["is_custom"] is False

    untouched = by_code["video_translate.localize"]
    assert untouched["provider"] == USE_CASES["video_translate.localize"]["default_provider"]
    assert untouched["is_custom"] is False
