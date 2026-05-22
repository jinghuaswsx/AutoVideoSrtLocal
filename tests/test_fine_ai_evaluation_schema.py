import pytest


def test_product_facts_schema_accepts_valid_sample():
    from appcore.fine_ai_evaluation_schemas import (
        PRODUCT_FACTS_SCHEMA,
        validate_json_schema,
    )

    sample = {
        "product_id": "123",
        "product_name": "Sample Product",
        "category_detected": "Sample Category",
        "sku_facts": [],
        "price_facts": [],
        "dimension_facts": [],
        "material_facts": [],
        "feature_facts": [],
        "claim_inventory": [],
        "claim_consistency_risks": [],
        "missing_data": [],
        "assumptions": [],
        "generated_search_keywords": {
            "english_keywords": [],
            "country_keyword_hints": {"DE": [], "FR": [], "IT": [], "ES": [], "JP": []},
        },
    }

    validate_json_schema(sample, PRODUCT_FACTS_SCHEMA)


def test_country_evaluation_schema_rejects_score_out_of_range():
    from appcore.fine_ai_evaluation_schemas import (
        COUNTRY_EVALUATION_SCHEMA,
        validate_json_schema,
    )

    sample = _country_sample("DE")
    sample["scores"]["overall_score"] = 101

    with pytest.raises(ValueError, match="overall_score"):
        validate_json_schema(sample, COUNTRY_EVALUATION_SCHEMA)


def test_country_evaluation_schema_rejects_invalid_decision():
    from appcore.fine_ai_evaluation_schemas import (
        COUNTRY_EVALUATION_SCHEMA,
        validate_json_schema,
    )

    sample = _country_sample("DE")
    sample["decision"]["final_decision"] = "MAYBE"

    with pytest.raises(ValueError, match="final_decision"):
        validate_json_schema(sample, COUNTRY_EVALUATION_SCHEMA)


def test_country_evaluation_schema_rejects_invalid_country_code():
    from appcore.fine_ai_evaluation_schemas import (
        COUNTRY_EVALUATION_SCHEMA,
        validate_json_schema,
    )

    sample = _country_sample("US")

    with pytest.raises(ValueError, match="country_code"):
        validate_json_schema(sample, COUNTRY_EVALUATION_SCHEMA)


def test_product_evaluation_result_schema_accepts_valid_sample():
    from appcore.fine_ai_evaluation_schemas import (
        PRODUCT_EVALUATION_RESULT_SCHEMA,
        validate_json_schema,
    )

    result = {
        "schema_version": "1.0",
        "evaluation_run_id": "eval_123",
        "product_id": "123",
        "status": "completed",
        "product_snapshot": {"product_id": "123", "asset_count": {"images": 0, "videos": 0}},
        "product_facts": {
            "product_id": "123",
            "product_name": "Sample Product",
            "category_detected": "Sample Category",
            "sku_facts": [],
            "price_facts": [],
            "dimension_facts": [],
            "material_facts": [],
            "feature_facts": [],
            "claim_inventory": [],
            "claim_consistency_risks": [],
            "missing_data": [],
            "assumptions": [],
            "generated_search_keywords": {
                "english_keywords": [],
                "country_keyword_hints": {"DE": [], "FR": [], "IT": [], "ES": [], "JP": []},
            },
        },
        "summary": {"country_ranking": [], "decision_counts": {"GO": 0, "TEST": 0, "HOLD": 0}},
        "countries": {"DE": _country_sample("DE")},
        "frontend": {"cards": [], "charts": {}, "tables": {}, "badges": [], "action_items": []},
        "metadata": {"countries_requested": ["DE"], "countries_completed": ["DE"], "countries_failed": []},
    }

    validate_json_schema(result, PRODUCT_EVALUATION_RESULT_SCHEMA)


def _country_sample(country_code):
    names = {
        "DE": ("Germany", "德国", "German", "EUR"),
        "US": ("United States", "美国", "English", "USD"),
    }
    country_name, country_name_zh, language, currency = names.get(
        country_code,
        ("Germany", "德国", "German", "EUR"),
    )
    return {
        "country_code": country_code,
        "country_name": country_name,
        "country_name_zh": country_name_zh,
        "language": language,
        "currency": currency,
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
        "decision": {
            "final_decision": "TEST",
            "confidence": "medium",
            "one_sentence_reason": "Data is partial.",
            "why": [],
            "blocking_issues": [],
        },
        "market_fit": {
            "local_positioning": "",
            "target_segments": [],
            "use_cases": [],
            "demand_analysis": {"summary": "", "facts": [], "inferences": [], "evidence_gaps": []},
            "seasonality": [],
            "market_entry_notes": [],
        },
        "competitor_analysis": {
            "summary": "",
            "competitors": [],
            "competitive_advantages": [],
            "competitive_disadvantages": [],
            "evidence_gaps": [],
        },
        "pricing_analysis": {
            "current_price": None,
            "current_currency": "",
            "recommended_price_range": {"min": None, "max": None, "currency": currency},
            "pricing_commentary": "",
            "margin_inputs_missing": [],
            "cannot_calculate_reasons": [],
        },
        "creative_fit": {
            "creative_missing": True,
            "assets_reviewed": {"cover_images": [], "product_images": [], "videos": []},
            "cover_image_audit": {
                "score": 0,
                "issues": [],
                "localization_needed": [],
                "claim_risks": [],
                "recommended_cover_directions": [],
            },
            "product_image_audit": {"score": 0, "issues": [], "recommended_image_directions": []},
            "video_audit": {
                "score": 0,
                "timestamp_findings": [],
                "hook_analysis": "",
                "proof_gaps": [],
                "scenes_to_keep": [],
                "scenes_to_replace_or_reshoot": [],
            },
            "localized_copy_directions": {
                "cover_text_direction": [],
                "hook_direction": [],
                "cta_direction": [],
                "language_notes": [],
            },
            "final_creative_decision": "NO_CREATIVE_PROVIDED",
        },
        "landing_page_localization": {
            "localization_difficulty": 50,
            "hero_section": {
                "title_direction": "",
                "subtitle_direction": "",
                "cta_direction": "",
                "image_direction": "",
            },
            "sections_needed": [],
            "trust_elements_needed": [],
            "claims_to_avoid_or_rewrite": [],
            "unit_and_currency_notes": [],
            "faq_directions": [],
        },
        "risks": {
            "claim_risks": [],
            "compliance_risks": [],
            "operational_risks": [],
            "trust_risks": [],
            "localization_risks": [],
        },
        "recommendations": {
            "recommended_positioning": "",
            "ad_test_angles": [],
            "audience_suggestions": [],
            "landing_page_actions": [],
            "creative_actions": [],
            "first_30_day_test_plan": {
                "test_priority": "medium",
                "creative_variants": [],
                "landing_page_variants": [],
                "success_metrics": [],
                "kill_criteria": [],
                "scale_criteria": [],
            },
        },
        "sources": [],
        "missing_data": [],
        "warnings": [],
    }
