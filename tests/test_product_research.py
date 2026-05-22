"""Tests for single-product AI research feature."""

import json
import pytest


# ── Config Tests ──────────────────────────────────────

def test_country_config_has_eight_countries():
    from appcore.product_research_config import COUNTRIES, DEFAULT_COUNTRY_CODES
    assert len(COUNTRIES) == 8
    assert len(DEFAULT_COUNTRY_CODES) == 8
    assert "DE" in DEFAULT_COUNTRY_CODES
    assert "JP" in DEFAULT_COUNTRY_CODES


def test_get_country_config():
    from appcore.product_research_config import get_country_config
    de = get_country_config("DE")
    assert de["country_name"] == "Germany"
    assert de["currency"] == "EUR"
    se = get_country_config("SE")
    assert se["currency"] == "SEK"
    jp = get_country_config("JP")
    assert jp["currency"] == "JPY"


def test_get_country_config_invalid():
    from appcore.product_research_config import get_country_config
    with pytest.raises(ValueError):
        get_country_config("XX")


def test_normalize_country_codes():
    from appcore.product_research_config import normalize_country_codes
    assert normalize_country_codes(["de", "FR"]) == ["DE", "FR"]


def test_decision_from_score():
    from appcore.product_research_config import decision_from_score
    assert decision_from_score(80) == "GO"
    assert decision_from_score(65) == "TEST"
    assert decision_from_score(50) == "HOLD"
    assert decision_from_score(80, ["serious_risk"]) == "HOLD"


# ── Schema Tests ──────────────────────────────────────

def test_validate_scores_in_range():
    from appcore.product_research_schemas import validate_scores
    assert validate_scores({"overall_score": 50, "demand_score": 80}) == []
    errors = validate_scores({"overall_score": 150})
    assert len(errors) > 0
    errors = validate_scores({"overall_score": -1})
    assert len(errors) > 0


def test_validate_country_code():
    from appcore.product_research_schemas import validate_country_code
    assert validate_country_code("DE") is None
    assert validate_country_code("XX") is not None


def test_validate_decision():
    from appcore.product_research_schemas import validate_decision
    assert validate_decision("GO") is None
    assert validate_decision("TEST") is None
    assert validate_decision("HOLD") is None
    assert validate_decision("INVALID") is not None


def test_validate_confidence():
    from appcore.product_research_schemas import validate_confidence
    assert validate_confidence("high") is None
    assert validate_confidence("medium") is None
    assert validate_confidence("low") is None
    assert validate_confidence("unknown") is not None


def test_product_facts_schema_valid():
    from appcore.product_research_schemas import PRODUCT_FACTS_SCHEMA, validate_json_schema
    sample = {
        "product_name": "Test",
        "category_detected": "Electronics",
        "key_selling_points": ["good"],
        "search_keywords_en": ["test"],
        "missing_data": [],
    }
    validate_json_schema(sample, PRODUCT_FACTS_SCHEMA)


def test_country_evaluation_schema_valid():
    from appcore.product_research_schemas import COUNTRY_EVALUATION_SCHEMA, validate_json_schema
    sample = _valid_country_result()
    validate_json_schema(sample, COUNTRY_EVALUATION_SCHEMA)


# ── Gemini Client Tests ───────────────────────────────

