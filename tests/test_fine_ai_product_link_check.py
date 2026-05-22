def test_resolve_product_link_keeps_current_when_available():
    from web.services.fine_ai_product_link_check import resolve_product_link

    calls = []

    def fake_probe(url: str):
        calls.append(url)
        return {"ok": True, "http_status": 200, "error": None, "elapsed_ms": 3}

    result = resolve_product_link(
        current_link="https://shop.example/products/a",
        candidate_links=["https://shop.example/products/b"],
        probe_fn=fake_probe,
    )

    assert result["ok"] is True
    assert result["status"] == "ok"
    assert result["selected_link"] == "https://shop.example/products/a"
    assert calls == ["https://shop.example/products/a"]
    assert result["candidates"][0]["used"] is True


def test_resolve_product_link_uses_first_available_candidate():
    from web.services.fine_ai_product_link_check import resolve_product_link

    calls = []
    probes = {
        "https://shop.example/products/a": {
            "ok": False,
            "http_status": 404,
            "error": "http 404",
            "elapsed_ms": 4,
        },
        "https://shop.example/products/b": {
            "ok": True,
            "http_status": 200,
            "error": None,
            "elapsed_ms": 5,
        },
    }

    def fake_probe(url: str):
        calls.append(url)
        return probes[url]

    result = resolve_product_link(
        current_link="https://shop.example/products/a",
        candidate_links=[
            "https://shop.example/products/a",
            "https://shop.example/products/b",
        ],
        probe_fn=fake_probe,
    )

    assert result["ok"] is True
    assert result["status"] == "replaced"
    assert result["selected_link"] == "https://shop.example/products/b"
    assert calls == [
        "https://shop.example/products/a",
        "https://shop.example/products/b",
    ]
    assert [item["used"] for item in result["candidates"]] == [False, True]


def test_resolve_product_link_reports_failure_when_all_candidates_fail():
    from web.services.fine_ai_product_link_check import resolve_product_link

    result = resolve_product_link(
        current_link="https://shop.example/products/a",
        candidate_links=["https://shop.example/products/b"],
        probe_fn=lambda url: {
            "ok": False,
            "http_status": 500,
            "error": "http 500",
            "elapsed_ms": 8,
        },
    )

    assert result["ok"] is False
    assert result["status"] == "failed"
    assert result["selected_link"] == ""
    assert [item["url"] for item in result["candidates"]] == [
        "https://shop.example/products/a",
        "https://shop.example/products/b",
    ]
