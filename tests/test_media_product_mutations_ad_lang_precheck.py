"""Tests for ad_supported_langs precheck on product update.

Spec: docs/superpowers/specs/2026-05-09-product-edit-ad-supported-langs-precheck-design.md

The service must:
- Compute "newly added" langs as `new_set - old_set`.
- Validate each newly-added lang has >=1 enabled domain AND every enabled
  domain is `ok=1` in `media_product_link_availability`.
- Skip the check entirely when no langs were added (old/unchanged/removed only).
- Return 422 with structured issues when any newly-added lang fails.
"""

from __future__ import annotations

import pytest


def _build(*, product, body, **overrides):
    """Helper: invoke build_product_update_response with sane DI defaults."""

    from web.services.media_product_mutations import build_product_update_response

    captured = {"updates": []}
    defaults = {
        "validate_product_code_fn": lambda code: (True, None),
        "get_product_by_code_fn": lambda code: None,
        "is_valid_language_fn": lambda code: code in {"de", "fr", "es", "ja", "it"},
        "update_product_fn": (
            lambda pid, **fields: captured["updates"].append((pid, fields))
        ),
        "replace_copywritings_fn": lambda *a, **kw: None,
        "schedule_material_evaluation_fn": None,
        "list_enabled_domain_rows_fn": lambda product, lang: [],
        "list_link_availability_fn": lambda pid, lang: [],
    }
    defaults.update(overrides)
    result = build_product_update_response(
        int(product["id"]),
        product,
        body,
        **defaults,
    )
    return result, captured


def test_skip_check_when_no_langs_added_old_lang_remains_broken():
    """Old lang already broken; user changes only product name → no precheck."""
    product = {"id": 1, "name": "Old", "ad_supported_langs": "de,fr"}
    enabled_called = []

    def list_enabled_domain_rows(product, lang):
        enabled_called.append(lang)
        return [{"domain": "newjoyloo.com", "url": "https://x"}]

    def list_link_availability(pid, lang):
        return [
            {
                "domain": "newjoyloo.com",
                "ok": False,
                "http_status": 404,
                "error": "http 404",
                "checked_at": "2026-01-01T00:00:00",
            }
        ]

    result, captured = _build(
        product=product,
        body={"name": "New Name", "ad_supported_langs": "de,fr"},
        list_enabled_domain_rows_fn=list_enabled_domain_rows,
        list_link_availability_fn=list_link_availability,
    )
    assert result.status_code == 200, result.payload
    assert enabled_called == [], "no lang added → precheck should be skipped"
    assert captured["updates"], "update_product_fn should still run"


def test_unchecking_lang_skips_precheck():
    """new ⊊ old → no langs added → skip check entirely."""
    product = {"id": 1, "name": "X", "ad_supported_langs": "de,fr,es"}
    called = []
    result, _ = _build(
        product=product,
        body={"ad_supported_langs": "de"},
        list_enabled_domain_rows_fn=lambda *a: called.append("e") or [],
        list_link_availability_fn=lambda *a: called.append("a") or [],
    )
    assert result.status_code == 200
    assert called == []


def test_added_lang_with_no_enabled_domain_is_blocked():
    product = {"id": 7, "ad_supported_langs": "de"}
    result, _ = _build(
        product=product,
        body={"ad_supported_langs": "de,fr"},
        list_enabled_domain_rows_fn=lambda product, lang: [] if lang == "fr" else [
            {"domain": "x.com", "url": "https://x.com"}
        ],
        list_link_availability_fn=lambda pid, lang: [
            {"domain": "x.com", "ok": True, "http_status": 200, "checked_at": "t"}
        ],
    )
    assert result.status_code == 422, result.payload
    assert result.payload["error"] == "ad_supported_langs_precheck_failed"
    issues = result.payload["issues"]
    assert any(i.get("lang") == "fr" and i.get("reason") == "no_enabled_domains" for i in issues)


def test_added_lang_with_all_ok_passes():
    product = {"id": 9, "ad_supported_langs": "de"}
    result, captured = _build(
        product=product,
        body={"ad_supported_langs": "de,fr"},
        list_enabled_domain_rows_fn=lambda product, lang: [
            {"domain": "newjoyloo.com", "url": f"https://newjoyloo.com/{lang}"},
            {"domain": "omurio.com", "url": f"https://omurio.com/{lang}"},
        ],
        list_link_availability_fn=lambda pid, lang: [
            {"domain": "newjoyloo.com", "ok": True, "http_status": 200, "checked_at": "t"},
            {"domain": "omurio.com", "ok": True, "http_status": 200, "checked_at": "t"},
        ],
    )
    assert result.status_code == 200, result.payload
    assert captured["updates"]


