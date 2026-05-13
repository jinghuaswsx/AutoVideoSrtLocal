"""appcore.llm_use_cases 中 4 条 asr_normalize.* use_case 守护测试。"""
from appcore.llm_use_cases import USE_CASES, get_use_case


def test_four_asr_normalize_use_cases_registered():
    assert "asr_normalize.detect_language" in USE_CASES
    assert "asr_normalize.translate_zh_to_en" in USE_CASES
    assert "asr_normalize.translate_es_to_en" in USE_CASES
    assert "asr_normalize.translate_generic_to_en" in USE_CASES


def test_detect_use_case_uses_gemini_flash_lite():
    uc = get_use_case("asr_normalize.detect_language")
    assert uc["default_provider"] == "openrouter"
    assert uc["default_model"] == "google/gemini-3.1-flash-lite-preview"
    assert uc["module"] == "video_translate"
    assert uc["units_type"] == "tokens"
    assert uc["usage_log_service"] == "openrouter"


def test_translate_use_cases_use_openrouter_gemini_3_flash():
    for code in (
        "asr_normalize.translate_zh_to_en",
        "asr_normalize.translate_es_to_en",
        "asr_normalize.translate_generic_to_en",
    ):
        uc = get_use_case(code)
        assert uc["default_provider"] == "openrouter"
        assert uc["default_model"] == "google/gemini-3-flash-preview"
        assert uc["module"] == "video_translate"
        assert uc["units_type"] == "tokens"
        assert uc["usage_log_service"] == "openrouter"
