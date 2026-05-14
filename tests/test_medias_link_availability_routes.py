"""Routes + service builder tests for product link availability."""
from __future__ import annotations

import pytest


@pytest.fixture
def stub_medias(monkeypatch):
    """Patch shared appcore.medias / product_link_domains entry points.

    Returns a dict that tests can mutate to reshape the fake product / domain rows.
    """
    state = {
        "product": {
            "id": 7,
            "product_code": "demo-rjc",
            "name": "Demo Product",
            "_owner_user_id": 1,
        },
        "rows": [
            {"domain": "newjoyloo.com", "url": "https://newjoyloo.com/de/products/demo-rjc", "lang": "de", "status_key": "newjoyloo.com:de"},
            {"domain": "omurio.com", "url": "https://omurio.com/de/products/demo-rjc", "lang": "de", "status_key": "omurio.com:de"},
        ],
        "lang_valid": True,
        "cached_results": [],
        "probe_calls": [],
    }

    monkeypatch.setattr("appcore.medias.get_product", lambda pid: state["product"] if int(pid) == 7 else None)
    monkeypatch.setattr("appcore.medias.is_valid_language", lambda code: bool(state["lang_valid"]))
    monkeypatch.setattr(
        "appcore.product_link_domains.resolve_product_page_url_rows",
        lambda product, lang: state["rows"],
    )
    monkeypatch.setattr(
        "web.routes.medias._helpers._can_access_product",
        lambda product: bool(product),
    )
    return state


def test_link_availability_routes_require_login(authed_client_no_db, stub_medias):
    """Sanity: when not authenticated, requests redirect to login."""
    raw = authed_client_no_db.application.test_client()
    response = raw.get("/medias/api/products/7/link-availability/de")
    assert response.status_code in (302, 401)


def test_link_availability_get_returns_enabled_domain_rows(authed_client_no_db, stub_medias, monkeypatch):
    monkeypatch.setattr(
        "appcore.link_availability.list_results",
        lambda pid, lang: [
            {
                "product_id": 7,
                "lang": "de",
                "domain": "newjoyloo.com",
                "link_url": "https://newjoyloo.com/de/products/demo-rjc",
                "http_status": 200,
                "ok": True,
                "error": None,
                "elapsed_ms": 230,
                "checked_at": "2026-05-09T08:00:00",
            }
        ],
    )

    response = authed_client_no_db.get("/medias/api/products/7/link-availability/de")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["product_id"] == 7
    assert payload["lang"] == "de"
    assert [item["domain"] for item in payload["items"]] == ["newjoyloo.com", "omurio.com"]
    assert payload["items"][0]["http_status"] == 200
    assert payload["items"][0]["ok"] is True
    # Domain present in product_link_domains but missing from cache → empty placeholder.
    assert payload["items"][1]["http_status"] is None
    assert payload["items"][1]["checked_at"] == ""


def test_link_availability_get_surfaces_stale_disabled_domain(authed_client_no_db, stub_medias, monkeypatch):
    monkeypatch.setattr(
        "appcore.link_availability.list_results",
        lambda pid, lang: [
            {
                "product_id": 7,
                "lang": "de",
                "domain": "old-domain.com",
                "link_url": "https://old-domain.com/de/products/demo-rjc",
                "http_status": 200,
                "ok": True,
                "error": None,
                "elapsed_ms": 1,
                "checked_at": "2026-04-01T00:00:00",
            }
        ],
    )
    response = authed_client_no_db.get("/medias/api/products/7/link-availability/de")
    payload = response.get_json()
    domains = [item["domain"] for item in payload["items"]]
    assert "old-domain.com" in domains
    stale_item = next(item for item in payload["items"] if item["domain"] == "old-domain.com")
    assert stale_item.get("stale") is True


def test_link_availability_get_returns_400_for_invalid_lang(authed_client_no_db, stub_medias):
    stub_medias["lang_valid"] = False
    response = authed_client_no_db.get("/medias/api/products/7/link-availability/zz")
    assert response.status_code == 400
    assert "不支持的语言" in response.get_json()["error"]


def test_link_availability_get_404_when_product_missing(authed_client_no_db, stub_medias, monkeypatch):
    stub_medias["product"] = None
    response = authed_client_no_db.get("/medias/api/products/7/link-availability/de")
    assert response.status_code == 404


