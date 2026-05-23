def test_fine_ai_prompts_require_chinese_human_readable_output():
    from appcore.fine_ai_evaluation_prompts import (
        COUNTRY_EVALUATION_SYSTEM_PROMPT,
        JSON_REPAIR_SYSTEM_PROMPT,
        PRODUCT_FACT_SYSTEM_PROMPT,
        build_country_evaluation_prompt,
        build_json_repair_prompt,
        build_product_fact_prompt,
    )

    product_prompt = build_product_fact_prompt(
        product_snapshot={"product_id": "123", "product_name": "Sample"},
        countries=[{"country_code": "DE", "country_name": "Germany", "country_name_zh": "德国"}],
    )
    country_prompt = build_country_evaluation_prompt(
        product_snapshot={"product_id": "123", "product_name": "Sample"},
        product_facts={"product_id": "123"},
        country={"country_code": "DE", "country_name": "Germany", "country_name_zh": "德国"},
        asset_snapshot={"videos": []},
    )
    repair_prompt = build_json_repair_prompt(
        raw_response='{"decision":{"one_sentence_reason":"Looks promising"}}',
        parse_error="demo",
    )

    common_rule = "所有面向运营阅读的字符串值必须使用简体中文"
    contract_rule = "字段名、国家代码、固定枚举值、货币代码、URL/source_url、时间戳、文件路径、ID 按 schema 和输入原样保留"
    for prompt_text in (
        PRODUCT_FACT_SYSTEM_PROMPT,
        COUNTRY_EVALUATION_SYSTEM_PROMPT,
        JSON_REPAIR_SYSTEM_PROMPT,
        product_prompt,
        country_prompt,
        repair_prompt,
    ):
        assert common_rule in prompt_text
        assert contract_rule in prompt_text

    assert "generated_search_keywords.english_keywords 字段名保持不变，但字段值也输出中文关键词" in product_prompt
    assert "country_name 和 country_name_zh 都输出中文国家名" in country_prompt


def test_fine_ai_gemini_client_invokes_vertex_adc_without_search_and_with_url_context(monkeypatch):
    from appcore import fine_ai_gemini_client as mod

    calls = []

    def fake_invoke(use_case_code, **kwargs):
        calls.append((use_case_code, kwargs))
        return {"json": _country_result(), "usage": {"input_tokens": 1, "output_tokens": 2}}

    monkeypatch.setattr(mod.llm_client, "invoke_generate", fake_invoke)

    client = mod.FineAiGeminiClient()
    result = client.generate_country_evaluation(
        product_snapshot={"product_id": "123", "product_name": "Sample", "product_url": "https://example.test/p"},
        product_facts={
            "product_id": "123",
            "product_name": "Sample",
            "category_detected": None,
            "sku_facts": [],
            "price_facts": [],
            "dimension_facts": [],
            "material_facts": [],
            "feature_facts": [],
            "claim_inventory": [],
            "claim_consistency_risks": [],
            "missing_data": [],
            "assumptions": [],
            "generated_search_keywords": {"english_keywords": [], "country_keyword_hints": {"DE": [], "FR": [], "IT": [], "ES": [], "JP": []}},
        },
        country={"country_code": "DE", "country_name": "Germany", "country_name_zh": "德国", "language": "German", "currency": "EUR"},
        asset_snapshot={"cover_images": [], "product_images": [], "videos": []},
        asset_paths=[],
    )

    assert result["country_code"] == "DE"
    assert calls[0][0] == "fine_ai_evaluation.country"
    kwargs = calls[0][1]
    assert kwargs["provider_override"] == "gemini_vertex_adc"
    assert kwargs["model_override"] == "gemini-3.5-flash"
    assert kwargs["google_search"] is False
    assert kwargs["url_context"] is True
    assert "temperature" not in kwargs


