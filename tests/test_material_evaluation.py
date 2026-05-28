import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def _default_product_link_probe_ok(monkeypatch):
    from appcore import material_evaluation

    monkeypatch.setattr(material_evaluation.pushes, "probe_ad_url", lambda url: (True, None))


def _fixed_target_country_rows(*, score: int = 88, suitable: bool = True) -> list[dict]:
    return [
        {
            "lang": code,
            "country": country,
            "is_suitable": suitable,
            "score": score,
            "risk_level": "low" if suitable else "high",
            "decision": "suitable" if suitable else "not_suitable",
            "summary": "market fit summary",
            "reason": "market fit reason",
            "suggestions": [],
        }
        for code, country in [
            ("de", "Germany"),
            ("fr", "France"),
            ("it", "Italy"),
            ("es", "Spain"),
            ("ja", "Japan"),
            ("en", "United States"),
        ]
    ]


def test_response_schema_requires_fixed_xuanpin_target_countries():
    from appcore import material_evaluation

    languages = material_evaluation.evaluation_target_languages()

    schema = material_evaluation.build_response_schema(languages)

    countries = schema["properties"]["countries"]
    item_props = countries["items"]["properties"]
    assert countries["minItems"] == 6
    assert countries["maxItems"] == 6
    assert item_props["lang"]["enum"] == ["de", "fr", "it", "es", "ja", "en"]
    assert item_props["recommendation"]["enum"] == ["做", "不做"]
    assert "summary" in item_props
    assert item_props["reason"]["maxLength"] == 100


def test_material_evaluation_defaults_to_openrouter_gemini3_flash(monkeypatch):
    from appcore import material_evaluation

    monkeypatch.setattr(
        material_evaluation.llm_bindings,
        "resolve",
        lambda use_case: (_ for _ in ()).throw(RuntimeError("no db binding")),
    )

    config = material_evaluation.resolve_evaluation_llm_config()

    assert config["provider"] == "openrouter"
    assert config["model"] == "google/gemini-3-flash-preview"
    assert config["search_enabled"] is False
    assert config["search_tools"] == []


def test_prompt_mentions_europe_small_languages_and_input_assets():
    from appcore import material_evaluation

    prompt = material_evaluation.build_prompt(
        product={"id": 7, "name": "Portable Neck Fan", "product_code": "neck-fan"},
        product_url="https://newjoyloo.com/products/neck-fan",
        languages=[{"code": "de", "name": "德语"}, {"code": "fr", "name": "法语"}],
    )

    assert "全球主要市场" in prompt
    assert "商品主图" in prompt
    assert "商品链接" in prompt
    assert "推广视频" in prompt
    assert "https://newjoyloo.com/products/neck-fan" in prompt
    assert "德语(de)" in prompt
    assert "法语(fr)" in prompt


def test_prompt_includes_current_date_and_local_season_rules():
    from datetime import date

    from appcore import material_evaluation

    prompt = material_evaluation.build_prompt(
        product={"id": 7, "name": "毛球修剪器", "product_code": "digital-lint-shaver"},
        product_url="https://newjoyloo.com/products/digital-lint-shaver",
        languages=[
            {"code": "de", "name": "德语"},
            {"code": "en-au", "name": "澳大利亚英语"},
        ],
        as_of_date=date(2026, 4, 29),
    )

    assert "当前评估日期：2026-04-29" in prompt
    assert "北半球季节参考" in prompt
    assert "南半球季节相反" in prompt
    assert "澳大利亚" in prompt
    assert "当前季节明显错配" in prompt
    assert "score 通常不应高于 55" in prompt
    assert "毛球修剪器主要用于大衣、毛衣、羽绒服" in prompt
    assert "接近夏季" in prompt
    assert "不应仅因素材清晰就给出“适合推广”" in prompt


def test_prompt_includes_balanced_market_timing_rules():
    from datetime import date

    from appcore import material_evaluation

    prompt = material_evaluation.build_prompt(
        product={"id": 8, "name": "Portable Neck Fan", "product_code": "neck-fan"},
        product_url="https://newjoyloo.com/products/neck-fan",
        languages=[{"code": "de", "name": "德语"}, {"code": "fr", "name": "法语"}],
        as_of_date=date(2026, 4, 29),
    )

    assert "市场时点 Gate" in prompt
    assert "投放准备提前量" in prompt
    assert "节日/礼品节点" in prompt
    assert "气候触发因素" in prompt
    assert "品类生命周期" in prompt
    assert "竞争和价格敏感度" in prompt
    assert "物流履约限制" in prompt
    assert "不是一票否决" in prompt
    assert "不要因为产品存在季节性就自动判为不适合" in prompt
    assert "全年刚需、礼品属性、提前预热、反季市场" in prompt


def test_normalize_result_covers_all_languages_and_truncates_reason():
    from appcore import material_evaluation

    languages = [
        {"code": "de", "name": "德语"},
        {"code": "fr", "name": "法语"},
    ]
    long_reason = "适合夏季出行、办公室和户外场景，但需要避免医疗功效暗示。" * 5
    raw = {
        "countries": [
            {
                "lang": "de",
                "country": "德国",
                "is_suitable": True,
                "score": 82,
                "risk_level": "low",
                "decision": "适合推广",
                "reason": long_reason,
                "suggestions": ["突出便携", "强调静音"],
            },
            {
                "lang": "fr",
                "country": "法国",
                "is_suitable": False,
                "score": 45,
                "risk_level": "high",
                "decision": "不适合推广",
                "reason": "视频场景与法国消费者习惯不匹配。",
                "suggestions": [],
            },
        ]
    }

    normalized = material_evaluation.normalize_result(raw, languages)

    assert [row["lang"] for row in normalized["countries"]] == ["de", "fr"]
    assert len(normalized["countries"][0]["reason"]) <= 100
    assert normalized["ai_score"] == 63.5
    assert normalized["ai_evaluation_result"] == "部分适合推广"
    assert "listing_status" not in normalized


def test_normalize_result_replaces_non_chinese_reason_with_chinese_fallback():
    from appcore import material_evaluation

    normalized = material_evaluation.normalize_result(
        {
            "countries": [
                {
                    "lang": "de",
                    "country": "德国",
                    "is_suitable": True,
                    "score": 82,
                    "risk_level": "low",
                    "decision": "适合推广",
                    "summary": "Geeignet fuer Pendler und Outdoor-Szenen.",
                    "reason": "Geeignet fuer deutsche Pendler und Outdoor-Szenen.",
                    "suggestions": ["Heben Sie die Akkulaufzeit hervor"],
                },
                {
                    "lang": "ja",
                    "country": "日本",
                    "is_suitable": False,
                    "score": 42,
                    "risk_level": "high",
                    "decision": "不适合推广",
                    "summary": "日本では季節需要が弱い。",
                    "reason": "日本では現在の季節需要が弱い。",
                    "suggestions": ["季節を再確認してください"],
                },
            ]
        },
        [{"code": "de", "name": "德语"}, {"code": "ja", "name": "日语"}],
    )

    for row in normalized["countries"]:
        assert row["reason"]
        assert "模型返回的原因不是中文" in row["reason"]
        assert any("\u4e00" <= ch <= "\u9fff" for ch in row["reason"])
        assert not any("\u3040" <= ch <= "\u30ff" for ch in row["reason"])
        assert row["summary"]
        assert any("\u4e00" <= ch <= "\u9fff" for ch in row["summary"])
        assert all(any("\u4e00" <= ch <= "\u9fff" for ch in item) for item in row["suggestions"])


def test_normalize_result_accepts_top_level_country_array():
    from appcore import material_evaluation

    normalized = material_evaluation.normalize_result(
        [
            {
                "lang": "de",
                "country": "德国",
                "is_suitable": True,
                "score": 90,
                "risk_level": "low",
                "decision": "适合推广",
                "reason": "春季园艺需求明确。",
                "suggestions": ["突出园艺场景"],
            }
        ],
        [{"code": "de", "name": "德语"}],
    )

    assert normalized["countries"][0]["lang"] == "de"
    assert normalized["ai_score"] == 90.0


