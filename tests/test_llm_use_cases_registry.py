from appcore.llm_use_cases import MODULE_LABELS, USE_CASES, get_use_case, list_by_module


def test_all_use_cases_have_required_fields():
    for code, uc in USE_CASES.items():
        assert uc["code"] == code, f"{code} mismatch self-key"
        assert uc["module"], f"{code} missing module"
        assert uc["label"], f"{code} missing label"
        assert uc["default_provider"] in {"openrouter", "doubao", "gemini_aistudio", "gemini_vertex"}
        assert uc["default_model"], f"{code} missing default_model"
        assert uc["usage_log_service"], f"{code} missing usage_log_service"


def test_video_translate_defaults_align_with_master_vertex_pref():
    for code in ("video_translate.localize",
                 "video_translate.tts_script",
                 "video_translate.rewrite"):
        uc = USE_CASES[code]
        assert uc["default_provider"] == "gemini_vertex"
        assert uc["default_model"] == "gemini-3.1-flash-lite-preview"


def test_gemini_video_analysis_family_defaults():
    for code in ("video_score.run", "video_review.analyze", "shot_decompose.run"):
        uc = USE_CASES[code]
        assert uc["default_provider"] == "gemini_aistudio"
        assert uc["usage_log_service"] == "gemini_video_analysis"


def test_image_and_link_check_defaults():
    for code in ("image_translate.detect",
                 "image_translate.generate",
                 "link_check.analyze"):
        uc = USE_CASES[code]
        assert uc["default_provider"] == "gemini_aistudio"
        assert uc["usage_log_service"] == "gemini"


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
