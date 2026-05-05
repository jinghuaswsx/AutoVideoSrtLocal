from __future__ import annotations

from types import SimpleNamespace


def test_local_media_upload_route_delegates_to_service(authed_user_client_no_db, monkeypatch):
    captured = {}

    def fake_complete(upload_id, *, user_id, stream, reservations, reservation_guard, write_stream_fn):
        captured.update(
            {
                "upload_id": upload_id,
                "user_id": user_id,
                "reservations": reservations,
                "reservation_guard": reservation_guard,
                "write_stream_fn": write_stream_fn,
                "stream_type": type(stream).__name__,
            }
        )
        return SimpleNamespace(not_found=False, status_code=204)

    monkeypatch.setattr("web.routes.medias.media_upload.complete_local_media_upload", fake_complete)

    response = authed_user_client_no_db.put(
        "/medias/api/local-media-upload/upload-1",
        data=b"video-bytes",
        content_type="video/mp4",
    )

    assert response.status_code == 204
    assert captured["upload_id"] == "upload-1"
    assert captured["user_id"] == 2
    assert captured["reservations"] is not None
    assert captured["reservation_guard"] is not None
    assert callable(captured["write_stream_fn"])


def test_local_media_upload_route_returns_404_for_service_not_found(
    authed_user_client_no_db,
    monkeypatch,
):
    monkeypatch.setattr(
        "web.routes.medias.media_upload.complete_local_media_upload",
        lambda *args, **kwargs: SimpleNamespace(not_found=True, status_code=404),
    )

    response = authed_user_client_no_db.put(
        "/medias/api/local-media-upload/missing",
        data=b"video-bytes",
        content_type="video/mp4",
    )

    assert response.status_code == 404
