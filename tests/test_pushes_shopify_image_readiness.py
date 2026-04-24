from appcore import pushes


def test_compute_readiness_blocks_non_english_until_shopify_images_confirmed(monkeypatch):
    monkeypatch.setattr(pushes.medias, "is_product_listed", lambda product: True)
    monkeypatch.setattr(pushes.medias, "parse_ad_supported_langs", lambda value: ["it"])
    monkeypatch.setattr(pushes, "_has_valid_en_push_texts", lambda product_id: True)
    monkeypatch.setattr(pushes, "query_one", lambda sql, args=(): {"ok": 1})
    monkeypatch.setattr(
        pushes.shopify_image_tasks,
        "is_confirmed_for_push",
        lambda product, lang: (False, "图片已自动替换，等待人工确认"),
    )

    readiness = pushes.compute_readiness(
        {
            "id": 1,
            "product_id": 7,
            "lang": "it",
            "object_key": "video.mp4",
            "cover_object_key": "cover.jpg",
        },
        {"id": 7, "ad_supported_langs": "it", "listing_status": "上架"},
    )

    assert readiness["shopify_image_confirmed"] is False
    assert readiness["shopify_image_reason"] == "图片已自动替换，等待人工确认"
    assert pushes.is_ready(readiness) is False


def test_compute_readiness_allows_english_without_shopify_gate(monkeypatch):
    monkeypatch.setattr(pushes.medias, "is_product_listed", lambda product: True)
    monkeypatch.setattr(pushes.medias, "parse_ad_supported_langs", lambda value: ["it"])
    monkeypatch.setattr(pushes, "_has_valid_en_push_texts", lambda product_id: True)
    monkeypatch.setattr(pushes, "query_one", lambda sql, args=(): {"ok": 1})

    readiness = pushes.compute_readiness(
        {
            "id": 1,
            "product_id": 7,
            "lang": "en",
            "object_key": "video.mp4",
            "cover_object_key": "cover.jpg",
        },
        {"id": 7, "ad_supported_langs": "it", "listing_status": "上架"},
    )

    assert readiness["shopify_image_confirmed"] is True