def test_link_availability_post_runs_probe_for_all_enabled_domains(authed_client_no_db, stub_medias, monkeypatch):
    captured: dict = {}

    def fake_probe_and_record(*, product_id, lang, rows, **kwargs):
        captured["product_id"] = product_id
        captured["lang"] = lang
        captured["rows"] = list(rows)
        return [
            {
                "product_id": product_id,
                "lang": lang,
                "domain": row["domain"],
                "link_url": row["url"],
                "http_status": 200,
                "ok": True,
                "error": None,
                "elapsed_ms": 100,
                "checked_at": "2026-05-09T09:00:00",
            }
            for row in rows
        ]

    monkeypatch.setattr("appcore.link_availability.probe_and_record", fake_probe_and_record)

    response = authed_client_no_db.post(
        "/medias/api/products/7/link-availability/de",
        json={},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert [item["domain"] for item in payload["items"]] == ["newjoyloo.com", "omurio.com"]
    assert all(item["ok"] for item in payload["items"])
    assert captured["product_id"] == 7
    assert captured["lang"] == "de"
    assert {row["domain"] for row in captured["rows"]} == {"newjoyloo.com", "omurio.com"}


def test_link_availability_post_supports_single_domain_filter(authed_client_no_db, stub_medias, monkeypatch):
    captured: dict = {}

    def fake_probe_and_record(*, product_id, lang, rows, **kwargs):
        captured["rows"] = list(rows)
        return [
            {
                "product_id": product_id,
                "lang": lang,
                "domain": row["domain"],
                "link_url": row["url"],
                "http_status": 404,
                "ok": False,
                "error": "http 404",
                "elapsed_ms": 80,
                "checked_at": "2026-05-09T09:30:00",
            }
            for row in rows
        ]

    monkeypatch.setattr("appcore.link_availability.probe_and_record", fake_probe_and_record)
    monkeypatch.setattr(
        "appcore.link_availability.list_results",
        lambda pid, lang: [
            {
                "product_id": pid,
                "lang": lang,
                "domain": "newjoyloo.com",
                "link_url": "https://newjoyloo.com/de/products/demo-rjc",
                "http_status": 200,
                "ok": True,
                "error": None,
                "elapsed_ms": 100,
                "checked_at": "2026-05-09T08:00:00",
            }
        ],
    )

    response = authed_client_no_db.post(
        "/medias/api/products/7/link-availability/de",
        json={"domain": "omurio.com"},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert {row["domain"] for row in captured["rows"]} == {"omurio.com"}
    domain_results = {item["domain"]: item for item in payload["items"]}
    assert domain_results["newjoyloo.com"]["ok"] is True  # untouched cached row
    assert domain_results["omurio.com"]["ok"] is False
    assert domain_results["omurio.com"]["http_status"] == 404


def test_link_availability_post_manual_confirm_marks_only_requested_domain(
    authed_client_no_db,
    stub_medias,
    monkeypatch,
):
    calls: list[tuple] = []

    def manual_confirm(*, product_id, lang, domain, link_url):
        calls.append((product_id, lang, domain, link_url))

    monkeypatch.setattr("appcore.link_availability.manual_confirm_result", manual_confirm)
    monkeypatch.setattr(
        "appcore.link_availability.probe_and_record",
        lambda *args, **kwargs: pytest.fail("manual confirm should not run HTTP probe"),
    )
    monkeypatch.setattr(
        "appcore.link_availability.list_results",
        lambda pid, lang: [
            {
                "product_id": pid,
                "lang": lang,
                "domain": "newjoyloo.com",
                "link_url": "https://newjoyloo.com/de/products/demo-rjc",
                "http_status": None,
                "ok": False,
                "error": "timeout",
                "elapsed_ms": 5000,
                "checked_at": "2026-05-14T10:00:00",
            },
            {
                "product_id": pid,
                "lang": lang,
                "domain": "omurio.com",
                "link_url": "https://omurio.com/de/products/demo-rjc",
                "http_status": 200,
                "ok": True,
                "error": "manual_confirmed",
                "elapsed_ms": 0,
                "checked_at": "2026-05-14T10:01:00",
            },
        ],
    )

    response = authed_client_no_db.post(
        "/medias/api/products/7/link-availability/de",
        json={"domain": "omurio.com", "manual_confirm": True},
    )

    assert response.status_code == 200
    assert calls == [
        (
            7,
            "de",
            "omurio.com",
            "https://omurio.com/de/products/demo-rjc",
        )
    ]
    payload = response.get_json()
    assert [item["domain"] for item in payload["items"]] == ["newjoyloo.com", "omurio.com"]
    assert payload["items"][1]["ok"] is True
    assert payload["items"][1]["error"] == "manual_confirmed"


def test_link_availability_post_invalid_domain_returns_400(authed_client_no_db, stub_medias):
    response = authed_client_no_db.post(
        "/medias/api/products/7/link-availability/de",
        json={"domain": "!!!"},
    )
    assert response.status_code == 400
    assert response.get_json()["error"] == "invalid domain"


def test_link_availability_post_unknown_domain_returns_404(authed_client_no_db, stub_medias):
    response = authed_client_no_db.post(
        "/medias/api/products/7/link-availability/de",
        json={"domain": "nope.com"},
    )
    assert response.status_code == 404
    assert response.get_json()["error"] == "domain not enabled for product"


def test_link_availability_post_empty_when_no_domains(authed_client_no_db, stub_medias, monkeypatch):
    stub_medias["rows"] = []
    monkeypatch.setattr(
        "appcore.link_availability.probe_and_record",
        lambda *args, **kwargs: pytest.fail("should not call probe when no targets"),
    )

    response = authed_client_no_db.post(
        "/medias/api/products/7/link-availability/de",
        json={},
    )
    assert response.status_code == 200
    assert response.get_json()["items"] == []