def test_added_lang_with_one_not_checked_is_blocked():
    product = {"id": 11, "ad_supported_langs": ""}
    result, _ = _build(
        product=product,
        body={"ad_supported_langs": "de"},
        list_enabled_domain_rows_fn=lambda product, lang: [
            {"domain": "newjoyloo.com", "url": "https://newjoyloo.com/de"},
            {"domain": "omurio.com", "url": "https://omurio.com/de"},
        ],
        list_link_availability_fn=lambda pid, lang: [
            {"domain": "newjoyloo.com", "ok": True, "http_status": 200, "checked_at": "t"},
            # omurio.com missing → not_checked
        ],
    )
    assert result.status_code == 422, result.payload
    issues = result.payload["issues"]
    de_issue = next(i for i in issues if i.get("lang") == "de")
    domains = de_issue["domains"]
    omurio = next(d for d in domains if d["domain"] == "omurio.com")
    assert omurio["reason"] == "not_checked"


def test_added_lang_with_one_failure_is_blocked_with_error_string():
    product = {"id": 12, "ad_supported_langs": ""}
    result, _ = _build(
        product=product,
        body={"ad_supported_langs": "de"},
        list_enabled_domain_rows_fn=lambda product, lang: [
            {"domain": "newjoyloo.com", "url": "https://newjoyloo.com/de"},
        ],
        list_link_availability_fn=lambda pid, lang: [
            {"domain": "newjoyloo.com", "ok": False, "http_status": 404, "error": "http 404", "checked_at": "t"},
        ],
    )
    assert result.status_code == 422
    issues = result.payload["issues"]
    de_issue = next(i for i in issues if i.get("lang") == "de")
    assert de_issue["domains"] == [{"domain": "newjoyloo.com", "reason": "http 404"}]


def test_added_lang_failure_falls_back_to_http_status_when_no_error_string():
    product = {"id": 13, "ad_supported_langs": ""}
    result, _ = _build(
        product=product,
        body={"ad_supported_langs": "de"},
        list_enabled_domain_rows_fn=lambda product, lang: [
            {"domain": "x.com", "url": "https://x.com"},
        ],
        list_link_availability_fn=lambda pid, lang: [
            {"domain": "x.com", "ok": False, "http_status": 500, "error": None, "checked_at": "t"},
        ],
    )
    assert result.status_code == 422
    de_issue = result.payload["issues"][0]
    assert de_issue["domains"] == [{"domain": "x.com", "reason": "http 500"}]


def test_multiple_added_langs_all_failing_are_aggregated():
    product = {"id": 14, "ad_supported_langs": ""}
    result, _ = _build(
        product=product,
        body={"ad_supported_langs": "de,fr,es"},
        list_enabled_domain_rows_fn=lambda product, lang: (
            []
            if lang == "es"
            else [{"domain": "x.com", "url": f"https://x.com/{lang}"}]
        ),
        list_link_availability_fn=lambda pid, lang: (
            [{"domain": "x.com", "ok": False, "http_status": 404, "error": "http 404", "checked_at": "t"}]
            if lang == "de"
            else []  # fr → not_checked
        ),
    )
    assert result.status_code == 422
    langs = {i["lang"] for i in result.payload["issues"]}
    assert langs == {"de", "fr", "es"}


def test_no_ad_supported_langs_in_body_skips_check():
    product = {"id": 15, "ad_supported_langs": "de,fr"}
    called = []
    result, _ = _build(
        product=product,
        body={"name": "rename only"},
        list_enabled_domain_rows_fn=lambda *a: called.append("e") or [],
        list_link_availability_fn=lambda *a: called.append("a") or [],
    )
    assert result.status_code == 200
    assert called == []


def test_precheck_skipped_when_unchanged():
    product = {"id": 16, "ad_supported_langs": "de,fr"}
    called = []
    result, _ = _build(
        product=product,
        body={"ad_supported_langs": "de,fr"},
        list_enabled_domain_rows_fn=lambda *a: called.append("e") or [],
        list_link_availability_fn=lambda *a: called.append("a") or [],
    )
    assert result.status_code == 200
    assert called == []


def test_added_lang_with_blank_checked_at_treated_as_not_checked():
    """Defensive: a row with checked_at='' or None counts as never-checked."""
    product = {"id": 17, "ad_supported_langs": ""}
    result, _ = _build(
        product=product,
        body={"ad_supported_langs": "de"},
        list_enabled_domain_rows_fn=lambda product, lang: [
            {"domain": "x.com", "url": "https://x.com"},
        ],
        list_link_availability_fn=lambda pid, lang: [
            {"domain": "x.com", "ok": False, "http_status": None, "error": None, "checked_at": ""},
        ],
    )
    assert result.status_code == 422
    de_issue = result.payload["issues"][0]
    assert de_issue["domains"][0]["reason"] == "not_checked"