def test_evaluate_ready_product_invokes_llm_and_updates_product(monkeypatch, tmp_path):
    from appcore import material_evaluation

    cover = tmp_path / "cover.jpg"
    video = tmp_path / "promo.mp4"
    cover.write_bytes(b"cover")
    video.write_bytes(b"video")
    updates = {}
    llm_calls = []

    monkeypatch.setattr(
        material_evaluation.medias,
        "get_product",
        lambda product_id: {
            "id": product_id,
            "name": "Portable Neck Fan",
            "product_code": "neck-fan",
            "user_id": 9,
            "ai_evaluation_result": None,
        },
    )
    monkeypatch.setattr(
        material_evaluation.medias,
        "list_enabled_languages_kv",
        lambda: [{"code": "en", "name": "英语"}, {"code": "de", "name": "德语"}],
    )
    monkeypatch.setattr(
        material_evaluation.medias,
        "resolve_cover",
        lambda product_id, lang="en": "media/cover.jpg",
    )
    monkeypatch.setattr(
        material_evaluation.medias,
        "list_items",
        lambda product_id, lang="en": [
            {"id": 11, "lang": "en", "object_key": "media/promo.mp4"}
        ],
    )
    monkeypatch.setattr(
        material_evaluation.pushes,
        "resolve_product_page_url",
        lambda lang, product: "https://newjoyloo.com/products/neck-fan",
    )
    monkeypatch.setattr(
        material_evaluation,
        "_materialize_media",
        lambda object_key: cover if object_key.endswith(".jpg") else video,
    )
    monkeypatch.setattr(
        material_evaluation.llm_client,
        "invoke_generate",
        lambda *args, **kwargs: llm_calls.append((args, kwargs)) or {
            "json": {
                "countries": [
                    *_fixed_target_country_rows(score=88),
                    {
                        "lang": "de",
                        "country": "德国",
                        "is_suitable": True,
                        "score": 88,
                        "risk_level": "low",
                        "decision": "适合推广",
                        "reason": "便携降温需求明确，视频场景适合德国夏季通勤和户外。",
                        "suggestions": ["强调静音和续航"],
                    }
                ]
            }
        },
    )
    monkeypatch.setattr(
        material_evaluation.medias,
        "update_product",
        lambda product_id, **kwargs: updates.update(kwargs) or 1,
    )

    result = material_evaluation.evaluate_product_if_ready(7)

    assert result["status"] == "evaluated"
    assert result["ai_evaluation_detail"]["countries"]
    assert updates["ai_score"] == 88.0
    assert updates["ai_evaluation_result"] == "适合推广"
    assert "listing_status" not in updates
    assert "listing_status" not in result
    assert len(llm_calls) == 6
    assert all(call[1]["provider_override"] == "openrouter" for call in llm_calls)
    assert all(call[1]["model_override"] == "google/gemini-3-flash-preview" for call in llm_calls)
    assert all(call[1]["google_search"] is False for call in llm_calls)
    assert all(call[1]["billing_extra"]["tools"] == [] for call in llm_calls)
    detail = json.loads(updates["ai_evaluation_detail"])
    assert detail["product_url"] == "https://newjoyloo.com/products/neck-fan"
    assert detail["provider"] == "openrouter"
    assert detail["model"] == "google/gemini-3-flash-preview"
    assert detail["search_enabled"] is False
    assert detail["search_tools"] == []
    assert detail["evaluation_mode"] == "per_country"
    assert detail["country_call_count"] == 6
    assert detail["countries"][0]["lang"] == "de"
    assert [row["lang"] for row in detail["countries"]] == ["de", "fr", "it", "es", "ja", "en"]


def test_evaluate_ready_product_uses_configured_gemini_aistudio_binding(monkeypatch, tmp_path):
    from appcore import material_evaluation

    cover = tmp_path / "cover.jpg"
    video = tmp_path / "promo.mp4"
    cover.write_bytes(b"cover")
    video.write_bytes(b"video")
    updates = {}
    llm_calls = []

    monkeypatch.setattr(
        material_evaluation,
        "llm_bindings",
        SimpleNamespace(resolve=lambda code: {
            "provider": "gemini_aistudio",
            "model": "gemini-3.5-flash",
        }),
        raising=False,
    )
    monkeypatch.setattr(
        material_evaluation.medias,
        "get_product",
        lambda product_id: {
            "id": product_id,
            "name": "Tomato Clip",
            "product_code": "tomato-clip",
            "user_id": 9,
            "ai_evaluation_result": None,
        },
    )
    monkeypatch.setattr(
        material_evaluation.medias,
        "list_enabled_languages_kv",
        lambda: [{"code": "de", "name": "德语"}],
    )
    monkeypatch.setattr(
        material_evaluation.medias,
        "resolve_cover",
        lambda product_id, lang="en": "media/cover.jpg",
    )
    monkeypatch.setattr(
        material_evaluation.medias,
        "list_items",
        lambda product_id, lang="en": [
            {"id": 11, "lang": "en", "object_key": "media/promo.mp4"}
        ],
    )
    monkeypatch.setattr(
        material_evaluation.pushes,
        "resolve_product_page_url",
        lambda lang, product: "https://newjoyloo.com/products/tomato-clip",
    )
    monkeypatch.setattr(
        material_evaluation,
        "_materialize_media",
        lambda object_key: cover if object_key.endswith(".jpg") else video,
    )
    monkeypatch.setattr(material_evaluation, "_automatic_attempt_count", lambda *args: 0)
    monkeypatch.setattr(material_evaluation, "_record_attempt_start", lambda *args, **kwargs: 123)
    monkeypatch.setattr(material_evaluation, "_record_attempt_finish", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        material_evaluation.llm_client,
        "invoke_generate",
        lambda *args, **kwargs: llm_calls.append((args, kwargs)) or {
            "json": {
                "countries": [
                    {
                        "lang": "de",
                        "country": "德国",
                        "is_suitable": True,
                        "score": 90,
                        "risk_level": "low",
                        "decision": "适合推广",
                        "reason": "春季园艺需求明确。",
                        "suggestions": [],
                    }
                ]
            }
        },
    )
    monkeypatch.setattr(
        material_evaluation.medias,
        "update_product",
        lambda product_id, **kwargs: updates.update(kwargs) or 1,
    )

    result = material_evaluation.evaluate_product_if_ready(7, force=True, manual=True)

    assert result["status"] == "evaluated"
    assert llm_calls[0][1]["provider_override"] == "gemini_aistudio"
    assert llm_calls[0][1]["model_override"] == "gemini-3.5-flash"
    assert llm_calls[0][1]["google_search"] is False
    assert llm_calls[0][1]["billing_extra"]["tools"] == []
    detail = json.loads(updates["ai_evaluation_detail"])
    assert detail["provider"] == "gemini_aistudio"
    assert detail["model"] == "gemini-3.5-flash"
    assert detail["search_enabled"] is False
    assert detail["search_tools"] == []


def test_evaluate_ready_product_invokes_llm_once_per_target_country(monkeypatch, tmp_path):
    from appcore import material_evaluation

    cover = tmp_path / "cover.jpg"
    video = tmp_path / "promo.mp4"
    cover.write_bytes(b"cover")
    video.write_bytes(b"video")
    updates = {}
    llm_calls = []
    languages = [
        {"code": "de", "name": "德语", "country": "德国"},
        {"code": "fr", "name": "法语", "country": "法国"},
        {"code": "en", "name": "英语", "country": "美国"},
    ]
    scores = {"de": 76, "fr": 61, "en": 84}

    monkeypatch.setattr(
        material_evaluation,
        "evaluation_target_languages",
        lambda: [dict(item) for item in languages],
    )
    monkeypatch.setattr(
        material_evaluation.medias,
        "get_product",
        lambda product_id: {
            "id": product_id,
            "name": "STEM Robot Kit",
            "product_code": "tool-free-robotics-building-set-rjc",
            "user_id": 9,
            "ai_evaluation_result": None,
        },
    )
    monkeypatch.setattr(
        material_evaluation.medias,
        "resolve_cover",
        lambda product_id, lang="en": "media/cover.jpg",
    )
    monkeypatch.setattr(
        material_evaluation.medias,
        "list_items",
        lambda product_id, lang="en": [
            {"id": 11, "lang": "en", "object_key": "media/promo.mp4"}
        ],
    )
    monkeypatch.setattr(
        material_evaluation.pushes,
        "resolve_product_page_url",
        lambda lang, product: "https://newjoyloo.com/products/tool-free-robotics-building-set-rjc",
    )
    monkeypatch.setattr(
        material_evaluation,
        "_materialize_media",
        lambda object_key: cover if object_key.endswith(".jpg") else video,
    )
    monkeypatch.setattr(material_evaluation, "_automatic_attempt_count", lambda *args: 0)
    monkeypatch.setattr(material_evaluation, "_record_attempt_start", lambda *args, **kwargs: 123)
    monkeypatch.setattr(material_evaluation, "_record_attempt_finish", lambda *args, **kwargs: None)

    def fake_invoke(*args, **kwargs):
        llm_calls.append(kwargs)
        enum = kwargs["response_schema"]["properties"]["countries"]["items"]["properties"]["lang"]["enum"]
        assert len(enum) == 1
        code = enum[0]
        lang = next(item for item in languages if item["code"] == code)
        return {
            "json": {
                "countries": [
                    {
                        "lang": code,
                        "country": lang["country"],
                        "is_suitable": scores[code] >= 70,
                        "score": scores[code],
                        "risk_level": "medium",
                        "decision": "适合推广" if scores[code] >= 70 else "谨慎推广",
                        "recommendation": "做" if scores[code] >= 70 else "不做",
                        "summary": f"{lang['country']}市场可以单独判断。",
                        "reason": f"{lang['country']}当前有独立市场判断依据。",
                        "suggestions": [f"针对{lang['country']}本土化素材"],
                    }
                ]
            },
            "usage_log_id": 100 + len(llm_calls),
        }

    monkeypatch.setattr(material_evaluation.llm_client, "invoke_generate", fake_invoke)
    monkeypatch.setattr(
        material_evaluation.medias,
        "update_product",
        lambda product_id, **kwargs: updates.update(kwargs) or 1,
    )

    result = material_evaluation.evaluate_product_if_ready(7, force=True, manual=True)

    assert result["status"] == "evaluated"
    assert len(llm_calls) == 3
    assert [call["project_id"] for call in llm_calls] == [
        "media-product-7-de",
        "media-product-7-fr",
        "media-product-7-en",
    ]
    assert [
        call["response_schema"]["properties"]["countries"]["items"]["properties"]["lang"]["enum"]
        for call in llm_calls
    ] == [["de"], ["fr"], ["en"]]
    assert "德国 / 德语(de)" in llm_calls[0]["prompt"]
    assert "法国 / 法语(fr)" not in llm_calls[0]["prompt"]
    assert [call["billing_extra"]["target_lang"] for call in llm_calls] == ["de", "fr", "en"]
    detail = json.loads(updates["ai_evaluation_detail"])
    assert detail["evaluation_mode"] == "per_country"
    assert detail["country_call_count"] == 3
    assert [row["lang"] for row in detail["countries"]] == ["de", "fr", "en"]
    assert updates["ai_score"] == 73.7


