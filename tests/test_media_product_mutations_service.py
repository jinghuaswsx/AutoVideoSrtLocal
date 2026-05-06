from __future__ import annotations


def test_build_product_create_response_creates_with_normalized_code():
    from web.services.media_product_mutations import build_product_create_response

    captured = {}

    def validate(code):
        captured["validated_code"] = code
        return True, None

    result = build_product_create_response(
        {"name": "  Demo Product  ", "product_code": "DEMO-RJC"},
        user_id=5,
        validate_product_code_fn=validate,
        get_product_by_code_fn=lambda code: None,
        create_product_fn=lambda user_id, name, **kwargs: captured.update(
            {"user_id": user_id, "name": name, "kwargs": kwargs}
        )
        or 123,
    )

    assert result.status_code == 201
    assert result.payload == {"id": 123}
    assert captured == {
        "validated_code": "demo-rjc",
        "user_id": 5,
        "name": "Demo Product",
        "kwargs": {"product_code": "demo-rjc"},
    }


def test_build_product_create_response_rejects_blank_name_before_lookup():
    from web.services.media_product_mutations import build_product_create_response

    called = []

    result = build_product_create_response(
        {"name": "   ", "product_code": "demo-rjc"},
        user_id=5,
        validate_product_code_fn=lambda code: called.append(("validate", code)) or (True, None),
        get_product_by_code_fn=lambda code: called.append(("lookup", code)),
        create_product_fn=lambda *args, **kwargs: called.append(("create", args, kwargs)),
    )

    assert result.status_code == 400
    assert result.payload == {"error": "name required"}
    assert called == []


def test_build_product_create_response_rejects_duplicate_code():
    from web.services.media_product_mutations import build_product_create_response

    called = []

    result = build_product_create_response(
        {"name": "Demo", "product_code": "demo-rjc"},
        user_id=5,
        validate_product_code_fn=lambda code: (True, None),
        get_product_by_code_fn=lambda code: {"id": 9},
        create_product_fn=lambda *args, **kwargs: called.append(("create", args, kwargs)),
    )

    assert result.status_code == 409
    assert result.payload == {"error": "product_code already exists"}
    assert called == []


def test_build_product_update_response_updates_fields_copywriting_and_evaluation():
    from web.services.media_product_mutations import build_product_update_response

    captured = {"updates": [], "copywritings": [], "evaluations": []}
    product = {"id": 42, "name": "Old Name", "product_code": "old-rjc"}
    body = {
        "name": "  New Name  ",
        "product_code": "NEW-RJC",
        "localized_links": {"de": " https://example.test/de ", "xx": "ignored"},
        "ad_supported_langs": ["en", "de", "de", "fr", "xx"],
        "copywritings": {"de": [{"title": "T"}], "xx": [{"title": "skip"}]},
    }

    result = build_product_update_response(
        42,
        product,
        body,
        validate_product_code_fn=lambda code: (True, None),
        get_product_by_code_fn=lambda code: None,
        is_valid_language_fn=lambda code: code in {"de", "fr"},
        update_product_fn=lambda pid, **fields: captured["updates"].append((pid, fields)),
        replace_copywritings_fn=lambda pid, items, lang=None: captured["copywritings"].append(
            (pid, items, lang)
        ),
        schedule_material_evaluation_fn=lambda pid, **kwargs: captured["evaluations"].append(
            (pid, kwargs)
        ),
    )

    assert result.status_code == 200
    assert result.payload == {"ok": True}
    assert captured["updates"] == [
        (
            42,
            {
                "name": "New Name",
                "product_code": "new-rjc",
                "localized_links_json": {"de": "https://example.test/de"},
                "ad_supported_langs": "de,fr",
            },
        )
    ]
    assert captured["copywritings"] == [(42, [{"title": "T"}], "de")]
    assert captured["evaluations"] == [(42, {"force": True})]


def test_build_product_update_response_maps_duplicate_mk_id_to_conflict():
    import pymysql.err

    from web.services.media_product_mutations import build_product_update_response

    def raise_duplicate(_pid, **_fields):
        raise pymysql.err.IntegrityError(1062, "Duplicate entry for uk_media_products_mk_id")

    result = build_product_update_response(
        42,
        {"id": 42, "name": "Old"},
        {"mk_id": "MK-1"},
        update_product_fn=raise_duplicate,
    )

    assert result.status_code == 409
    assert result.payload["error"] == "mk_id_conflict"


def test_build_product_update_response_maps_invalid_field_to_bad_request():
    from web.services.media_product_mutations import build_product_update_response

    def raise_invalid(_pid, **_fields):
        raise ValueError("shopifyid must be numeric")

    result = build_product_update_response(
        42,
        {"id": 42, "name": "Old"},
        {"shopifyid": "abc"},
        update_product_fn=raise_invalid,
    )

    assert result.status_code == 400
    assert result.payload == {
        "error": "invalid_product_field",
        "message": "shopifyid must be numeric",
    }


def test_build_product_delete_response_soft_deletes_product():
    from web.services.media_product_mutations import build_product_delete_response

    deleted = []

    result = build_product_delete_response(
        42,
        soft_delete_product_fn=lambda pid: deleted.append(pid),
    )

    assert result.status_code == 200
    assert result.payload == {"ok": True}
    assert deleted == [42]


def test_product_mutation_flask_response_returns_payload_and_status(
    authed_client_no_db,
):
    from web.services.media_product_mutations import (
        ProductMutationResponse,
        product_mutation_flask_response,
    )

    with authed_client_no_db.application.app_context():
        response, status_code = product_mutation_flask_response(
            ProductMutationResponse({"ok": True}, 202)
        )

    assert status_code == 202
    assert response.get_json() == {"ok": True}