def test_gemini_client_uses_aistudio_provider(monkeypatch):
    from appcore import product_research_gemini_client as mod

    calls = []

    def fake_invoke(use_case_code, **kwargs):
        calls.append((use_case_code, kwargs))
        if use_case_code == "product_research.product_facts":
            return {"json": {
                "product_name": "Test Product",
                "category_detected": "Home & Kitchen",
                "key_selling_points": ["durable"],
                "search_keywords_en": ["test"],
                "search_keywords_by_country": {},
                "missing_data": [],
            }, "usage": {"input_tokens": 1, "output_tokens": 2}}
        if use_case_code == "product_research.media_understanding":
            return {"json": {
                "main_image_analysis": {"product_clarity": "clear", "visual_quality": "high",
                    "text_on_image": [], "claims_on_image": [], "localization_risks": [],
                    "overall_assessment": "good"},
                "video_analysis": {"duration_seconds": 30, "timestamp_findings": [],
                    "hook_analysis": "good", "pain_point_addressed": "convenience",
                    "solution_presentation": "clear", "demo_quality": "high",
                    "before_after_present": False, "cta_analysis": "weak",
                    "subtitles_detected": False, "narration_language": "English",
                    "visual_style": "clean", "claims_in_video": [],
                    "scenes_to_keep": [], "scenes_to_replace_or_reshoot": [],
                    "overall_assessment": "good"},
                "missing_data": [], "warnings": [],
            }, "usage": {"input_tokens": 1, "output_tokens": 2}}
        return {"json": _valid_country_result(), "usage": {"input_tokens": 1, "output_tokens": 2}}

    monkeypatch.setattr(mod.llm_client, "invoke_generate", fake_invoke)

    client = mod.ProductResearchGeminiClient()

    # Test product_facts also has google_search=True
    client.generate_product_facts(
        input_snapshot={"product_url": "https://example.test/p"},
        countries=[{"country_code": "DE", "country_name": "Germany", "country_name_zh": "德国",
                    "language": "German", "currency": "EUR", "marketplaces": ["Amazon.de"]}],
    )
    assert calls[0][0] == "product_research.product_facts"
    assert calls[0][1]["google_search"] is True

    result = client.generate_country_evaluation(
        country={"country_code": "DE", "country_name": "Germany", "country_name_zh": "德国",
                 "language": "German", "currency": "EUR", "marketplaces": ["Amazon.de"]},
        input_snapshot={"product_url": "https://example.test/p"},
        product_facts={"product_name": "Test", "category_detected": "X", "key_selling_points": [],
                       "search_keywords_en": [], "missing_data": []},
        media_understanding={},
    )

    assert result["country_code"] == "DE"
    assert calls[1][0] == "product_research.country"
    kwargs = calls[1][1]
    assert kwargs["provider_override"] == "gemini_aistudio"
    assert kwargs["model_override"] == "gemini-3.5-flash"
    assert kwargs["google_search"] is True
    assert kwargs["url_context"] is True


def test_json_repair_markdown_wrapped():
    from appcore.product_research_gemini_client import _parse_json_with_repair
    assert _parse_json_with_repair('```json\n{"ok": true}\n```') == {"ok": True}
    assert _parse_json_with_repair('some text {"key": "val"} more text') == {"key": "val"}


def test_json_repair_fails_gracefully():
    from appcore.product_research_gemini_client import _parse_json_with_repair
    with pytest.raises(ValueError):
        _parse_json_with_repair("not json at all {{{")


# ── Exchange Rate Tests ───────────────────────────────

def test_exchange_rate_provider_usd_to_eur():
    from appcore.product_research_exchange_rate import ExchangeRateProvider
    fx = ExchangeRateProvider()
    rate = fx.get_rate("USD", "EUR")
    assert rate is not None
    converted = fx.convert(100, "USD", "EUR")
    assert converted is not None
    assert converted > 0


def test_exchange_rate_same_currency():
    from appcore.product_research_exchange_rate import ExchangeRateProvider
    fx = ExchangeRateProvider()
    assert fx.get_rate("EUR", "EUR") == 1


def test_exchange_rate_unknown_currency():
    from appcore.product_research_exchange_rate import ExchangeRateProvider
    fx = ExchangeRateProvider()
    assert fx.get_rate("USD", "ZZZ") is None
    assert fx.convert(100, "USD", "ZZZ") is None


# ── Pipeline Tests ────────────────────────────────────