def test_evaluate_countries_records_failed_country_and_continues(monkeypatch, tmp_path):
    from appcore import material_evaluation

    languages = [
        {"code": "de", "name": "德语", "country": "德国"},
        {"code": "fr", "name": "法语", "country": "法国"},
        {"code": "en", "name": "英语", "country": "美国"},
    ]
    calls = []
    progress_events = []

    def fake_invoke(**kwargs):
        enum = kwargs["response_schema"]["properties"]["countries"]["items"]["properties"]["lang"]["enum"]
        code = enum[0]
        calls.append(code)
        if code == "fr":
            raise RuntimeError("OpenRouter 429")
        country = next(item["country"] for item in languages if item["code"] == code)
        return {
            "countries": [
                {
                    "lang": code,
                    "country": country,
                    "is_suitable": True,
                    "score": 82,
                    "risk_level": "low",
                    "decision": "适合推广",
                    "recommendation": "做",
                    "summary": f"{country}市场可推广。",
                    "reason": f"{country}素材匹配度高。",
                    "suggestions": [],
                }
            ]
        }

    monkeypatch.setattr(
        material_evaluation,
        "_invoke_evaluation_llm_with_recovery",
        fake_invoke,
    )

    normalized, recovery = material_evaluation._evaluate_countries_with_llm(
        product={"id": 7, "user_id": 9},
        product_id=7,
        product_url="https://example.test/products/stem",
        languages=languages,
        system="system",
        media=[tmp_path / "cover.jpg", tmp_path / "video.mp4"],
        llm_config={
            "provider": "openrouter",
            "model": "google/gemini-3-flash-preview",
            "search_enabled": False,
            "search_tools": [],
        },
        progress_callback=progress_events.append,
    )

    assert calls == ["de", "fr", "en"]
    assert [row["lang"] for row in normalized["countries"]] == ["de", "fr", "en"]
    failed = next(row for row in normalized["countries"] if row["lang"] == "fr")
    assert failed["is_suitable"] is False
    assert failed["score"] == 50.0
    assert normalized["ai_evaluation_result"] == "需人工复核"
    assert recovery["fr"]["error"] == "OpenRouter 429"
    assert any(
        event["countries"][1]["status"] == "failed"
        and event["countries"][2]["status"] == "queued"
        for event in progress_events
    )
    assert progress_events[-1]["status"] == "partially_completed"
    assert progress_events[-1]["failed_count"] == 1


@pytest.mark.parametrize("provider", ["gemini_vertex", "gemini_aistudio"])
def test_resolve_evaluation_llm_config_keeps_google_bindings(monkeypatch, provider):
    from appcore import material_evaluation

    monkeypatch.setattr(
        material_evaluation,
        "llm_bindings",
        SimpleNamespace(resolve=lambda code: {
            "provider": provider,
            "model": "google/gemini-3.5-flash",
        }),
        raising=False,
    )

    config = material_evaluation.resolve_evaluation_llm_config()

    assert config["provider"] == provider
    assert config["model"] == "gemini-3.5-flash"
    assert config["search_enabled"] is False
    assert config["search_tools"] == []


def test_evaluate_ready_product_sends_30s_clip_to_llm(monkeypatch, tmp_path):
    from appcore import material_evaluation

    clip_root = tmp_path / "eval_clips"
    cover = tmp_path / "cover.jpg"
    video = tmp_path / "promo.mp4"
    cover.write_bytes(b"cover")
    video.write_bytes(b"video")
    llm_calls = []
    ffmpeg_calls = []

    monkeypatch.setattr(material_evaluation, "EVAL_CLIPS_ROOT", clip_root)
    monkeypatch.setattr(
        material_evaluation.medias,
        "get_product",
        lambda product_id: {
            "id": product_id,
            "name": "Portable Neck Fan",
            "product_code": "neck-fan",
            "user_id": 9,
            "ai_evaluation_result": None,
        },
    )
    monkeypatch.setattr(
        material_evaluation.medias,
        "list_enabled_languages_kv",
        lambda: [{"code": "en", "name": "English"}, {"code": "de", "name": "German"}],
    )
    monkeypatch.setattr(
        material_evaluation,
        "evaluation_target_languages",
        lambda: [{"code": "de", "name": "德语", "country": "德国"}],
    )
    monkeypatch.setattr(
        material_evaluation.medias,
        "resolve_cover",
        lambda product_id, lang="en": "media/cover.jpg",
    )
    monkeypatch.setattr(
        material_evaluation.medias,
        "list_items",
        lambda product_id, lang="en": [
            {
                "id": 11,
                "lang": "en",
                "object_key": "media/promo.mp4",
                "duration_seconds": 31.0,
            }
        ],
    )
    monkeypatch.setattr(
        material_evaluation.pushes,
        "resolve_product_page_url",
        lambda lang, product: "https://newjoyloo.com/products/neck-fan",
    )
    monkeypatch.setattr(
        material_evaluation,
        "_materialize_media",
        lambda object_key: cover if object_key.endswith(".jpg") else video,
    )
    monkeypatch.setattr(material_evaluation, "_automatic_attempt_count", lambda *args: 0)
    monkeypatch.setattr(material_evaluation, "_record_attempt_start", lambda *args, **kwargs: 123)
    monkeypatch.setattr(material_evaluation, "_record_attempt_finish", lambda *args, **kwargs: None)

    def fake_run(cmd, **kwargs):
        ffmpeg_calls.append(cmd)
        Path(cmd[-1]).write_bytes(b"clip")

        class Result:
            returncode = 0
            stderr = b""

        return Result()

    monkeypatch.setattr("subprocess.run", fake_run)

    from appcore.llm_media_optimizer import OptimizedMedia

    def fake_prepare(video_path, policy, output_dir=None, output_path=None):
        Path(output_path).write_bytes(b"llm-clip")
        return OptimizedMedia(
            original_path=str(video_path),
            llm_path=str(output_path),
            optimized=True,
            cleanup_path=str(output_path),
            original_bytes=4,
            llm_bytes=8,
            command=["ffmpeg"],
            policy_name=policy.name,
        )

    monkeypatch.setattr(material_evaluation, "prepare_video_for_llm", fake_prepare)
    monkeypatch.setattr(
        material_evaluation.llm_client,
        "invoke_generate",
        lambda *args, **kwargs: llm_calls.append((args, kwargs))
        or {
            "json": {
                "countries": [
                    {
                        "lang": "de",
                        "country": "Germany",
                        "is_suitable": True,
                        "score": 88,
                        "risk_level": "low",
                        "decision": "适合推广",
                        "reason": "portable cooling demand is clear",
                        "suggestions": [],
                    }
                ]
            }
        },
    )
    monkeypatch.setattr(
        material_evaluation.medias,
        "update_product",
        lambda product_id, **kwargs: 1,
    )

    result = material_evaluation.evaluate_product_if_ready(7)

    assert result["status"] == "evaluated"
    assert ffmpeg_calls
    assert "-ss" in ffmpeg_calls[0]
    assert ffmpeg_calls[0][ffmpeg_calls[0].index("-ss") + 1] == "0"
    assert "-t" in ffmpeg_calls[0]
    assert ffmpeg_calls[0][ffmpeg_calls[0].index("-t") + 1] == "30"
    media = llm_calls[0][1]["media"]
    assert media[0] == cover
    assert str(media[1]).endswith("11_30s_llm.mp4")
    assert Path(media[1]).read_bytes() == b"llm-clip"


