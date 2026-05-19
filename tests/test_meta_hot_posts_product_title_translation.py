from appcore.meta_hot_posts import product_title_translation


def test_translate_product_title_uses_openrouter_gemini31_flash_lite():
    calls = {}

    def fake_invoke(use_case_code, **kwargs):
        calls["use_case_code"] = use_case_code
        calls["kwargs"] = kwargs
        return {
            "text": "便携式露营灯",
            "provider": "openrouter",
            "model": "google/gemini-3.1-flash-lite",
        }

    result = product_title_translation.translate_product_title(
        "Portable Camping Lantern",
        user_id=7,
        invoke_fn=fake_invoke,
    )

    assert result == "便携式露营灯"
    assert calls["use_case_code"] == "meta_hot_posts.translate_product_title"
    assert calls["kwargs"]["user_id"] == 7
    assert calls["kwargs"]["provider_override"] == "openrouter"
    assert calls["kwargs"]["model_override"] == "google/gemini-3.1-flash-lite"
    assert calls["kwargs"]["billing_extra"]["source"] == "meta_hot_posts_product_title"
    assert "Portable Camping Lantern" in calls["kwargs"]["prompt"]


def test_translate_product_title_reuses_existing_chinese_without_llm():
    def fail_invoke(*args, **kwargs):
        raise AssertionError("LLM should not be called for Chinese titles")

    assert (
        product_title_translation.translate_product_title(
            "户外感应灯",
            invoke_fn=fail_invoke,
        )
        == "户外感应灯"
    )


def test_translate_product_title_strips_quotes_and_code_fences():
    def fake_invoke(use_case_code, **kwargs):
        return {"text": "```text\n\"可调节硅胶戒指\"\n```"}

    assert (
        product_title_translation.translate_product_title(
            "Silicone Ring Size Adjuster",
            invoke_fn=fake_invoke,
        )
        == "可调节硅胶戒指"
    )
