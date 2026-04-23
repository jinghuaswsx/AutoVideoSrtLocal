import json


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
    assert normalized["listing_status"] == "上架"


def test_evaluate_ready_product_invokes_llm_and_updates_product(monkeypatch, tmp_path):
    from appcore import material_evaluation

    cover = tmp_path / "cover.jpg"
    video = tmp_path / "promo.mp4"
    cover.write_bytes(b"cover")
    video.write_bytes(b"video")
    updates = {}

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
        lambda *args, **kwargs: {
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
    assert updates["listing_status"] == "上架"
    detail = json.loads(updates["ai_evaluation_detail"])
    assert detail["product_url"] == "https://newjoyloo.com/products/neck-fan"
    assert detail["countries"][0]["lang"] == "de"


def test_medias_route_schedules_material_evaluation_in_background(monkeypatch):
    from web.routes import medias as route

    calls = []
    monkeypatch.setattr(
        route,
        "start_background_task",
        lambda fn, *args, **kwargs: calls.append((fn, args, kwargs)),
    )

    route._schedule_material_evaluation(7, force=True)

    assert calls == [
        (route.material_evaluation.evaluate_product_if_ready, (7,), {"force": True})
    ]