def test_make_eval_clip_15s_optimizes_short_video_without_raw_cut(monkeypatch, tmp_path):
    from appcore import material_evaluation
    from appcore.llm_media_optimizer import OptimizedMedia

    video = tmp_path / "short.mp4"
    video.write_bytes(b"video")

    monkeypatch.setattr(material_evaluation, "_materialize_media", lambda object_key: video)

    def fail_if_ffmpeg_runs(*args, **kwargs):
        raise AssertionError("ffmpeg should not run for videos within 15 seconds")

    monkeypatch.setattr("subprocess.run", fail_if_ffmpeg_runs)

    captured = {}

    def fake_prepare(video_path, policy, output_dir=None, output_path=None):
        captured["video_path"] = str(video_path)
        Path(output_path).write_bytes(b"llm-short")
        return OptimizedMedia(
            original_path=str(video_path),
            llm_path=str(output_path),
            optimized=True,
            cleanup_path=str(output_path),
            original_bytes=5,
            llm_bytes=9,
            command=["ffmpeg"],
            policy_name=policy.name,
        )

    monkeypatch.setattr(material_evaluation, "prepare_video_for_llm", fake_prepare)

    result = material_evaluation._make_eval_clip_15s(
        7,
        {"id": 11, "object_key": "media/short.mp4", "duration_seconds": 14.9},
        clips_root=tmp_path / "eval_clips",
    )

    assert captured["video_path"] == str(video)
    assert result.name == "11_15s_llm.mp4"
    assert result.read_bytes() == b"llm-short"


def test_auto_evaluation_skips_after_one_logged_attempt(monkeypatch, tmp_path):
    from appcore import material_evaluation

    invoked = []

    monkeypatch.setattr(
        material_evaluation.medias,
        "get_product",
        lambda product_id: {
            "id": product_id,
            "name": "Portable Neck Fan",
            "product_code": "neck-fan",
            "user_id": 9,
            "ai_evaluation_result": None,
        },
    )
    monkeypatch.setattr(
        material_evaluation.medias,
        "list_enabled_languages_kv",
        lambda: [{"code": "en", "name": "English"}, {"code": "de", "name": "German"}],
    )
    monkeypatch.setattr(
        material_evaluation.medias,
        "resolve_cover",
        lambda product_id, lang="en": "media/cover.jpg",
    )
    monkeypatch.setattr(
        material_evaluation.medias,
        "list_items",
        lambda product_id, lang="en": [
            {"id": 11, "lang": "en", "object_key": "media/promo.mp4"}
        ],
    )
    monkeypatch.setattr(
        material_evaluation.pushes,
        "resolve_product_page_url",
        lambda lang, product: "https://newjoyloo.com/products/neck-fan",
    )
    monkeypatch.setattr(material_evaluation, "_automatic_attempt_count", lambda *args: 1)
    monkeypatch.setattr(material_evaluation, "_record_attempt_start", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        material_evaluation.llm_client,
        "invoke_generate",
        lambda *args, **kwargs: invoked.append((args, kwargs)),
    )

    result = material_evaluation.evaluate_product_if_ready(7)

    assert result == {
        "status": "auto_attempt_limit_reached",
        "product_id": 7,
        "attempts": 1,
    }
    assert invoked == []


def test_evaluation_blocks_404_product_link_before_llm(monkeypatch):
    from appcore import material_evaluation

    calls = {"llm": 0, "updates": 0, "attempts": 0, "materialize": 0}

    monkeypatch.setattr(
        material_evaluation.medias,
        "get_product",
        lambda product_id: {
            "id": product_id,
            "name": "Missing Shopify Product",
            "product_code": "missing-product-rjc",
            "user_id": 9,
            "ai_evaluation_result": None,
        },
    )
    monkeypatch.setattr(
        material_evaluation.medias,
        "list_enabled_languages_kv",
        lambda: [{"code": "en", "name": "English"}, {"code": "de", "name": "German"}],
    )
    monkeypatch.setattr(
        material_evaluation.pushes,
        "resolve_product_page_url",
        lambda lang, product: "https://newjoyloo.com/products/missing-product-rjc",
    )
    monkeypatch.setattr(
        material_evaluation.pushes,
        "probe_ad_url",
        lambda url: (False, "HTTP 404"),
    )
    monkeypatch.setattr(
        material_evaluation.medias,
        "resolve_cover",
        lambda product_id, lang="en": "media/cover.jpg",
    )
    monkeypatch.setattr(
        material_evaluation.medias,
        "list_items",
        lambda product_id, lang="en": [
            {"id": 11, "lang": "en", "object_key": "media/promo.mp4"}
        ],
    )
    monkeypatch.setattr(
        material_evaluation,
        "_materialize_media",
        lambda object_key: calls.__setitem__("materialize", calls["materialize"] + 1),
    )
    monkeypatch.setattr(
        material_evaluation,
        "_record_attempt_start",
        lambda *args, **kwargs: calls.__setitem__("attempts", calls["attempts"] + 1),
    )
    monkeypatch.setattr(
        material_evaluation.llm_client,
        "invoke_generate",
        lambda *args, **kwargs: calls.__setitem__("llm", calls["llm"] + 1),
    )
    monkeypatch.setattr(
        material_evaluation.medias,
        "update_product",
        lambda product_id, **kwargs: calls.__setitem__("updates", calls["updates"] + 1),
    )

    result = material_evaluation.evaluate_product_if_ready(7, force=True, manual=True)

    assert result["status"] == "product_link_unavailable"
    assert result["product_id"] == 7
    assert result["product_url"] == "https://newjoyloo.com/products/missing-product-rjc"
    assert result["error"] == "HTTP 404"
    assert calls == {"llm": 0, "updates": 0, "attempts": 0, "materialize": 0}


def test_evaluation_blocks_missing_product_link_before_llm(monkeypatch):
    from appcore import material_evaluation

    calls = {"llm": 0, "updates": 0, "attempts": 0, "materialize": 0}

    monkeypatch.setattr(
        material_evaluation.medias,
        "get_product",
        lambda product_id: {
            "id": product_id,
            "name": "Missing Link Product",
            "product_code": "",
            "user_id": 9,
            "ai_evaluation_result": None,
        },
    )
    monkeypatch.setattr(
        material_evaluation.medias,
        "list_enabled_languages_kv",
        lambda: [{"code": "en", "name": "English"}, {"code": "de", "name": "German"}],
    )
    monkeypatch.setattr(material_evaluation.pushes, "resolve_product_page_url", lambda lang, product: "")
    monkeypatch.setattr(
        material_evaluation,
        "_materialize_media",
        lambda object_key: calls.__setitem__("materialize", calls["materialize"] + 1),
    )
    monkeypatch.setattr(
        material_evaluation,
        "_record_attempt_start",
        lambda *args, **kwargs: calls.__setitem__("attempts", calls["attempts"] + 1),
    )
    monkeypatch.setattr(
        material_evaluation.llm_client,
        "invoke_generate",
        lambda *args, **kwargs: calls.__setitem__("llm", calls["llm"] + 1),
    )
    monkeypatch.setattr(
        material_evaluation.medias,
        "update_product",
        lambda product_id, **kwargs: calls.__setitem__("updates", calls["updates"] + 1),
    )

    result = material_evaluation.evaluate_product_if_ready(7, force=True, manual=True)

    assert result == {"status": "missing_product_link", "product_id": 7}
    assert calls == {"llm": 0, "updates": 0, "attempts": 0, "materialize": 0}