class InMemoryResearchRepository:
    def __init__(self):
        self.runs = {}
        self.countries = {}

    def create_run(self, run):
        self.runs[run["research_run_id"]] = dict(run)

    def get_run(self, run_id):
        return self.runs.get(run_id)

    def update_run(self, run_id, **kwargs):
        if run_id in self.runs:
            self.runs[run_id].update(kwargs)

    def upsert_country(self, run_id, code, data):
        key = f"{run_id}:{code}"
        self.countries[key] = data

    def list_countries(self, run_id):
        return {k.split(":")[1]: v for k, v in self.countries.items() if k.startswith(run_id)}


class FakeResearchGeminiClient:
    def __init__(self, calls_list=None):
        self.calls = calls_list if calls_list is not None else []
        self.last_call_metadata = {}

    def generate_product_facts(self, *, input_snapshot, countries):
        self.calls.append("product_facts")
        return {
            "product_name": "Test Product",
            "category_detected": "Home & Kitchen",
            "key_selling_points": ["durable", "affordable"],
            "search_keywords_en": ["test product", "home gadget"],
            "search_keywords_by_country": {"DE": ["Testprodukt"], "FR": ["produit test"]},
            "missing_data": [],
        }

    def generate_media_understanding(self, *, input_snapshot, product_facts, media_paths=None):
        self.calls.append("media_understanding")
        return {
            "main_image_analysis": {
                "product_clarity": "clear",
                "visual_quality": "high",
                "text_on_image": [],
                "claims_on_image": [],
                "localization_risks": [],
                "overall_assessment": "good",
            },
            "video_analysis": {
                "duration_seconds": 30,
                "timestamp_findings": [],
                "hook_analysis": "good hook",
                "pain_point_addressed": "convenience",
                "solution_presentation": "clear",
                "demo_quality": "high",
                "before_after_present": False,
                "cta_analysis": "weak",
                "subtitles_detected": False,
                "narration_language": "English",
                "visual_style": "clean",
                "claims_in_video": [],
                "scenes_to_keep": ["opening", "demo"],
                "scenes_to_replace_or_reshoot": ["cta"],
                "overall_assessment": "good with room for improvement",
            },
            "missing_data": [],
            "warnings": [],
        }

    def generate_country_evaluation(self, *, country, input_snapshot, product_facts, media_understanding):
        code = country["country_code"]
        self.calls.append(f"country:{code}")
        return _valid_country_result(code)


def _valid_country_result(code="DE"):
    return {
        "country_code": code,
        "country_name": "Germany",
        "country_name_zh": "德国",
        "language": "German",
        "currency": "EUR",
        "status": "completed",
        "scores": {
            "overall_score": 72,
            "product_market_fit_score": 70,
            "demand_score": 65,
            "competition_score": 60,
            "video_selling_fit_score": 50,
            "main_image_fit_score": 65,
            "landing_page_localization_score": 55,
            "operational_fit_score": 60,
            "risk_score": 50,
        },
        "decision": {
            "final_decision": "TEST",
            "confidence": "medium",
            "one_sentence_reason": "Has potential but needs localization",
            "why": ["market demand exists"],
            "blocking_issues": [],
        },
        "market_fit": {"local_positioning": "", "target_segments": [], "use_cases": [], "demand_summary": "", "seasonality": [], "market_entry_notes": []},
        "competitor_pricing": {"summary": "", "competitors": [], "price_band": {"min": None, "max": None, "median": None, "currency": "EUR"}, "evidence_gaps": []},
        "pricing_strategy": {
            "current_price_local": {"amount": None, "currency": "EUR"},
            "recommended_price": {"amount": 29.99, "currency": "EUR"},
            "recommended_price_range": {"min": 24.99, "max": 34.99, "currency": "EUR"},
            "recommended_price_ending": ".99",
            "margin_warnings": [],
            "pricing_confidence": "low",
        },
        "shipping_strategy": {"recommended_model": "free_shipping", "customer_shipping_fee": None, "free_shipping_threshold": None, "currency": "EUR", "reason": "", "missing_inputs": ["weight"]},
        "short_video_fit": {
            "final_video_decision": "LOCALIZE_BEFORE_TEST",
            "hook_fit": "", "local_language_fit": "", "cultural_fit": "",
            "claim_risks": [], "scenes_to_keep": [], "scenes_to_replace_or_reshoot": [],
            "localized_hook_directions": [], "localized_cta_directions": [],
        },
        "main_image_fit": {"decision": "USE_AS_IS", "issues": [], "localization_directions": []},
        "landing_page_localization": {"localization_difficulty": 40, "hero_direction": "", "sections_needed": [], "trust_elements_needed": [], "claims_to_avoid_or_rewrite": [], "unit_and_currency_notes": "", "faq_directions": ""},
        "risks": {"claim_risks": [], "compliance_risks": [], "operational_risks": [], "trust_risks": [], "localization_risks": []},
        "recommendations": {
            "recommended_positioning": "",
            "ad_test_angles": [],
            "creative_actions": [],
            "pricing_actions": [],
            "shipping_actions": [],
            "landing_page_actions": [],
            "first_30_day_test_plan": {"test_priority": "medium", "creative_variants": [], "pricing_variants": [], "success_metrics": [], "kill_criteria": [], "scale_criteria": []},
        },
        "sources": [],
        "missing_data": [],
        "warnings": [],
    }


