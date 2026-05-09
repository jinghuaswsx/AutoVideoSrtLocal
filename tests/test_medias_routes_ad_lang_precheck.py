"""Route-level wiring test for ad_supported_langs precheck.

Spec: docs/superpowers/specs/2026-05-09-product-edit-ad-supported-langs-precheck-design.md

This complements the service-level tests in
test_media_product_mutations_ad_lang_precheck.py by verifying the
PUT /medias/api/products/<pid> endpoint correctly serializes the 422
ProductMutationResponse into a Flask JSON response.
"""

from __future__ import annotations


def test_put_product_returns_422_with_structured_issues_when_precheck_fails(
    authed_client_no_db, monkeypatch
):
    from web.routes import medias as r
    from web.services.media_product_mutations import ProductMutationResponse

    product = {"id": 42, "user_id": 1, "name": "Demo", "product_code": "demo-rjc"}

    monkeypatch.setattr(r.medias, "get_product", lambda pid: product)
    monkeypatch.setattr(r, "_can_access_product", lambda p: True)

    def fake_build(pid, product_arg, body):
        return ProductMutationResponse(
            {
                "error": "ad_supported_langs_precheck_failed",
                "issues": [
                    {
                        "lang": "de",
                        "domains": [{"domain": "newjoyloo.com", "reason": "http 404"}],
                    }
                ],
            },
            422,
        )

    monkeypatch.setattr(r, "_build_product_update_response", fake_build)

    resp = authed_client_no_db.put(
        "/medias/api/products/42",
        json={"ad_supported_langs": ["de"]},
    )

    assert resp.status_code == 422
    body = resp.get_json()
    assert body["error"] == "ad_supported_langs_precheck_failed"
    assert body["issues"] == [
        {"lang": "de", "domains": [{"domain": "newjoyloo.com", "reason": "http 404"}]}
    ]


def test_put_product_returns_422_with_no_enabled_domains_reason(
    authed_client_no_db, monkeypatch
):
    from web.routes import medias as r
    from web.services.media_product_mutations import ProductMutationResponse

    product = {"id": 42, "user_id": 1, "name": "Demo", "product_code": "demo-rjc"}
    monkeypatch.setattr(r.medias, "get_product", lambda pid: product)
    monkeypatch.setattr(r, "_can_access_product", lambda p: True)
    monkeypatch.setattr(
        r,
        "_build_product_update_response",
        lambda pid, p, body: ProductMutationResponse(
            {
                "error": "ad_supported_langs_precheck_failed",
                "issues": [{"lang": "fr", "reason": "no_enabled_domains"}],
            },
            422,
        ),
    )

    resp = authed_client_no_db.put(
        "/medias/api/products/42",
        json={"ad_supported_langs": ["fr"]},
    )

    assert resp.status_code == 422
    body = resp.get_json()
    assert body["error"] == "ad_supported_langs_precheck_failed"
    assert body["issues"][0]["reason"] == "no_enabled_domains"
