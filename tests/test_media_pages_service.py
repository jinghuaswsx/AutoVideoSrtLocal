from __future__ import annotations


def test_media_page_flask_response_returns_payload_and_status(authed_client_no_db):
    from web.services.media_pages import media_page_flask_response

    with authed_client_no_db.application.app_context():
        response, status_code = media_page_flask_response({"error": "forbidden"}, 403)

    assert status_code == 403
    assert response.get_json() == {"error": "forbidden"}


def test_build_medias_page_context_uses_query_then_extra_fallbacks():
    from web.services.media_pages import build_medias_page_context

    release = {"version": "1.0.0"}
    context = build_medias_page_context(
        {"q": "  demo-rjc  ", "keyword": "ignored"},
        {"initial_query": "fallback", "custom_flag": True},
        get_release_info_fn=lambda: release,
        get_rmb_per_usd_fn=lambda: "7.23",
    )

    assert context["shopify_image_localizer_release"] == release
    assert context["material_roas_rmb_per_usd"] == 7.23
    assert context["medias_initial_query"] == "demo-rjc"
    assert context["initial_query"] == "fallback"
    assert context["custom_flag"] is True


def test_build_medias_page_context_uses_keyword_and_extra_when_query_missing():
    from web.services.media_pages import build_medias_page_context

    keyword_context = build_medias_page_context(
        {"keyword": "  product-keyword  "},
        {},
        get_release_info_fn=lambda: {},
        get_rmb_per_usd_fn=lambda: 7,
    )
    extra_context = build_medias_page_context(
        {},
        {"initial_query": "  product-extra  "},
        get_release_info_fn=lambda: {},
        get_rmb_per_usd_fn=lambda: 7,
    )

    assert keyword_context["medias_initial_query"] == "product-keyword"
    assert extra_context["medias_initial_query"] == "product-extra"


def test_build_active_users_response_wraps_user_rows():
    from web.services.media_pages import build_active_users_response

    users = [{"id": 1, "username": "admin"}]

    assert build_active_users_response(list_active_users_fn=lambda: users) == {
        "users": users,
    }


def test_build_admin_required_response_is_standardized():
    from web.services.media_pages import build_admin_required_response

    result = build_admin_required_response()

    assert result == {"error": "\u4ec5\u7ba1\u7406\u5458\u53ef\u8bbf\u95ee"}


def test_build_languages_response_wraps_language_rows():
    from web.services.media_pages import build_languages_response

    languages = [{"code": "en", "name_zh": "English"}]

    assert build_languages_response(list_languages_fn=lambda: languages) == {
        "items": languages,
    }
