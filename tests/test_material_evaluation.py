import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def _default_product_link_probe_ok(monkeypatch):
    from appcore import material_evaluation

    monkeypatch.setattr(material_evaluation.pushes, "probe_ad_url", lambda url: (True, None))


def test_response_schema_requires_every_enabled_small_language():
    from appcore import material_evaluation

    languages = [
        {"code": "de", "name": "德语"},
        {"code": "fr", "name": "法语"},
        {"code": "es", "name": "西班牙语"},
    ]

    schema = material_evaluation.build_response_schema(languages)

    countries = schema["properties"]["countries"]
    item_props = countries["items"]["properties"]
    assert countries["minItems"] == 3
    assert countries["maxItems"] == 3
    assert item_props["lang"]["enum"] == ["de", "fr", "es"]
    assert item_props["reason"]["maxLength"] == 100


def test_prompt_mentions_europe_small_languages_and_input_assets():
    from appcore import material_evaluation

    prompt = material_evaluation.build_prompt(
        product={"id": 7, "name": "Portable Neck Fan", "product_code": "neck-fan"},
        product_url="https://newjoyloo.com/products/neck-fan",
        languages=[{"code": "de", "name": "德语"}, {"code": "fr", "name": "法语"}],
    )

    assert "欧洲市场" in prompt
    assert "小语种国家" in prompt
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
    assert updates["ai_score"] == 88.0
    assert updates["ai_evaluation_result"] == "适合推广"
    assert "listing_status" not in updates
    assert "listing_status" not in result
    assert llm_calls[0][1]["provider_override"] == "gemini_vertex_adc"
    assert llm_calls[0][1]["model_override"] == "gemini-3.1-pro-preview"
    assert llm_calls[0][1]["google_search"] is True
    detail = json.loads(updates["ai_evaluation_detail"])
    assert detail["product_url"] == "https://newjoyloo.com/products/neck-fan"
    assert detail["provider"] == "gemini_vertex_adc"
    assert detail["model"] == "gemini-3.1-pro-preview"
    assert detail["search_tools"] == [{"google_search": {}}]
    assert detail["countries"][0]["lang"] == "de"


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
            "model": "gemini-3.1-pro-preview",
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
    assert llm_calls[0][1]["model_override"] == "gemini-3.1-pro-preview"
    assert llm_calls[0][1]["google_search"] is True
    assert llm_calls[0][1]["billing_extra"]["tools"] == [{"google_search": {}}]
    detail = json.loads(updates["ai_evaluation_detail"])
    assert detail["provider"] == "gemini_aistudio"
    assert detail["model"] == "gemini-3.1-pro-preview"
    assert detail["search_tools"] == [{"google_search": {}}]


@pytest.mark.parametrize("provider", ["gemini_vertex", "gemini_vertex_adc"])
def test_resolve_evaluation_llm_config_keeps_vertex_bindings(monkeypatch, provider):
    from appcore import material_evaluation

    monkeypatch.setattr(
        material_evaluation,
        "llm_bindings",
        SimpleNamespace(resolve=lambda code: {
            "provider": provider,
            "model": "google/gemini-3.1-pro-preview",
        }),
        raising=False,
    )

    config = material_evaluation.resolve_evaluation_llm_config()

    assert config["provider"] == provider
    assert config["model"] == "gemini-3.1-pro-preview"
    assert config["search_tools"] == [{"google_search": {}}]


def test_evaluate_ready_product_sends_15s_clip_to_llm(monkeypatch, tmp_path):
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
                "duration_seconds": 30.0,
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
    assert ffmpeg_calls[0][ffmpeg_calls[0].index("-t") + 1] == "15"
    media = llm_calls[0][1]["media"]
    assert media[0] == cover
    assert str(media[1]).endswith("11_15s.mp4")
    assert Path(media[1]).read_bytes() == b"clip"


def test_make_eval_clip_15s_returns_original_for_short_video(monkeypatch, tmp_path):
    from appcore import material_evaluation

    video = tmp_path / "short.mp4"
    video.write_bytes(b"video")

    monkeypatch.setattr(material_evaluation, "_materialize_media", lambda object_key: video)

    def fail_if_ffmpeg_runs(*args, **kwargs):
        raise AssertionError("ffmpeg should not run for videos within 15 seconds")

    monkeypatch.setattr("subprocess.run", fail_if_ffmpeg_runs)

    result = material_evaluation._make_eval_clip_15s(
        7,
        {"id": 11, "object_key": "media/short.mp4", "duration_seconds": 14.9},
    )

    assert result == video


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
        "_make_eval_clip_15s",
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
