from __future__ import annotations


def test_build_product_owner_update_response_updates_owner_for_admin():
    from web.services.media_product_owner import build_product_owner_update_response

    captured = {}

    result = build_product_owner_update_response(
        42,
        {"user_id": "7"},
        is_admin=True,
        get_product_fn=lambda pid: {"id": pid, "deleted_at": None},
        update_product_owner_fn=lambda pid, uid: captured.update({"pid": pid, "uid": uid}),
        get_user_display_name_fn=lambda uid: "李四",
    )

    assert result.not_found is False
    assert result.status_code == 200
    assert result.payload == {"user_id": 7, "owner_name": "李四"}
    assert captured == {"pid": 42, "uid": 7}


def test_build_product_owner_update_response_rejects_non_admin_before_update():
    from web.services.media_product_owner import build_product_owner_update_response

    called = []

    result = build_product_owner_update_response(
        42,
        {"user_id": 7},
        is_admin=False,
        get_product_fn=lambda pid: called.append(("get", pid)) or {"id": pid},
        update_product_owner_fn=lambda pid, uid: called.append(("update", pid, uid)),
        get_user_display_name_fn=lambda uid: "李四",
    )

    assert result.status_code == 403
    assert result.payload == {"error": "仅管理员可操作"}
    assert called == []


def test_build_product_owner_update_response_validates_user_id_before_db_lookup():
    from web.services.media_product_owner import build_product_owner_update_response

    called = []

    result = build_product_owner_update_response(
        42,
        {},
        is_admin=True,
        get_product_fn=lambda pid: called.append(("get", pid)) or {"id": pid},
        update_product_owner_fn=lambda pid, uid: called.append(("update", pid, uid)),
        get_user_display_name_fn=lambda uid: "李四",
    )

    assert result.status_code == 400
    assert result.payload == {"error": "user_id required"}
    assert called == []


def test_build_product_owner_update_response_maps_missing_product_to_not_found():
    from web.services.media_product_owner import build_product_owner_update_response

    result = build_product_owner_update_response(
        9999,
        {"user_id": 1},
        is_admin=True,
        get_product_fn=lambda pid: None,
        update_product_owner_fn=lambda pid, uid: None,
        get_user_display_name_fn=lambda uid: "",
    )

    assert result.not_found is True
    assert result.status_code == 404
    assert result.payload == {}


def test_build_product_owner_update_response_maps_user_error_to_bad_request():
    from web.services.media_product_owner import build_product_owner_update_response

    def raise_user(_pid, _uid):
        raise ValueError("user not found or inactive")

    result = build_product_owner_update_response(
        42,
        {"user_id": 7},
        is_admin=True,
        get_product_fn=lambda pid: {"id": pid, "deleted_at": None},
        update_product_owner_fn=raise_user,
        get_user_display_name_fn=lambda uid: "",
    )

    assert result.not_found is False
    assert result.status_code == 400
    assert result.payload == {"error": "user not found or inactive"}
