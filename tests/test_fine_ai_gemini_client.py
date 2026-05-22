def test_fine_ai_gemini_client_invokes_vertex_adc_with_search_and_url_context(monkeypatch):
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
    assert kwargs["google_search"] is True
    assert kwargs["url_context"] is True
    assert "temperature" not in kwargs


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
