"""Tests for list-for-link-check route in medias app."""
from __future__ import annotations

import pytest


def test_list_for_link_check_requires_login(authed_client_no_db):
    """Sanity: when not authenticated, requests redirect to login."""
    raw = authed_client_no_db.application.test_client()
    response = raw.get("/medias/api/products/list-for-link-check?created_from=2026-06-01&created_to=2026-06-07")
    assert response.status_code in (302, 401)


def test_list_for_link_check_missing_params(authed_client_no_db):
    response = authed_client_no_db.get("/medias/api/products/list-for-link-check")
    assert response.status_code == 400


def test_list_for_link_check_success(authed_client_no_db, monkeypatch):
    mock_products = [
        {
            "id": 100,
            "product_code": "p1-rjc",
            "name": "Product 1",
            "localized_links_json": "{}",
            "localized_links": None
        }
    ]

    mock_urls = [
        {"domain": "newjoyloo.com", "url": "https://newjoyloo.com/products/p1-rjc"}
    ]

    # Mock database query
    monkeypatch.setattr(
        "appcore.db.query",
        lambda sql, args: mock_products
    )
    # Mock url resolution
    monkeypatch.setattr(
        "appcore.product_link_domains.resolve_product_page_url_rows",
        lambda product, lang: mock_urls if lang == "en" else []
    )

    response = authed_client_no_db.get(
        "/medias/api/products/list-for-link-check?created_from=2026-06-06&created_to=2026-06-12"
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert "products" in payload
    assert len(payload["products"]) == 1
    p = payload["products"][0]
    assert p["id"] == 100
    assert p["product_code"] == "p1-rjc"
    assert p["name"] == "Product 1"
    assert p["urls"] == mock_urls
