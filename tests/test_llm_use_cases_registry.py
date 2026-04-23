from appcore.llm_use_cases import MODULE_LABELS, USE_CASES, get_use_case, list_by_module


def test_all_use_cases_have_required_fields():
    for code, uc in USE_CASES.items():
        assert uc["code"] == code, f"{code} mismatch self-key"
        assert uc["module"], f"{code} missing module"
        assert uc["label"], f"{code} missing label"
        assert uc["default_provider"] in {
            "openrouter", "doubao", "gemini_aistudio", "gemini_vertex",
            "elevenlabs", "doubao_asr",
        }
        assert uc["default_model"], f"{code} missing default_model"
        assert uc["usage_log_service"], f"{code} missing usage_log_service"
        assert uc["units_type"] in {"tokens", "chars", "seconds", "images"}


def test_video_translate_defaults_align_with_master_vertex_pref():
    for code in ("video_translate.localize",
                 "video_translate.tts_script",
                 "video_translate.rewrite"):
        uc = USE_CASES[code]
        assert uc["default_provider"] == "gemini_vertex"
        assert uc["default_model"] == "gemini-3.1-flash-lite-preview"
        assert uc["units_type"] == "tokens"


def test_video_translate_asr_and_tts_defaults():
    assert USE_CASES["video_translate.tts"]["default_provider"] == "elevenlabs"
    assert USE_CASES["video_translate.tts"]["units_type"] == "chars"
    assert USE_CASES["video_translate.asr"]["default_provider"] == "doubao_asr"
    assert USE_CASES["video_translate.asr"]["units_type"] == "seconds"
    assert USE_CASES["video_translate.tts_language_check"]["default_provider"] == "openrouter"
    assert USE_CASES["video_translate.tts_language_check"]["default_model"] == "google/gemini-3.1-flash-lite-preview"
    assert USE_CASES["video_translate.tts_language_check"]["units_type"] == "tokens"


def test_gemini_video_analysis_family_defaults():
    for code in ("video_score.run", "video_review.analyze", "shot_decompose.run"):
        uc = USE_CASES[code]
        assert uc["default_provider"] == "gemini_aistudio"
        assert uc["usage_log_service"] == "gemini_video_analysis"


def test_image_and_link_check_defaults():
    assert USE_CASES["image_translate.detect"]["default_provider"] == "gemini_vertex"
    assert USE_CASES["image_translate.detect"]["default_model"] == "gemini-3.1-flash-lite-preview"
    assert USE_CASES["image_translate.detect"]["usage_log_service"] == "gemini"
    assert USE_CASES["image_translate.detect"]["units_type"] == "images"
    for code in ("image_translate.generate",
                 "link_check.analyze",
                 "link_check.same_image"):
        uc = USE_CASES[code]
        assert uc["default_provider"] == "gemini_aistudio"
        assert uc["usage_log_service"] == "gemini"


def test_registry_count_and_new_units_types():
    assert len(USE_CASES) >= 23
    assert "ja_translate.localize" in USE_CASES
    assert "ja_translate.rewrite" in USE_CASES
    assert USE_CASES["copywriting_translate.generate"]["units_type"] == "tokens"
    assert USE_CASES["image_translate.generate"]["units_type"] == "images"


def test_material_evaluation_defaults_to_openrouter_gemini_pro():
    uc = USE_CASES["material_evaluation.evaluate"]
    assert uc["module"] == "material"
    assert uc["default_provider"] == "openrouter"
    assert uc["default_model"] == "google/gemini-3.1-pro-preview"
    assert uc["usage_log_service"] == "openrouter"
    assert uc["units_type"] == "tokens"
    assert MODULE_LABELS["material"] == "素材管理"


def test_ja_translate_module_label_exists():
    assert MODULE_LABELS["ja_translate"] == "日语翻译"


def test_same_image_use_case_defaults():
    uc = USE_CASES["link_check.same_image"]
    assert uc["default_provider"] == "gemini_aistudio"
    assert uc["default_model"] == "gemini-3.1-flash-lite-preview"
    assert uc["usage_log_service"] == "gemini"


def test_video_translate_av_sync_defaults():
    expected = {
        "video_translate.shot_notes": (
            "gemini_aistudio",
            "gemini-3.1-pro-preview",
            "gemini_video_analysis",
        ),
        "video_translate.av_localize": (
            "openrouter",
            "anthropic/claude-sonnet-4.6",
            "openrouter",
        ),
        "video_translate.av_rewrite": (
            "openrouter",
            "anthropic/claude-sonnet-4.6",
            "openrouter",
        ),
    }
    for code, (provider, model, service) in expected.items():
        uc = USE_CASES[code]
        assert uc["default_provider"] == provider
        assert uc["default_model"] == model
        assert uc["usage_log_service"] == service


def test_get_use_case_unknown_raises():
    import pytest
    with pytest.raises(KeyError):
        get_use_case("nonexistent.case")


def test_list_by_module_groups_correctly():
    groups = list_by_module()
    assert "video_translate" in groups
    assert "copywriting" in groups
    assert "video_analysis" in groups
    assert "image" in groups
    assert "text_translate" in groups
    for module, items in groups.items():
        assert items, f"module {module} has no use cases"
        assert module in MODULE_LABELS, f"module {module} missing from MODULE_LABELS"
