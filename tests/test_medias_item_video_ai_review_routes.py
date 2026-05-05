from __future__ import annotations

from types import SimpleNamespace


def test_run_media_item_video_ai_review_route_uses_service_payload(
    authed_user_client_no_db,
    monkeypatch,
):
    item = {"id": 42, "product_id": 7}
    product = {"id": 7, "user_id": 2}
    outcome = SimpleNamespace(
        payload={
            "status": "started",
            "run_id": "run-42",
            "channel": "test-channel",
            "model": "test-model",
        },
        status_code=200,
    )
    captured = {}

    monkeypatch.setattr("web.routes.medias.medias.get_item", lambda item_id: item)
    monkeypatch.setattr("web.routes.medias.medias.get_product", lambda product_id: product)

    def fake_start(item_id, *, user_id):
        captured["item_id"] = item_id
        captured["user_id"] = user_id
        return outcome

    monkeypatch.setattr("web.routes.medias.items.start_media_item_video_ai_review", fake_start)

    response = authed_user_client_no_db.post("/medias/api/items/42/video-ai-review/run")

    assert response.status_code == 200
    assert response.get_json() == outcome.payload
    assert captured == {"item_id": 42, "user_id": 2}


def test_get_media_item_video_ai_review_route_uses_service_payload(
    authed_user_client_no_db,
    monkeypatch,
):
    item = {"id": 42, "product_id": 7}
    product = {"id": 7, "user_id": 2}
    outcome = SimpleNamespace(payload={"review": {"score": 88}}, status_code=200)
    captured = {}

    monkeypatch.setattr("web.routes.medias.medias.get_item", lambda item_id: item)
    monkeypatch.setattr("web.routes.medias.medias.get_product", lambda product_id: product)

    def fake_get(item_id):
        captured["item_id"] = item_id
        return outcome

    monkeypatch.setattr("web.routes.medias.items.get_media_item_video_ai_review", fake_get)

    response = authed_user_client_no_db.get("/medias/api/items/42/video-ai-review")

    assert response.status_code == 200
    assert response.get_json() == {"review": {"score": 88}}
    assert captured == {"item_id": 42}
