from __future__ import annotations


def test_shopify_image_flask_response_returns_payload_and_status(authed_client_no_db):
    from web.services.media_shopify_image import (
        MediaShopifyImageResponse,
        shopify_image_flask_response,
    )

    result = MediaShopifyImageResponse({"ok": False, "task": {"status": "blocked"}}, 409)

    with authed_client_no_db.application.app_context():
        response, status_code = shopify_image_flask_response(result)

    assert status_code == 409
    assert response.get_json() == {"ok": False, "task": {"status": "blocked"}}


def test_normalize_shopify_image_lang_rejects_empty_en_and_unknown(monkeypatch):
    from web.services import media_shopify_image

    monkeypatch.setattr(media_shopify_image.medias, "is_valid_language", lambda code: code == "it")

    assert media_shopify_image.normalize_shopify_image_lang(" IT ") == "it"
    assert media_shopify_image.normalize_shopify_image_lang("") is None
    assert media_shopify_image.normalize_shopify_image_lang("en") is None
    assert media_shopify_image.normalize_shopify_image_lang("xx") is None


def test_shopify_image_confirm_response_delegates_to_task_service(monkeypatch):
    from web.services import media_shopify_image

    monkeypatch.setattr(
        media_shopify_image.shopify_image_tasks,
        "confirm_lang",
        lambda pid, lang, user_id: {
            "replace_status": "confirmed",
            "link_status": "normal",
            "confirmed_by": user_id,
            "pid": pid,
            "lang": lang,
        },
    )

    result = media_shopify_image.build_shopify_image_confirm_response(
        product_id=7,
        lang="it",
        user_id=2,
    )

    assert result.status_code == 200
    assert result.payload["ok"] is True
    assert result.payload["status"]["confirmed_by"] == 2
    assert result.payload["status"]["pid"] == 7
    assert result.payload["status"]["lang"] == "it"


def test_shopify_image_unavailable_response_trims_reason(monkeypatch):
    from web.services import media_shopify_image

    captured = {}
    monkeypatch.setattr(
        media_shopify_image.shopify_image_tasks,
        "mark_link_unavailable",
        lambda pid, lang, reason: captured.setdefault(
            "status",
            {"pid": pid, "lang": lang, "reason": reason},
        ),
    )

    result = media_shopify_image.build_shopify_image_unavailable_response(
        product_id=7,
        lang="it",
        body={"reason": "  missing localized page  "},
    )

    assert result.status_code == 200
    assert result.payload == {"ok": True, "status": captured["status"]}
    assert captured["status"]["reason"] == "missing localized page"


def test_shopify_image_requeue_response_maps_blocked_status(monkeypatch):
    from web.services import media_shopify_image

    calls = []
    monkeypatch.setattr(
        media_shopify_image.shopify_image_tasks,
        "reset_lang",
        lambda pid, lang: calls.append(("reset", pid, lang)),
    )
    monkeypatch.setattr(
        media_shopify_image.shopify_image_tasks,
        "create_or_reuse_task",
        lambda pid, lang: {"id": 44, "status": media_shopify_image.shopify_image_tasks.TASK_BLOCKED},
    )

    result = media_shopify_image.build_shopify_image_requeue_response(
        product_id=7,
        lang="it",
    )

    assert result.status_code == 409
    assert result.payload == {"ok": False, "task": {"id": 44, "status": "blocked"}}
    assert calls == [("reset", 7, "it")]