def test_fine_ai_gemini_client_records_full_safe_llm_trace(monkeypatch):
    from appcore import fine_ai_gemini_client as mod

    def fake_invoke(use_case_code, **kwargs):
        return {
            "json": _country_result(),
            "text": '{"country_code":"DE"}',
            "usage": {"input_tokens": 11, "output_tokens": 22},
            "usage_log_id": 99,
            "raw": {"provider_response_id": "raw-123", "nested": {"ok": True}},
        }

    monkeypatch.setattr(mod.llm_client, "invoke_generate", fake_invoke)

    client = mod.FineAiGeminiClient()
    result = client.generate_country_evaluation(
        product_snapshot={"product_id": "123", "product_name": "Sample", "product_url": "https://example.test/p"},
        product_facts={
            "product_id": "123",
            "product_name": "Sample",
            "category_detected": None,
            "sku_facts": [],
            "price_facts": [],
            "dimension_facts": [],
            "material_facts": [],
            "feature_facts": [],
            "claim_inventory": [],
            "claim_consistency_risks": [],
            "missing_data": [],
            "assumptions": [],
            "generated_search_keywords": {"english_keywords": [], "country_keyword_hints": {"DE": [], "FR": [], "IT": [], "ES": [], "JP": []}},
        },
        country={"country_code": "DE", "country_name": "Germany", "country_name_zh": "德国", "language": "German", "currency": "EUR"},
        asset_snapshot={"cover_images": [], "product_images": [], "videos": []},
        asset_paths=["G:/tmp/card_15s_llm.mp4"],
    )

    trace = client.last_call_trace
    assert result["country_code"] == "DE"
    assert trace["provider"] == "gemini_vertex_adc"
    assert trace["model_id"] == "gemini-3.5-flash"
    assert trace["use_case_code"] == "fine_ai_evaluation.country"
    assert trace["request"]["system_prompt"] == mod.COUNTRY_EVALUATION_SYSTEM_PROMPT
    assert "Sample" in trace["request"]["prompt"]
    assert trace["request"]["payload"]["provider_override"] == "gemini_vertex_adc"
    assert trace["request"]["payload"]["media"] == ["G:/tmp/card_15s_llm.mp4"]
    assert trace["response"]["summary"]["input_tokens"] == 11
    assert trace["response"]["parsed_json"]["country_code"] == "DE"
    assert trace["response"]["raw_payload"]["raw"]["provider_response_id"] == "raw-123"
    assert "api_key" not in repr(trace).lower()
    assert "authorization" not in repr(trace).lower()


def test_fine_ai_gemini_client_uses_toolless_json_repair_call_after_parse_failure(monkeypatch):
    from appcore import fine_ai_gemini_client as mod

    calls = []

    def fake_invoke(use_case_code, **kwargs):
        calls.append((use_case_code, kwargs))
        if len(calls) == 1:
            return {
                "text": '{"country_code": "DE", bad',
                "json": None,
                "json_parse_error": "Expecting property name",
                "usage": {"input_tokens": 10, "output_tokens": 20},
            }
        return {
            "json": _country_result(),
            "usage": {"input_tokens": 3, "output_tokens": 4},
        }

    monkeypatch.setattr(mod.llm_client, "invoke_generate", fake_invoke)

    client = mod.FineAiGeminiClient()
    result = client.generate_country_evaluation(
        product_snapshot={"product_id": "123", "product_name": "Sample", "product_url": "https://example.test/p"},
        product_facts={
            "product_id": "123",
            "product_name": "Sample",
            "category_detected": None,
            "sku_facts": [],
            "price_facts": [],
            "dimension_facts": [],
            "material_facts": [],
            "feature_facts": [],
            "claim_inventory": [],
            "claim_consistency_risks": [],
            "missing_data": [],
            "assumptions": [],
            "generated_search_keywords": {"english_keywords": [], "country_keyword_hints": {"DE": [], "FR": [], "IT": [], "ES": [], "JP": []}},
        },
        country={"country_code": "DE", "country_name": "Germany", "country_name_zh": "德国", "language": "German", "currency": "EUR"},
        asset_snapshot={"cover_images": [], "product_images": [], "videos": []},
        asset_paths=["G:/tmp/demo.mp4"],
    )

    assert result["country_code"] == "DE"
    assert [call[0] for call in calls] == ["fine_ai_evaluation.country", "fine_ai_evaluation.country"]
    first_kwargs = calls[0][1]
    repair_kwargs = calls[1][1]
    assert first_kwargs["google_search"] is False
    assert first_kwargs["url_context"] is True
    assert first_kwargs["media"] == ["G:/tmp/demo.mp4"]
    assert repair_kwargs["google_search"] is False
    assert repair_kwargs["url_context"] is False
    assert repair_kwargs["media"] is None
    assert "修复" in repair_kwargs["prompt"]
    assert "Expecting property name" in repair_kwargs["prompt"]
    assert client.last_call_metadata["json_repair_attempted"] is True
    assert client.last_call_metadata["json_repair_succeeded"] is True
    assert client.last_call_metadata["raw_response"]["json_parse_error"] == "Expecting property name"


