import pytest
from appcore.llm_use_cases import USE_CASES, get_use_case
from appcore.image_translate_runtime import _reset_item_processing_state

def test_use_case_registry():
    # 1. Assert the use case "image_translate.eval" is registered correctly
    assert "image_translate.eval" in USE_CASES
    uc = get_use_case("image_translate.eval")
    assert uc["code"] == "image_translate.eval"
    assert uc["default_provider"] == "openrouter"
    assert uc["default_model"] == "google/gemini-1.5-flash-lite"
    assert uc["units_type"] == "tokens"

def test_evaluation_fields_reset():
    # 2. Check that the evaluation state keys are set/reset properly in item structure
    item = {
        "status": "done",
        "attempts": 2,
        "eval_status": "done",
        "eval_result": {"translation_quality_score": 9},
        "eval_error": "none"
    }
    _reset_item_processing_state(item)
    assert item["status"] == "pending"
    assert item["attempts"] == 0
    assert item["eval_status"] == "pending"
    assert item["eval_result"] is None
    assert item["eval_error"] == ""
