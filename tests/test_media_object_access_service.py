from __future__ import annotations


def test_validate_private_media_object_access_accepts_safe_key():
    from web.services.media_object_access import validate_private_media_object_access

    calls = []

    result = validate_private_media_object_access(
        " 1/medias/123/demo.mp4 ",
        safe_local_path_for_fn=lambda key: calls.append(key) or f"/safe/{key}",
    )

    assert calls == ["1/medias/123/demo.mp4"]
    assert result.ok is True
    assert result.object_key == "1/medias/123/demo.mp4"
    assert result.not_found is False


def test_validate_private_media_object_access_rejects_blank_without_storage_call():
    from web.services.media_object_access import validate_private_media_object_access

    calls = []

    result = validate_private_media_object_access(
        "   ",
        safe_local_path_for_fn=lambda key: calls.append(key),
    )

    assert calls == []
    assert result.ok is False
    assert result.not_found is True


def test_validate_private_media_object_access_rejects_path_escape():
    from web.services.media_object_access import validate_private_media_object_access

    result = validate_private_media_object_access(
        "../outside.mp4",
        safe_local_path_for_fn=lambda key: (_ for _ in ()).throw(ValueError("escape")),
    )

    assert result.ok is False
    assert result.not_found is True


def test_build_private_media_object_proxy_response_returns_object_and_audit_item():
    from web.services.media_object_access import (
        MediaObjectAccess,
        build_private_media_object_proxy_response,
    )

    calls = []

    result = build_private_media_object_proxy_response(
        " 1/medias/123/demo.mp4 ",
        validate_access_fn=lambda key: calls.append(("validate", key))
        or MediaObjectAccess(True, object_key="1/medias/123/demo.mp4"),
        find_item_by_object_key_fn=lambda key: calls.append(("find", key))
        or {"id": 44, "object_key": key},
    )

    assert result.status_code == 200
    assert result.not_found is False
    assert result.object_key == "1/medias/123/demo.mp4"
    assert result.audit_item == {"id": 44, "object_key": "1/medias/123/demo.mp4"}
    assert calls == [
        ("validate", " 1/medias/123/demo.mp4 "),
        ("find", "1/medias/123/demo.mp4"),
    ]


def test_build_private_media_object_proxy_response_skips_audit_lookup_when_not_found():
    from web.services.media_object_access import (
        MediaObjectAccess,
        build_private_media_object_proxy_response,
    )

    calls = []

    result = build_private_media_object_proxy_response(
        "../outside.mp4",
        validate_access_fn=lambda key: MediaObjectAccess(False, not_found=True),
        find_item_by_object_key_fn=lambda key: calls.append(key),
    )

    assert result.status_code == 404
    assert result.not_found is True
    assert result.object_key is None
    assert result.audit_item is None
    assert calls == []


def test_validate_public_media_object_access_accepts_allowed_namespaces():
    from web.services.media_object_access import validate_public_media_object_access

    for key in (
        "1/medias/123/demo.mp4",
        "artifacts/task/1/file.jpg",
        "uploads/task/1/source.mp4",
    ):
        result = validate_public_media_object_access(key)
        assert result.ok is True
        assert result.object_key == key
        assert result.not_found is False


def test_validate_public_media_object_access_rejects_traversal_and_unknown_scope():
    from web.services.media_object_access import validate_public_media_object_access

    for key in (
        "",
        "/1/medias/123/demo.mp4",
        "1/medias/../secret.mp4",
        "not-user/xxx.mp4",
        "1/not-medias/123/demo.mp4",
    ):
        result = validate_public_media_object_access(key)
        assert result.ok is False
        assert result.not_found is True


def test_build_public_media_object_proxy_response_returns_validated_object_key():
    from web.services.media_object_access import (
        MediaObjectAccess,
        build_public_media_object_proxy_response,
    )

    result = build_public_media_object_proxy_response(
        "uploads/task/1/source.mp4",
        validate_access_fn=lambda key: MediaObjectAccess(True, object_key=key),
    )

    assert result.status_code == 200
    assert result.not_found is False
    assert result.object_key == "uploads/task/1/source.mp4"
    assert result.audit_item is None