def test_fine_ai_gemini_client_retries_original_call_when_json_repair_fails(monkeypatch):
    from appcore import fine_ai_gemini_client as mod

    calls = []

    def fake_invoke(use_case_code, **kwargs):
        calls.append((use_case_code, kwargs))
        if len(calls) == 1:
            return {
                "text": '{"country_code": "DE", bad',
                "json": None,
                "json_parse_error": "Expecting property name",
                "usage": {"input_tokens": 10, "output_tokens": 20},
            }
        if len(calls) == 2:
            return {
                "text": "still not json",
                "json": None,
                "json_parse_error": "No JSON object",
                "usage": {"input_tokens": 3, "output_tokens": 4},
            }
        return {"json": _country_result(), "usage": {"input_tokens": 11, "output_tokens": 22}}

    monkeypatch.setattr(mod.llm_client, "invoke_generate", fake_invoke)

    client = mod.FineAiGeminiClient()
    result = client.generate_country_evaluation(
        product_snapshot={"product_id": "123", "product_name": "Sample", "product_url": "https://example.test/p"},
        product_facts={
            "product_id": "123",
            "product_name": "Sample",
            "category_detected": None,
            "sku_facts": [],
            "price_facts": [],
            "dimension_facts": [],
            "material_facts": [],
            "feature_facts": [],
            "claim_inventory": [],
            "claim_consistency_risks": [],
            "missing_data": [],
            "assumptions": [],
            "generated_search_keywords": {"english_keywords": [], "country_keyword_hints": {"DE": [], "FR": [], "IT": [], "ES": [], "JP": []}},
        },
        country={"country_code": "DE", "country_name": "Germany", "country_name_zh": "德国", "language": "German", "currency": "EUR"},
        asset_snapshot={"cover_images": [], "product_images": [], "videos": []},
        asset_paths=[],
    )

    assert result["country_code"] == "DE"
    assert len(calls) == 3
    assert calls[0][1]["url_context"] is True
    assert calls[1][1]["url_context"] is False
    assert calls[2][1]["url_context"] is True
    assert calls[2][1]["project_id"].endswith("-retry-2")
    assert client.last_call_metadata["structured_retry_attempt"] == 2


def test_fine_ai_gemini_client_repairs_markdown_wrapped_json():
    from appcore.fine_ai_gemini_client import _parse_json_with_repair

    assert _parse_json_with_repair("```json\n{\"ok\": true}\n```") == {"ok": True}


def _country_result():
    return {
        "country_code": "DE",
        "country_name": "Germany",
        "country_name_zh": "德国",
        "language": "German",
        "currency": "EUR",
        "status": "completed",
        "scores": {
            "overall_score": 70,
            "product_market_fit_score": 70,
            "demand_score": 70,
            "competition_score": 70,
            "pricing_score": 70,
            "creative_fit_score": 70,
            "landing_page_fit_score": 70,
            "operational_fit_score": 70,
            "compliance_risk_score": 70,
        },
        "decision": {"final_decision": "TEST", "confidence": "medium", "one_sentence_reason": "", "why": [], "blocking_issues": []},
        "market_fit": {"local_positioning": "", "target_segments": [], "use_cases": [], "demand_analysis": {"summary": "", "facts": [], "inferences": [], "evidence_gaps": []}, "seasonality": [], "market_entry_notes": []},
        "competitor_analysis": {"summary": "", "competitors": [], "competitive_advantages": [], "competitive_disadvantages": [], "evidence_gaps": []},
        "pricing_analysis": {"current_price": None, "current_currency": "", "recommended_price_range": {"min": None, "max": None, "currency": "EUR"}, "pricing_commentary": "", "margin_inputs_missing": [], "cannot_calculate_reasons": []},
        "creative_fit": {"creative_missing": True, "assets_reviewed": {"cover_images": [], "product_images": [], "videos": []}, "cover_image_audit": {"score": 0, "issues": [], "localization_needed": [], "claim_risks": [], "recommended_cover_directions": []}, "product_image_audit": {"score": 0, "issues": [], "recommended_image_directions": []}, "video_audit": {"score": 0, "timestamp_findings": [], "hook_analysis": "", "proof_gaps": [], "scenes_to_keep": [], "scenes_to_replace_or_reshoot": []}, "localized_copy_directions": {"cover_text_direction": [], "hook_direction": [], "cta_direction": [], "language_notes": []}, "final_creative_decision": "NO_CREATIVE_PROVIDED"},
        "landing_page_localization": {"localization_difficulty": 50, "hero_section": {"title_direction": "", "subtitle_direction": "", "cta_direction": "", "image_direction": ""}, "sections_needed": [], "trust_elements_needed": [], "claims_to_avoid_or_rewrite": [], "unit_and_currency_notes": [], "faq_directions": []},
        "risks": {"claim_risks": [], "compliance_risks": [], "operational_risks": [], "trust_risks": [], "localization_risks": []},
        "recommendations": {"recommended_positioning": "", "ad_test_angles": [], "audience_suggestions": [], "landing_page_actions": [], "creative_actions": [], "first_30_day_test_plan": {"test_priority": "medium", "creative_variants": [], "landing_page_variants": [], "success_metrics": [], "kill_criteria": [], "scale_criteria": []}},
        "sources": [],
        "missing_data": [],
        "warnings": [],
    }
