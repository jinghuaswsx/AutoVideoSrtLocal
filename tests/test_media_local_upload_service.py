from __future__ import annotations

from contextlib import nullcontext


def test_complete_local_media_upload_writes_reserved_object_for_owner():
    from web.services.media_local_upload import complete_local_media_upload

    writes = []
    stream = object()
    outcome = complete_local_media_upload(
        "upload-1",
        user_id=7,
        stream=stream,
        reservations={"upload-1": {"user_id": 7, "object_key": "7/medias/1/demo.mp4"}},
        reservation_guard=nullcontext(),
        write_stream_fn=lambda object_key, value: writes.append((object_key, value)),
    )

    assert outcome.not_found is False
    assert outcome.status_code == 204
    assert writes == [("7/medias/1/demo.mp4", stream)]


def test_complete_local_media_upload_hides_missing_or_foreign_reservation():
    from web.services.media_local_upload import complete_local_media_upload

    writes = []
    missing = complete_local_media_upload(
        "missing",
        user_id=7,
        stream=object(),
        reservations={},
        reservation_guard=nullcontext(),
        write_stream_fn=lambda object_key, value: writes.append((object_key, value)),
    )
    foreign = complete_local_media_upload(
        "upload-1",
        user_id=7,
        stream=object(),
        reservations={"upload-1": {"user_id": 8, "object_key": "8/medias/1/demo.mp4"}},
        reservation_guard=nullcontext(),
        write_stream_fn=lambda object_key, value: writes.append((object_key, value)),
    )

    assert missing.not_found is True
    assert missing.status_code == 404
    assert foreign.not_found is True
    assert foreign.status_code == 404
    assert writes == []
