import pytest

from appcore.llm_media_optimizer import OptimizedMedia
from appcore.meta_hot_posts import europe_fit


def test_build_prompt_contains_product_video_and_european_translation_markets():
    prompt = europe_fit.build_prompt(
        {
            "product_url": "https://example.com/products/socket",
            "product_title": "Flexible Socket Extender",
            "price_min": 19.99,
            "currency": "USD",
            "category_l1": "Tools & Hardware",
            "latest_likes": 12000,
            "latest_comments": 330,
            "sync_period_likes": 2400,
            "message_html": "<p>Turn any corner into a power station.</p>",
            "message_zh_html": "把任何角落变成电源站。",
        }
    )

    assert "https://example.com/products/socket" in prompt
    assert "Flexible Socket Extender" in prompt
    assert "Germany" in prompt
    assert "German" in prompt
    assert "France" in prompt
    assert "French" in prompt
    assert "Italy" in prompt
    assert "Italian" in prompt
    assert "Spain" in prompt
    assert "Spanish" in prompt
    assert "translate" in prompt.lower()
    assert "localized" in prompt.lower()
    assert "Post copy" in prompt
    assert "把任何角落变成电源站" in prompt


def test_system_prompt_ignores_video_clarity_after_review_compression():
    prompt = europe_fit.build_system_prompt()

    assert "visual clarity" not in prompt
    assert "clarity" not in prompt.lower()
    assert "visible product demo" in prompt
    assert "translated and localized" in prompt
    assert "voiceover" in prompt


def test_response_schema_includes_translation_localization_fields():
    schema = europe_fit.build_response_schema()
    properties = schema["properties"]

    assert "translation_fit_score" in properties
    assert "best_language_markets" in properties
    assert "source_language_detected" in properties
    assert "speech_dependency" in properties
    assert "on_screen_text_dependency" in properties
    assert "needs_subtitle_translation" in properties
    assert "needs_voiceover_or_dubbing" in properties
    assert "needs_screen_text_replacement" in properties
    assert "localization_difficulty" in properties
    assert "country_localization_notes" in properties
    assert properties["recommendation"]["enum"] == [
        "translate_and_launch",
        "adapt_before_translation",
        "not_recommended",
    ]


def test_europe_fit_prompt_requires_chinese_operator_content():
    system_prompt = europe_fit.build_system_prompt()
    user_prompt = europe_fit.build_prompt(
        {
            "product_url": "https://example.com/products/socket",
            "product_title": "Flexible Socket Extender",
            "category_l1": "Tools & Hardware",
        }
    )
    schema = europe_fit.build_response_schema()

    assert "Simplified Chinese" in system_prompt
    assert "strengths" in user_prompt
    assert "risks" in user_prompt
    assert "required_changes" in user_prompt
    assert "reasoning" in user_prompt
    assert "简体中文" in user_prompt
    assert schema["properties"]["recommendation"]["enum"] == [
        "translate_and_launch",
        "adapt_before_translation",
        "not_recommended",
    ]


def test_normalize_response_clamps_score_and_maps_recommendation():
    result = europe_fit.normalize_assessment_response(
        {
            "json": {
                "suitability_score": 120,
                "recommendation": "direct",
                "direct_reuse": True,
                "best_countries": ["DE", "FR"],
                "country_scores": {"DE": 95, "FR": 88},
                "strengths": ["clear demo"],
                "risks": ["English overlay"],
                "required_changes": ["translate captions"],
                "reasoning": "Strong product-market fit.",
            },
            "provider": "openrouter",
            "model": "google/gemini-3-flash-preview",
        }
    )

    assert result["suitability_score"] == 100
    assert result["recommendation"] == "direct_reuse"
    assert result["direct_reuse"] is True
    assert result["best_countries"] == ["DE", "FR"]
    assert result["provider"] == "openrouter"
    assert result["model"] == "google/gemini-3-flash-preview"


