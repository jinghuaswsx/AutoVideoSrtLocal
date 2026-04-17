"""Tests for the `join_admin` SocketIO handler (Task 7.3).

The handler lets admin clients join the shared "admin" room so that
`voice_library_sync_task` progress broadcasts (`socketio.emit(..., to="admin")`)
reach the admin UI.
"""
from web.extensions import socketio


def test_admin_joins_admin_room_when_role_admin(authed_client_no_db):
    """Admin user connecting and emitting `join_admin` stays connected.

    There is no public API to introspect which rooms a test client is in,
    so we assert the handler does not raise and the connection is preserved.
    """
    sio_client = socketio.test_client(
        authed_client_no_db.application,
        flask_test_client=authed_client_no_db,
    )
    try:
        assert sio_client.is_connected()
        sio_client.emit("join_admin")
        assert sio_client.is_connected()
    finally:
        sio_client.disconnect()


def test_non_admin_does_not_raise_on_join_admin(authed_user_client_no_db):
    """Non-admin users can emit `join_admin` safely — handler returns silently."""
    sio_client = socketio.test_client(
        authed_user_client_no_db.application,
        flask_test_client=authed_user_client_no_db,
    )
    try:
        assert sio_client.is_connected()
        sio_client.emit("join_admin")
        assert sio_client.is_connected()
    finally:
        sio_client.disconnect()


def test_join_admin_actually_joins_admin_room(authed_client_no_db, monkeypatch):
    """Verify the handler calls `join_room("admin")` when the user is admin."""
    joined_rooms: list[str] = []
    monkeypatch.setattr("web.app.join_room", lambda room: joined_rooms.append(room))

    sio_client = socketio.test_client(
        authed_client_no_db.application,
        flask_test_client=authed_client_no_db,
    )
    try:
        sio_client.emit("join_admin")
        assert joined_rooms == ["admin"]
    finally:
        sio_client.disconnect()


def test_join_admin_skips_join_for_non_admin(
    authed_user_client_no_db, monkeypatch
):
    """Non-admin users must NOT be added to the `admin` room."""
    joined_rooms: list[str] = []
    monkeypatch.setattr("web.app.join_room", lambda room: joined_rooms.append(room))

    sio_client = socketio.test_client(
        authed_user_client_no_db.application,
        flask_test_client=authed_user_client_no_db,
    )
    try:
        sio_client.emit("join_admin")
        assert joined_rooms == []
    finally:
        sio_client.disconnect()