def test_evaluation_blocks_missing_local_cover_before_llm(monkeypatch, tmp_path):
    from appcore import material_evaluation

    video = tmp_path / "promo.mp4"
    video.write_bytes(b"video")
    calls = {"llm": 0, "updates": 0, "attempts": 0}

    monkeypatch.setattr(
        material_evaluation.medias,
        "get_product",
        lambda product_id: {
            "id": product_id,
            "name": "No Cover Product",
            "product_code": "no-cover-rjc",
            "user_id": 9,
            "ai_evaluation_result": None,
        },
    )
    monkeypatch.setattr(
        material_evaluation.medias,
        "list_enabled_languages_kv",
        lambda: [{"code": "en", "name": "English"}, {"code": "de", "name": "German"}],
    )
    monkeypatch.setattr(
        material_evaluation.pushes,
        "resolve_product_page_url",
        lambda lang, product: "https://newjoyloo.com/products/no-cover-rjc",
    )
    monkeypatch.setattr(material_evaluation.pushes, "probe_ad_url", lambda url: (True, None))
    monkeypatch.setattr(
        material_evaluation.medias,
        "resolve_cover",
        lambda product_id, lang="en": "media/cover.jpg",
    )
    monkeypatch.setattr(
        material_evaluation.medias,
        "list_items",
        lambda product_id, lang="en": [
            {"id": 11, "lang": "en", "object_key": "media/promo.mp4"}
        ],
    )

    def fake_materialize(object_key):
        if object_key.endswith(".jpg"):
            raise FileNotFoundError(object_key)
        return video

    monkeypatch.setattr(material_evaluation, "_materialize_media", fake_materialize)
    monkeypatch.setattr(
        material_evaluation,
        "_record_attempt_start",
        lambda *args, **kwargs: calls.__setitem__("attempts", calls["attempts"] + 1),
    )
    monkeypatch.setattr(
        material_evaluation.llm_client,
        "invoke_generate",
        lambda *args, **kwargs: calls.__setitem__("llm", calls["llm"] + 1),
    )
    monkeypatch.setattr(
        material_evaluation.medias,
        "update_product",
        lambda product_id, **kwargs: calls.__setitem__("updates", calls["updates"] + 1),
    )

    result = material_evaluation.evaluate_product_if_ready(7, force=True, manual=True)

    assert result == {
        "status": "missing_cover_file",
        "product_id": 7,
        "object_key": "media/cover.jpg",
    }
    assert calls == {"llm": 0, "updates": 0, "attempts": 0}


def test_evaluation_blocks_missing_local_video_before_llm(monkeypatch, tmp_path):
    from appcore import material_evaluation

    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"cover")
    calls = {"llm": 0, "updates": 0, "attempts": 0}

    monkeypatch.setattr(
        material_evaluation.medias,
        "get_product",
        lambda product_id: {
            "id": product_id,
            "name": "No Video Product",
            "product_code": "no-video-rjc",
            "user_id": 9,
            "ai_evaluation_result": None,
        },
    )
    monkeypatch.setattr(
        material_evaluation.medias,
        "list_enabled_languages_kv",
        lambda: [{"code": "en", "name": "English"}, {"code": "de", "name": "German"}],
    )
    monkeypatch.setattr(
        material_evaluation.pushes,
        "resolve_product_page_url",
        lambda lang, product: "https://newjoyloo.com/products/no-video-rjc",
    )
    monkeypatch.setattr(material_evaluation.pushes, "probe_ad_url", lambda url: (True, None))
    monkeypatch.setattr(
        material_evaluation.medias,
        "resolve_cover",
        lambda product_id, lang="en": "media/cover.jpg",
    )
    monkeypatch.setattr(
        material_evaluation.medias,
        "list_items",
        lambda product_id, lang="en": [
            {"id": 11, "lang": "en", "object_key": "media/promo.mp4"}
        ],
    )
    monkeypatch.setattr(material_evaluation, "_materialize_media", lambda object_key: cover)
    monkeypatch.setattr(
        material_evaluation,
        "_make_eval_clip_30s",
        lambda product_id, item: (_ for _ in ()).throw(FileNotFoundError(item["object_key"])),
    )
    monkeypatch.setattr(
        material_evaluation,
        "_record_attempt_start",
        lambda *args, **kwargs: calls.__setitem__("attempts", calls["attempts"] + 1),
    )
    monkeypatch.setattr(
        material_evaluation.llm_client,
        "invoke_generate",
        lambda *args, **kwargs: calls.__setitem__("llm", calls["llm"] + 1),
    )
    monkeypatch.setattr(
        material_evaluation.medias,
        "update_product",
        lambda product_id, **kwargs: calls.__setitem__("updates", calls["updates"] + 1),
    )

    result = material_evaluation.evaluate_product_if_ready(7, force=True, manual=True)

    assert result == {
        "status": "missing_video_file",
        "product_id": 7,
        "object_key": "media/promo.mp4",
    }
    assert calls == {"llm": 0, "updates": 0, "attempts": 0}


def test_manual_evaluation_bypasses_auto_attempt_limit(monkeypatch, tmp_path):
    from appcore import material_evaluation

    cover = tmp_path / "cover.jpg"
    video = tmp_path / "promo.mp4"
    cover.write_bytes(b"cover")
    video.write_bytes(b"video")
    calls = []
    updates = {}

    monkeypatch.setattr(
        material_evaluation.medias,
        "get_product",
        lambda product_id: {
            "id": product_id,
            "name": "Portable Neck Fan",
            "product_code": "neck-fan",
            "user_id": 9,
            "ai_evaluation_result": "评估失败",
        },
    )
    monkeypatch.setattr(
        material_evaluation.medias,
        "list_enabled_languages_kv",
        lambda: [{"code": "en", "name": "English"}, {"code": "de", "name": "German"}],
    )
    monkeypatch.setattr(
        material_evaluation.medias,
        "resolve_cover",
        lambda product_id, lang="en": "media/cover.jpg",
    )
    monkeypatch.setattr(
        material_evaluation.medias,
        "list_items",
        lambda product_id, lang="en": [
            {"id": 11, "lang": "en", "object_key": "media/promo.mp4"}
        ],
    )
    monkeypatch.setattr(
        material_evaluation.pushes,
        "resolve_product_page_url",
        lambda lang, product: "https://newjoyloo.com/products/neck-fan",
    )
    monkeypatch.setattr(
        material_evaluation,
        "_materialize_media",
        lambda object_key: cover if object_key.endswith(".jpg") else video,
    )
    monkeypatch.setattr(material_evaluation, "_automatic_attempt_count", lambda *args: 9)
    monkeypatch.setattr(
        material_evaluation,
        "_record_attempt_start",
        lambda *args, **kwargs: calls.append((args, kwargs)) or 123,
    )
    monkeypatch.setattr(
        material_evaluation,
        "_record_attempt_finish",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    monkeypatch.setattr(
        material_evaluation.llm_client,
        "invoke_generate",
        lambda *args, **kwargs: {
            "json": {
                "countries": [
                    *_fixed_target_country_rows(score=88),
                    {
                        "lang": "de",
                        "country": "Germany",
                        "is_suitable": True,
                        "score": 88,
                        "risk_level": "low",
                        "decision": "适合推广",
                        "reason": "便携场景明确。",
                        "suggestions": [],
                    }
                ]
            }
        },
    )
    monkeypatch.setattr(
        material_evaluation.medias,
        "update_product",
        lambda product_id, **kwargs: updates.update(kwargs) or 1,
    )

    result = material_evaluation.evaluate_product_if_ready(7, force=True, manual=True)

    assert result["status"] == "evaluated"
    assert updates["ai_evaluation_result"] == "适合推广"
    assert calls[0][1]["trigger"] == "manual"


def test_evaluation_failure_returns_error_and_saves_detail(monkeypatch, tmp_path):
    from appcore import material_evaluation

    cover = tmp_path / "cover.jpg"
    video = tmp_path / "promo.mp4"
    cover.write_bytes(b"cover")
    video.write_bytes(b"video")
    updates = {}
    finishes = []

    monkeypatch.setattr(
        material_evaluation.medias,
        "get_product",
        lambda product_id: {
            "id": product_id,
            "name": "Portable Neck Fan",
            "product_code": "neck-fan",
            "user_id": 9,
            "ai_evaluation_result": None,
        },
    )
    monkeypatch.setattr(
        material_evaluation.medias,
        "list_enabled_languages_kv",
        lambda: [{"code": "en", "name": "English"}, {"code": "de", "name": "German"}],
    )
    monkeypatch.setattr(
        material_evaluation.medias,
        "resolve_cover",
        lambda product_id, lang="en": "media/cover.jpg",
    )
    monkeypatch.setattr(
        material_evaluation.medias,
        "list_items",
        lambda product_id, lang="en": [
            {"id": 11, "lang": "en", "object_key": "media/promo.mp4"}
        ],
    )
    monkeypatch.setattr(
        material_evaluation.pushes,
        "resolve_product_page_url",
        lambda lang, product: "https://newjoyloo.com/products/neck-fan",
    )
    monkeypatch.setattr(
        material_evaluation,
        "_materialize_media",
        lambda object_key: cover if object_key.endswith(".jpg") else video,
    )
    monkeypatch.setattr(material_evaluation, "_automatic_attempt_count", lambda *args: 0)
    monkeypatch.setattr(material_evaluation, "_record_attempt_start", lambda *args, **kwargs: 123)
    monkeypatch.setattr(
        material_evaluation,
        "_record_attempt_finish",
        lambda *args, **kwargs: finishes.append((args, kwargs)),
    )
    monkeypatch.setattr(
        material_evaluation.llm_client,
        "invoke_generate",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("upstream timeout")),
    )
    monkeypatch.setattr(
        material_evaluation.medias,
        "update_product",
        lambda product_id, **kwargs: updates.update(kwargs) or 1,
    )

    result = material_evaluation.evaluate_product_if_ready(7)

    assert result == {"status": "failed", "product_id": 7, "error": "upstream timeout"}
    assert updates["ai_evaluation_result"] == "评估失败"
    detail = json.loads(updates["ai_evaluation_detail"])
    assert detail["error"] == "upstream timeout"
    assert finishes[0][1] == {"success": False, "error": "upstream timeout"}