def test_normalize_response_preserves_translation_localization_fields():
    result = europe_fit.normalize_assessment_response(
        {
            "json": {
                "suitability_score": 86,
                "translation_fit_score": 91,
                "recommendation": "translate_and_launch",
                "direct_reuse": False,
                "best_countries": ["Germany", "France"],
                "best_language_markets": [
                    {"country": "Germany", "language": "German", "score": 92},
                    {"country": "France", "language": "French", "score": 88},
                ],
                "country_scores": {"Germany": 86},
                "source_language_detected": "English",
                "speech_dependency": "medium",
                "on_screen_text_dependency": "high",
                "needs_subtitle_translation": True,
                "needs_voiceover_or_dubbing": False,
                "needs_screen_text_replacement": True,
                "localization_difficulty": "medium",
                "country_localization_notes": {
                    "Germany": ["Translate overlay text"],
                    "France": ["Use local CTA"],
                },
                "strengths": ["visual demo"],
                "risks": ["English overlay"],
                "required_changes": ["Replace screen text"],
                "reasoning": "Good once localized.",
            },
            "provider": "openrouter",
            "model": "google/gemini-3-flash-preview",
        }
    )

    assert result["recommendation"] == "translate_and_launch"
    assert result["direct_reuse"] is True
    assert result["translation_fit_score"] == 91
    assert result["best_language_markets"][0]["language"] == "German"
    assert result["source_language_detected"] == "English"
    assert result["speech_dependency"] == "medium"
    assert result["on_screen_text_dependency"] == "high"
    assert result["needs_subtitle_translation"] is True
    assert result["needs_voiceover_or_dubbing"] is False
    assert result["needs_screen_text_replacement"] is True
    assert result["localization_difficulty"] == "medium"
    assert result["country_localization_notes"]["Germany"] == ["Translate overlay text"]


def test_assess_material_uses_optimized_video_and_llm(monkeypatch, tmp_path):
    output_video = tmp_path / "optimized.mp4"
    output_video.write_bytes(b"optimized-video")
    original_video = tmp_path / "output" / "meta_hot_posts" / "videos" / "1.mp4"
    original_video.parent.mkdir(parents=True)
    original_video.write_bytes(b"original-video")
    calls = {}
    cleaned = []

    monkeypatch.setattr(
        europe_fit.video_localization,
        "resolve_local_video_path",
        lambda local_video_path: original_video,
    )

    def fake_prepare(video_path, policy, output_dir=None):
        calls["prepare"] = (video_path, policy.name, output_dir)
        return OptimizedMedia(
            original_path=str(original_video),
            llm_path=str(output_video),
            optimized=True,
            cleanup_path=str(output_video),
            original_bytes=14,
            llm_bytes=15,
            policy_name=policy.name,
        )

    def fake_invoke(use_case_code, **kwargs):
        calls["invoke"] = (use_case_code, kwargs)
        return {
            "json": {
                "suitability_score": 87,
                "recommendation": "adapt_before_use",
                "direct_reuse": False,
                "best_countries": ["DE"],
                "country_scores": {"DE": 87},
                "strengths": ["visual demo"],
                "risks": ["needs translated caption"],
                "required_changes": ["localize overlay text"],
                "reasoning": "Useful, but text needs localization.",
            },
            "provider": "openrouter",
            "model": "google/gemini-3-flash-preview",
        }

    monkeypatch.setattr(europe_fit, "prepare_video_for_llm", fake_prepare)
    monkeypatch.setattr(europe_fit, "cleanup_optimized_media", lambda media: cleaned.append(media.llm_path))
    monkeypatch.setattr(europe_fit.llm_client, "invoke_generate", fake_invoke)

    result = europe_fit.assess_material(
        {
            "id": 1,
            "product_url": "https://example.com/products/a",
            "product_title": "Demo Product",
            "local_video_path": "meta_hot_posts/videos/1.mp4",
        },
        user_id=7,
    )

    assert calls["prepare"][0] == str(original_video)
    assert calls["prepare"][1] == "review_480p_audio"
    assert calls["invoke"][0] == "meta_hot_posts.europe_fit"
    assert calls["invoke"][1]["media"] == [str(output_video)]
    assert calls["invoke"][1]["user_id"] == 7
    assert calls["invoke"][1]["provider_override"] == "openrouter"
    assert calls["invoke"][1]["model_override"] == "google/gemini-3-flash-preview"
    assert result["suitability_score"] == 87
    assert result["video_optimization"]["optimized"] is True
    assert cleaned == [str(output_video)]


def test_assess_material_requires_resolvable_local_video(monkeypatch):
    monkeypatch.setattr(
        europe_fit.video_localization,
        "resolve_local_video_path",
        lambda local_video_path: None,
    )

    with pytest.raises(europe_fit.EuropeFitAssessmentError, match="local video"):
        europe_fit.assess_material(
            {
                "id": 1,
                "product_url": "https://example.com/products/a",
                "local_video_path": "meta_hot_posts/videos/missing.mp4",
            },
            user_id=7,
        )
