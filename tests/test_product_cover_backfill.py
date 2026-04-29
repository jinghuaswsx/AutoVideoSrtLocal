from __future__ import annotations


def test_find_missing_cover_products_returns_rows_without_en_cover(monkeypatch):
    from appcore import product_cover_backfill as pcb

    captured = {}

    def fake_query(sql, args):
        captured["sql"] = sql
        captured["args"] = args
        return [{"id": 7, "user_id": 3, "product_code": "demo-rjc"}]

    monkeypatch.setattr(pcb, "query", fake_query)

    rows = pcb.find_missing_cover_products()

    assert rows == [{"id": 7, "user_id": 3, "product_code": "demo-rjc"}]
    assert "media_product_covers" in captured["sql"]
    assert "c.lang='en'" in captured["sql"]
    assert "c.object_key IS NULL" in captured["sql"]
    assert captured["args"] == ()


def test_pick_first_carousel_image_prefers_first_valid_url():
    from appcore import product_cover_backfill as pcb

    items = [
        {"source_url": "data:image/svg+xml;base64,abc"},
        {"source_url": "https://cdn.example.com/hero.jpg?width=800"},
        {"source_url": "https://cdn.example.com/second.jpg"},
    ]

    assert pcb.pick_first_carousel_image(items) == "https://cdn.example.com/hero.jpg?width=800"


def test_backfill_product_cover_downloads_first_carousel_image_and_sets_en_cover(monkeypatch):
    from appcore import product_cover_backfill as pcb

    product = {
        "id": 42,
        "user_id": 9,
        "product_code": "clip-in-style-magnetic-clothing-clips-rjc",
    }
    calls = {}

    monkeypatch.setattr(
        pcb.pushes,
        "resolve_product_page_url",
        lambda lang, row: f"https://newjoyloo.com/products/{row['product_code']}",
    )
    monkeypatch.setattr(
        pcb,
        "fetch_carousel_images",
        lambda url: [
            {"source_url": "https://cdn.example.com/cover.jpg?width=1200"},
            {"source_url": "https://cdn.example.com/other.jpg"},
        ],
    )
    def fake_download(url, product_id, user_id):
        calls["download"] = (url, product_id, user_id)
        return "9/medias/42/cover.jpg"

    monkeypatch.setattr(pcb, "download_image_to_media_storage", fake_download)

    def fake_set_cover(product_id, lang, object_key):
        calls["cover"] = (product_id, lang, object_key)

    monkeypatch.setattr(pcb.medias, "set_product_cover", fake_set_cover)

    result = pcb.backfill_product_cover(product)

    assert result["status"] == "backfilled"
    assert calls["download"] == ("https://cdn.example.com/cover.jpg?width=1200", 42, 9)
    assert calls["cover"] == (42, "en", "9/medias/42/cover.jpg")


def test_backfill_all_missing_covers_processes_every_missing_product(monkeypatch):
    from appcore import product_cover_backfill as pcb

    products = [{"id": 1}, {"id": 2}, {"id": 3}]
    processed = []
    monkeypatch.setattr(pcb, "find_missing_cover_products", lambda: products)
    monkeypatch.setattr(
        pcb,
        "backfill_product_cover",
        lambda product: processed.append(product["id"]) or {"status": "backfilled"},
    )

    result = pcb.backfill_all_missing_covers()

    assert processed == [1, 2, 3]
    assert result == {"total": 3, "backfilled": 3, "failed": 0, "skipped": 0}