class _FakeFxProvider:
    def get_rate(self, from_cur, to_cur):
        return 0.92 if to_cur == "EUR" else (10.3 if to_cur == "SEK" else (150 if to_cur == "JPY" else 1.0))

    def convert(self, amount, from_cur, to_cur):
        if amount is None:
            return None
        rate = self.get_rate(from_cur, to_cur)
        if rate is None:
            return None
        from decimal import Decimal
        return Decimal(str(amount)) * rate


def test_pipeline_creates_12_step_cards():
    from appcore.product_research_config import PIPELINE_STEPS
    assert len(PIPELINE_STEPS) == 12
    card_ids = [s["card_id"] for s in PIPELINE_STEPS]
    assert "input_validation" in card_ids
    assert "product_facts" in card_ids
    assert "media_understanding" in card_ids
    for code in ["DE", "FR", "IT", "ES", "NL", "PT", "SE", "JP"]:
        assert f"country_{code}" in card_ids
    assert "final_conclusion" in card_ids


def test_pipeline_no_pricing_strategy_step():
    from appcore.product_research_config import PIPELINE_STEPS
    card_ids = [s["card_id"] for s in PIPELINE_STEPS]
    assert "pricing_strategy" not in card_ids


@pytest.mark.skip(reason="Requires DB connection; run manually with real DB")
def test_service_create_run():
    from appcore.product_research_service import ProductResearchService
    service = ProductResearchService()
    run = service.create_run({
        "product_url": "https://example.com/p/1",
        "main_image": {"url": "https://cdn.example.com/img.jpg"},
        "short_video": {"url": "https://cdn.example.com/vid.mp4"},
    })
    assert run["status"] == "queued"
    assert len(run["countries"]) == 8
    assert run["research_run_id"].startswith("research_")


# ── Frontend Tests ────────────────────────────────────

