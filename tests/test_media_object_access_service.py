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