def test_evaluation_repairs_invalid_llm_json_before_marking_failed(monkeypatch, tmp_path):
    from appcore import material_evaluation

    cover = tmp_path / "cover.jpg"
    video = tmp_path / "promo.mp4"
    cover.write_bytes(b"cover")
    video.write_bytes(b"video")
    updates = {}
    calls = []

    monkeypatch.setattr(
        material_evaluation.medias,
        "get_product",
        lambda product_id: {
            "id": product_id,
            "name": "Portable Neck Fan",
            "product_code": "neck-fan",
            "user_id": 9,
            "ai_evaluation_result": None,
        },
    )
    monkeypatch.setattr(
        material_evaluation.medias,
        "list_enabled_languages_kv",
        lambda: [{"code": "en", "name": "English"}, {"code": "de", "name": "German"}],
    )
    monkeypatch.setattr(
        material_evaluation,
        "evaluation_target_languages",
        lambda: [{"code": "de", "name": "德语", "country": "德国"}],
    )
    monkeypatch.setattr(
        material_evaluation.medias,
        "resolve_cover",
        lambda product_id, lang="en": "media/cover.jpg",
    )
    monkeypatch.setattr(
        material_evaluation.medias,
        "list_items",
        lambda product_id, lang="en": [
            {"id": 11, "lang": "en", "object_key": "media/promo.mp4"}
        ],
    )
    monkeypatch.setattr(
        material_evaluation.pushes,
        "resolve_product_page_url",
        lambda lang, product: "https://newjoyloo.com/products/neck-fan",
    )
    monkeypatch.setattr(
        material_evaluation,
        "_materialize_media",
        lambda object_key: cover if object_key.endswith(".jpg") else video,
    )
    monkeypatch.setattr(material_evaluation, "_automatic_attempt_count", lambda *args: 0)
    monkeypatch.setattr(material_evaluation, "_record_attempt_start", lambda *args, **kwargs: 123)
    monkeypatch.setattr(material_evaluation, "_record_attempt_finish", lambda *args, **kwargs: None)

    def fake_invoke(*args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return {
                "json": None,
                "text": '{"countries":[{"lang":"de","reason":"truncated',
                "json_parse_error": "Unterminated string",
                "usage_log_id": 101,
            }
        assert kwargs["media"] is None
        assert kwargs["google_search"] is False
        assert "修复" in kwargs["prompt"]
        assert "Unterminated string" in kwargs["prompt"]
        return {
            "json": {
                "countries": _fixed_target_country_rows(score=88)
            },
            "usage_log_id": 102,
        }

    monkeypatch.setattr(material_evaluation.llm_client, "invoke_generate", fake_invoke)
    monkeypatch.setattr(
        material_evaluation.medias,
        "update_product",
        lambda product_id, **kwargs: updates.update(kwargs) or 1,
    )

    result = material_evaluation.evaluate_product_if_ready(7)

    assert result["status"] == "evaluated"
    assert updates["ai_evaluation_result"] == "适合推广"
    detail = json.loads(updates["ai_evaluation_detail"])
    assert detail["llm_recovery"]["de"]["json_repair_succeeded"] is True
    assert detail["llm_recovery"]["de"]["initial_usage_log_id"] == 101
    assert detail["llm_recovery"]["de"]["repair_usage_log_id"] == 102


def test_evaluation_retries_original_call_when_invalid_json_has_no_raw_text(monkeypatch, tmp_path):
    from appcore import material_evaluation

    cover = tmp_path / "cover.jpg"
    video = tmp_path / "promo.mp4"
    cover.write_bytes(b"cover")
    video.write_bytes(b"video")
    updates = {}
    calls = []

    monkeypatch.setattr(
        material_evaluation.medias,
        "get_product",
        lambda product_id: {
            "id": product_id,
            "name": "Portable Neck Fan",
            "product_code": "neck-fan",
            "user_id": 9,
            "ai_evaluation_result": None,
        },
    )
    monkeypatch.setattr(
        material_evaluation.medias,
        "list_enabled_languages_kv",
        lambda: [{"code": "de", "name": "German"}],
    )
    monkeypatch.setattr(
        material_evaluation,
        "evaluation_target_languages",
        lambda: [{"code": "de", "name": "德语", "country": "德国"}],
    )
    monkeypatch.setattr(
        material_evaluation.medias,
        "resolve_cover",
        lambda product_id, lang="en": "media/cover.jpg",
    )
    monkeypatch.setattr(
        material_evaluation.medias,
        "list_items",
        lambda product_id, lang="en": [
            {"id": 11, "lang": "en", "object_key": "media/promo.mp4"}
        ],
    )
    monkeypatch.setattr(
        material_evaluation.pushes,
        "resolve_product_page_url",
        lambda lang, product: "https://newjoyloo.com/products/neck-fan",
    )
    monkeypatch.setattr(
        material_evaluation,
        "_materialize_media",
        lambda object_key: cover if object_key.endswith(".jpg") else video,
    )
    monkeypatch.setattr(material_evaluation, "_automatic_attempt_count", lambda *args: 0)
    monkeypatch.setattr(material_evaluation, "_record_attempt_start", lambda *args, **kwargs: 123)
    monkeypatch.setattr(material_evaluation, "_record_attempt_finish", lambda *args, **kwargs: None)

    def fake_invoke(*args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return {
                "json": None,
                "text": "",
                "json_parse_error": "Expecting value: line 1 column 1 (char 0)",
                "usage_log_id": 201,
            }
        assert kwargs["media"] == [cover, video]
        assert kwargs["project_id"] == "media-product-7-de-retry-2"
        return {
            "json": {
                "countries": _fixed_target_country_rows(score=88)
            },
            "usage_log_id": 202,
        }

    monkeypatch.setattr(material_evaluation.llm_client, "invoke_generate", fake_invoke)
    monkeypatch.setattr(
        material_evaluation.medias,
        "update_product",
        lambda product_id, **kwargs: updates.update(kwargs) or 1,
    )

    result = material_evaluation.evaluate_product_if_ready(7)

    assert result["status"] == "evaluated"
    assert updates["ai_evaluation_result"] == "适合推广"
    detail = json.loads(updates["ai_evaluation_detail"])
    assert detail["llm_recovery"]["de"]["original_retry_attempted"] is True
    assert detail["llm_recovery"]["de"]["retry_usage_log_id"] == 202


def test_manual_evaluation_failure_preserves_existing_success(monkeypatch, tmp_path):
    from appcore import material_evaluation

    cover = tmp_path / "cover.jpg"
    video = tmp_path / "promo.mp4"
    cover.write_bytes(b"cover")
    video.write_bytes(b"video")
    updates = []
    finishes = []

    existing_detail = {
        "countries": [
            {
                "lang": "de",
                "country": "Germany",
                "is_suitable": True,
                "score": 75,
            }
        ]
    }
    monkeypatch.setattr(
        material_evaluation.medias,
        "get_product",
        lambda product_id: {
            "id": product_id,
            "name": "Portable Neck Fan",
            "product_code": "neck-fan",
            "user_id": 9,
            "ai_score": 75,
            "ai_evaluation_result": "适合推广",
            "ai_evaluation_detail": json.dumps(existing_detail, ensure_ascii=False),
        },
    )
    monkeypatch.setattr(
        material_evaluation.medias,
        "list_enabled_languages_kv",
        lambda: [{"code": "en", "name": "English"}, {"code": "de", "name": "German"}],
    )
    monkeypatch.setattr(
        material_evaluation.medias,
        "resolve_cover",
        lambda product_id, lang="en": "media/cover.jpg",
    )
    monkeypatch.setattr(
        material_evaluation.medias,
        "list_items",
        lambda product_id, lang="en": [
            {"id": 11, "lang": "en", "object_key": "media/promo.mp4"}
        ],
    )
    monkeypatch.setattr(
        material_evaluation.pushes,
        "resolve_product_page_url",
        lambda lang, product: "https://newjoyloo.com/products/neck-fan",
    )
    monkeypatch.setattr(
        material_evaluation,
        "_materialize_media",
        lambda object_key: cover if object_key.endswith(".jpg") else video,
    )
    monkeypatch.setattr(material_evaluation, "_automatic_attempt_count", lambda *args: 0)
    monkeypatch.setattr(material_evaluation, "_record_attempt_start", lambda *args, **kwargs: 123)
    monkeypatch.setattr(
        material_evaluation,
        "_record_attempt_finish",
        lambda *args, **kwargs: finishes.append((args, kwargs)),
    )
    monkeypatch.setattr(
        material_evaluation.llm_client,
        "invoke_generate",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("upstream timeout")),
    )
    monkeypatch.setattr(
        material_evaluation.medias,
        "update_product",
        lambda product_id, **kwargs: updates.append(kwargs) or 1,
    )

    result = material_evaluation.evaluate_product_if_ready(7, force=True, manual=True)

    assert result == {
        "status": "failed",
        "product_id": 7,
        "error": "upstream timeout",
        "preserved_existing_evaluation": True,
    }
    assert updates == []
    assert finishes[0][1] == {"success": False, "error": "upstream timeout"}


def test_evaluate_product_waits_when_english_items_are_not_videos(monkeypatch):
    from appcore import material_evaluation

    invoked = []
    monkeypatch.setattr(
        material_evaluation.medias,
        "get_product",
        lambda product_id: {
            "id": product_id,
            "name": "Portable Neck Fan",
            "product_code": "neck-fan",
            "user_id": 9,
            "ai_evaluation_result": None,
        },
    )
    monkeypatch.setattr(
        material_evaluation.medias,
        "list_enabled_languages_kv",
        lambda: [{"code": "en", "name": "鑻辫"}, {"code": "de", "name": "寰疯"}],
    )
    monkeypatch.setattr(
        material_evaluation.pushes,
        "resolve_product_page_url",
        lambda lang, product: "https://newjoyloo.com/products/neck-fan",
    )
    monkeypatch.setattr(
        material_evaluation.medias,
        "resolve_cover",
        lambda product_id, lang="en": "media/cover.jpg",
    )
    monkeypatch.setattr(
        material_evaluation.medias,
        "list_items",
        lambda product_id, lang="en": [
            {"id": 11, "lang": "en", "filename": "detail.jpg", "object_key": "media/detail.jpg"}
        ],
    )
    monkeypatch.setattr(
        material_evaluation.llm_client,
        "invoke_generate",
        lambda *args, **kwargs: invoked.append((args, kwargs)),
    )

    result = material_evaluation.evaluate_product_if_ready(7)

    assert result == {"status": "missing_video", "product_id": 7}
    assert invoked == []


def test_first_english_video_skips_image_items():
    from appcore import material_evaluation

    class FakeMedias:
        @staticmethod
        def list_items(product_id, lang="en"):
            return [
                {"id": 10, "filename": "main.jpg", "object_key": "media/main.jpg"},
                {"id": 11, "filename": "promo.mp4", "object_key": "media/promo.mp4"},
            ]

    original = material_evaluation.medias
    try:
        material_evaluation.medias = FakeMedias
        assert material_evaluation._first_english_video(7)["id"] == 11
    finally:
        material_evaluation.medias = original


def test_medias_route_schedules_material_evaluation_in_background(monkeypatch):
    from web.routes import medias as route
    from appcore import runner_lifecycle

    calls = []
    monkeypatch.setattr(
        runner_lifecycle,
        "start_tracked_thread",
        lambda **kwargs: calls.append(kwargs) or True,
    )

    assert route._schedule_material_evaluation(7, force=True) is True

    assert calls == [
        {
            "project_type": "material_evaluation",
            "task_id": "7",
            "target": route.material_evaluation.evaluate_product_if_ready,
            "args": (7,),
            "kwargs": {"force": True, "manual": False},
            "daemon": True,
            "user_id": None,
            "runner": "appcore.material_evaluation.evaluate_product_if_ready",
            "entrypoint": "medias.material_evaluation",
            "stage": "queued_evaluation",
            "details": {"force": True, "manual": False},
        }
    ]


def test_medias_route_skips_duplicate_material_evaluation_thread(monkeypatch):
    from web.routes import medias as route
    from appcore import runner_lifecycle

    monkeypatch.setattr(
        runner_lifecycle,
        "start_tracked_thread",
        lambda **kwargs: False,
    )

    assert route._schedule_material_evaluation(7) is False


def test_find_ready_product_ids_uses_exists_without_distinct(monkeypatch):
    from appcore import material_evaluation

    captured = {}

    def fake_query(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return [{"id": 7}, {"id": 8}]

    monkeypatch.setattr(material_evaluation, "query", fake_query)

    assert material_evaluation.find_ready_product_ids(limit=2) == [7, 8]
    assert "EXISTS (" in captured["sql"]
    assert "LOWER(i.object_key) LIKE '%%.mp4'" in captured["sql"]
    assert "LIKE '%.mp4'" not in captured["sql"]
    assert "SELECT DISTINCT" not in captured["sql"]
    assert captured["args"] == (2,)


def test_normalize_result_fills_missing_language_for_manual_review():
    from appcore import material_evaluation

    languages = [
        {"code": "de", "name": "德语"},
        {"code": "fi", "name": "芬兰语"},
    ]
    raw = {
        "countries": [
            {
                "lang": "de",
                "country": "德国",
                "is_suitable": True,
                "score": 80,
                "risk_level": "low",
                "decision": "适合推广",
                "reason": "德国通勤和户外场景需求明确。",
                "suggestions": [],
            }
        ]
    }

    normalized = material_evaluation.normalize_result(raw, languages)

    assert [row["lang"] for row in normalized["countries"]] == ["de", "fi"]
    assert normalized["countries"][1]["decision"] == "谨慎推广"
    assert normalized["countries"][1]["reason"] == "模型未返回该语种结果，需人工复核。"
    assert normalized["ai_evaluation_result"] == "需人工复核"
    assert "listing_status" not in normalized


def test_make_eval_clip_15s_optimizes_clip_for_llm(monkeypatch, tmp_path):
    from unittest.mock import MagicMock

    from appcore.llm_media_optimizer import OptimizedMedia
    from appcore import material_evaluation

    source = tmp_path / "source.mp4"
    source.write_bytes(b"source-video")
    captured = {}

    monkeypatch.setattr(material_evaluation, "_materialize_media", lambda key: source)

    def fake_run(cmd, **kwargs):
        Path(cmd[-1]).write_bytes(b"raw-clip")
        return MagicMock(returncode=0, stderr=b"")

    def fake_prepare(video_path, policy, output_dir=None, output_path=None):
        captured["video_path"] = str(video_path)
        captured["policy"] = policy
        captured["output_dir"] = output_dir
        captured["output_path"] = output_path
        Path(output_path).write_bytes(b"llm-clip")
        return OptimizedMedia(
            original_path=str(video_path),
            llm_path=str(output_path),
            optimized=True,
            cleanup_path=str(output_path),
            original_bytes=8,
            llm_bytes=8,
            command=["ffmpeg"],
            policy_name=policy.name,
        )

    monkeypatch.setattr(material_evaluation.subprocess, "run", fake_run)
    monkeypatch.setattr(material_evaluation, "prepare_video_for_llm", fake_prepare)

    result = material_evaluation._make_eval_clip_15s(
        7,
        {"id": 99, "object_key": "key/source.mp4", "duration_seconds": 30},
        clips_root=tmp_path / "eval_clips",
    )

    assert result.name == "99_15s_llm.mp4"
    assert result.read_bytes() == b"llm-clip"
    assert captured["policy"].name == "short_clip_audio"
    assert captured["video_path"].endswith("99_15s.mp4")
    assert captured["output_path"] == result


def test_make_eval_clip_15s_falls_back_to_raw_clip_when_optimization_fails(monkeypatch, tmp_path):
    from unittest.mock import MagicMock

    from appcore.llm_media_optimizer import OptimizedMedia
    from appcore import material_evaluation

    source = tmp_path / "source.mp4"
    source.write_bytes(b"source-video")

    monkeypatch.setattr(material_evaluation, "_materialize_media", lambda key: source)

    def fake_run(cmd, **kwargs):
        Path(cmd[-1]).write_bytes(b"raw-clip")
        return MagicMock(returncode=0, stderr=b"")

    def fake_prepare(video_path, policy, output_dir=None, output_path=None):
        return OptimizedMedia(
            original_path=str(video_path),
            llm_path=str(video_path),
            optimized=False,
            cleanup_path=None,
            original_bytes=8,
            llm_bytes=8,
            command=["ffmpeg"],
            error="ffmpeg failed",
            policy_name=policy.name,
        )

    monkeypatch.setattr(material_evaluation.subprocess, "run", fake_run)
    monkeypatch.setattr(material_evaluation, "prepare_video_for_llm", fake_prepare)

    result = material_evaluation._make_eval_clip_15s(
        7,
        {"id": 88, "object_key": "key/source.mp4", "duration_seconds": 30},
        clips_root=tmp_path / "eval_clips",
    )

    assert result.name == "88_15s.mp4"
    assert result.read_bytes() == b"raw-clip"


def test_build_request_debug_payload_base64_uses_llm_eval_video(monkeypatch, tmp_path):
    from appcore import material_evaluation

    cover_path = tmp_path / "cover.jpg"
    video_path = tmp_path / "99_30s_llm.mp4"
    cover_path.write_bytes(b"cover")
    video_path.write_bytes(b"llm-video")
    product = {
        "id": 7,
        "name": "Debug Product",
        "product_code": "DP-7",
        "product_link": "https://example.test/products/debug",
        "user_id": 42,
        "cover_object_key": "covers/7.jpg",
    }
    video = {
        "id": 99,
        "object_key": "videos/7.mp4",
        "filename": "7.mp4",
        "duration_seconds": 30,
        "file_size": 123,
    }

    monkeypatch.setattr(material_evaluation.medias, "get_product", lambda pid: product)
    monkeypatch.setattr(
        material_evaluation.medias,
        "list_enabled_languages_kv",
        lambda: [{"code": "de", "name": "German"}],
    )
    monkeypatch.setattr(material_evaluation.medias, "resolve_cover", lambda pid, lang: "covers/7.jpg")
    monkeypatch.setattr(material_evaluation.medias, "list_items", lambda pid, lang=None: [video])
    monkeypatch.setattr(material_evaluation.pushes, "resolve_product_page_url", lambda lang, product: product["product_link"])
    monkeypatch.setattr(material_evaluation, "_materialize_media", lambda object_key: cover_path)
    monkeypatch.setattr(material_evaluation, "_make_eval_clip_30s", lambda pid, item: video_path)

    payload = material_evaluation.build_request_debug_payload(7, include_base64=True)

    assert payload["media"][1]["role"] == "english_video"
    assert payload["media"][1]["byte_size"] == len(b"llm-video")
    assert payload["request"]["media"][1]["filename"] == "7.mp4"


def test_build_request_debug_payload_preview_visualizes_processed_eval_clip(monkeypatch, tmp_path):
    from appcore import material_evaluation

    product = {
        "id": 7,
        "name": "Debug Product",
        "product_code": "DP-7",
        "product_link": "https://example.test/products/debug",
        "user_id": 42,
        "cover_object_key": "covers/7.jpg",
    }
    video = {
        "id": 99,
        "product_id": 7,
        "lang": "en",
        "object_key": "videos/7.mp4",
        "filename": "7.mp4",
        "duration_seconds": 65,
        "file_size": 123,
    }

    monkeypatch.setattr(material_evaluation.medias, "get_product", lambda pid: product)
    monkeypatch.setattr(material_evaluation.medias, "resolve_cover", lambda pid, lang: "covers/7.jpg")
    monkeypatch.setattr(material_evaluation.medias, "list_items", lambda pid, lang=None: [video])
    monkeypatch.setattr(
        material_evaluation.pushes,
        "resolve_product_page_url",
        lambda lang, product: product["product_link"],
    )

    payload = material_evaluation.build_request_debug_payload(7, include_base64=False)

    video_entry = payload["media"][1]
    assert video_entry["role"] == "english_video"
    assert video_entry["preview_url"] == "/medias/api/products/7/evaluate/clip?media_item_id=99"
    assert video_entry["original_preview_url"] == "/medias/object?object_key=videos%2F7.mp4"
    assert video_entry["clip_seconds"] == 30
    assert video_entry["processing"] == {
        "policy_name": "short_clip_audio",
        "max_height": 480,
        "fps": 15,
        "video_bitrate": "600k",
        "maxrate": "800k",
        "bufsize": "1200k",
        "drop_audio": False,
        "audio_bitrate": "64k",
    }
    assert payload["request"]["media"][1]["processing"] == video_entry["processing"]


def test_build_request_debug_payload_uses_requested_media_item_and_30s_clip(monkeypatch, tmp_path):
    from appcore import material_evaluation

    cover_path = tmp_path / "cover.jpg"
    selected_video_path = tmp_path / "77_30s_llm.mp4"
    cover_path.write_bytes(b"cover")
    selected_video_path.write_bytes(b"selected-llm-video")
    product = {
        "id": 7,
        "name": "Debug Product",
        "product_code": "DP-7",
        "product_link": "https://example.test/products/debug",
        "user_id": 42,
        "cover_object_key": "covers/7.jpg",
    }
    selected_video = {
        "id": 77,
        "product_id": 7,
        "lang": "en",
        "object_key": "videos/selected.mp4",
        "filename": "selected.mp4",
        "duration_seconds": 62,
        "file_size": 456,
    }
    other_video = {
        "id": 99,
        "product_id": 7,
        "lang": "en",
        "object_key": "videos/first.mp4",
        "filename": "first.mp4",
        "duration_seconds": 30,
        "file_size": 123,
    }
    clip_calls = []

    monkeypatch.setattr(material_evaluation.medias, "get_product", lambda pid: product)
    monkeypatch.setattr(material_evaluation.medias, "get_item", lambda item_id: selected_video if item_id == 77 else None)
    monkeypatch.setattr(material_evaluation.medias, "resolve_cover", lambda pid, lang: "covers/7.jpg")
    monkeypatch.setattr(material_evaluation.medias, "list_items", lambda pid, lang=None: [other_video, selected_video])
    monkeypatch.setattr(material_evaluation.pushes, "resolve_product_page_url", lambda lang, product: product["product_link"])
    monkeypatch.setattr(material_evaluation, "_materialize_media", lambda object_key: cover_path)
    monkeypatch.setattr(
        material_evaluation,
        "_make_eval_clip_30s",
        lambda pid, item: clip_calls.append(item["id"]) or selected_video_path,
    )

    payload = material_evaluation.build_request_debug_payload(
        7,
        include_base64=True,
        media_item_id=77,
    )

    assert clip_calls == [77]
    assert payload["media"][1]["item_id"] == 77
    assert payload["media"][1]["filename"] == "selected.mp4"
    assert payload["request"]["media"][1]["data_base64"] == "c2VsZWN0ZWQtbGxtLXZpZGVv"


def test_build_request_debug_payload_prefers_mingkong_product_url_override(monkeypatch, tmp_path):
    from appcore import material_evaluation

    cover_path = tmp_path / "cover.jpg"
    cover_path.write_bytes(b"cover")
    product = {
        "id": 7,
        "name": "Debug Product",
        "product_code": "DP-7",
        "product_link": "https://our-shop.example/products/debug",
        "user_id": 42,
        "cover_object_key": "covers/7.jpg",
    }
    video = {
        "id": 99,
        "product_id": 7,
        "lang": "en",
        "object_key": "videos/7.mp4",
        "filename": "7.mp4",
        "duration_seconds": 30,
        "file_size": 123,
    }

    monkeypatch.setattr(material_evaluation.medias, "get_product", lambda pid: product)
    monkeypatch.setattr(material_evaluation.medias, "resolve_cover", lambda pid, lang: "covers/7.jpg")
    monkeypatch.setattr(material_evaluation.medias, "list_items", lambda pid, lang=None: [video])
    monkeypatch.setattr(
        material_evaluation.pushes,
        "resolve_product_page_url",
        lambda lang, product: "https://our-shop.example/products/debug",
    )
    monkeypatch.setattr(material_evaluation, "_materialize_media", lambda object_key: cover_path)

    payload = material_evaluation.build_request_debug_payload(
        7,
        product_url_override="https://mingkong.example/item/DP-7",
    )

    assert payload["product"]["product_url"] == "https://mingkong.example/item/DP-7"
    assert payload["request"]["prompt"].count("https://mingkong.example/item/DP-7") == 1
    assert "https://our-shop.example/products/debug" not in payload["request"]["prompt"]