def test_frontend_payload_has_expected_structure():
    countries = {
        "DE": _valid_country_result("DE"),
        "FR": _valid_country_result("FR"),
    }
    summary = {
        "ranking": [
            {"country_code": "DE", "country_name_zh": "德国", "overall_score": 72, "decision": "TEST", "confidence": "medium", "one_sentence_reason": "", "status": "completed"},
            {"country_code": "FR", "country_name_zh": "法国", "overall_score": 68, "decision": "TEST", "confidence": "medium", "one_sentence_reason": "", "status": "completed"},
        ],
        "average_score": 70,
        "best_country": "DE", "best_country_zh": "德国",
        "worst_country": "FR", "worst_country_zh": "法国",
        "go_count": 0, "test_count": 2, "hold_count": 0,
    }
    from appcore.product_research_service import _build_frontend
    frontend = _build_frontend(summary, countries)

    assert len(frontend["cards"]) == 4
    assert frontend["cards"][0]["card_type"] == "summary_metric"
    assert frontend["cards"][0]["value"] == 70

    assert len(frontend["charts"]["country_score_bar"]) == 2
    assert len(frontend["charts"]["score_radar"]) == 2
    assert "pricing_comparison" not in frontend["charts"]

    assert len(frontend["tables"]["country_overview"]) == 2
    assert len(frontend["badges"]) == 2

    overview = frontend["tables"]["country_overview"][0]
    assert overview["country_code"] == "DE"
    assert overview["overall_score"] == 72
    assert "video_decision" in overview


# ── Flaky Country Tests ───────────────────────────────

class FailingCountryGeminiClient(FakeResearchGeminiClient):
    def __init__(self):
        super().__init__([])
        self.last_call_metadata = {}
        self._fail_country = None

    def generate_country_evaluation(self, *, country, input_snapshot, product_facts, media_understanding):
        code = country["country_code"]
        if code == self._fail_country:
            raise RuntimeError(f"Simulated failure for {code}")
        return super().generate_country_evaluation(
            country=country, input_snapshot=input_snapshot,
            product_facts=product_facts, media_understanding=media_understanding,
        )


@pytest.mark.skip(reason="Requires DB connection")
def test_country_failure_does_not_block_other_countries():
    pass  # Requires real DB to run; covered by manual testing


# ── Validate Input ────────────────────────────────────

def test_validate_input_requires_product_url():
    from appcore.product_research_service import _validate_input
    valid, errors = _validate_input({})
    assert not valid
    assert any("PRODUCT_URL" in e for e in errors)


def test_validate_input_requires_main_image():
    from appcore.product_research_service import _validate_input
    valid, errors = _validate_input({"product_url": "https://example.com/p"})
    assert not valid
    assert any("MAIN_IMAGE" in e for e in errors)


def test_validate_input_requires_short_video():
    from appcore.product_research_service import _validate_input
    valid, errors = _validate_input({
        "product_url": "https://example.com/p",
        "main_image": {"url": "https://cdn.example.com/img.jpg"},
    })
    assert not valid
    assert any("SHORT_VIDEO" in e for e in errors)


def test_validate_input_passes_with_all_required():
    from appcore.product_research_service import _validate_input
    valid, errors = _validate_input({
        "product_url": "https://example.com/p",
        "main_image": {"url": "https://cdn.example.com/img.jpg"},
        "short_video": {"url": "https://cdn.example.com/vid.mp4"},
    })
    assert valid
    assert len(errors) == 0


# ── Summary Aggregation ───────────────────────────────

def test_build_summary_handles_empty_countries():
    from appcore.product_research_service import _build_summary
    result = _build_summary({})
    assert result["ranking"] == []
    assert result["average_score"] == 0
    assert result["go_count"] == 0
    assert result["test_count"] == 0
    assert result["hold_count"] == 0


def test_build_summary_counts_go_test_hold():
    from appcore.product_research_service import _build_summary
    countries = {
        "DE": _valid_country_result("DE"),
        "FR": _valid_country_result("FR"),
    }
    result = _build_summary(countries)
    assert result["test_count"] == 2
    assert result["go_count"] == 0


def test_build_summary_ranking_sorted_by_score():
    from appcore.product_research_service import _build_summary
    de = _valid_country_result("DE")
    de["scores"]["overall_score"] = 72
    fr = _valid_country_result("FR")
    fr["scores"]["overall_score"] = 68
    result = _build_summary({"DE": de, "FR": fr})
    assert result["ranking"][0]["country_code"] == "DE"
    assert result["best_country"] == "DE"