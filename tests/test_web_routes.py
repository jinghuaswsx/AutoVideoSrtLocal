from web import store
from web.app import create_app


def test_index_page_contains_alignment_and_voice_controls():
    app = create_app()
    client = app.test_client()

    response = client.get("/")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "分段确认" in body
    assert "voiceSelect" in body


def test_alignment_route_compiles_script_segments():
    app = create_app()
    client = app.test_client()

    task = store.create("task-1", "video.mp4", "output/task-1")
    task["utterances"] = [
        {"text": "你好", "start_time": 0.0, "end_time": 0.8, "words": []},
        {"text": "世界", "start_time": 0.8, "end_time": 1.6, "words": []},
    ]

    response = client.put(
        "/api/tasks/task-1/alignment",
        json={"break_after": [False, True]},
    )

    assert response.status_code == 200
    saved = store.get("task-1")
    assert saved["_alignment_confirmed"] is True
    assert saved["script_segments"][0]["text"] == "你好世界"


def test_voice_routes_support_crud(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICES_FILE", str(tmp_path / "voices.json"))
    app = create_app()
    client = app.test_client()

    created = client.post(
        "/api/voices",
        json={
            "name": "Taylor",
            "gender": "female",
            "elevenlabs_voice_id": "voice_1",
            "description": "Warm and bright",
            "style_tags": ["warm", "beauty"],
        },
    )
    assert created.status_code == 201
    voice_id = created.get_json()["voice"]["id"]

    updated = client.put(
        f"/api/voices/{voice_id}",
        json={"description": "Warm and updated"},
    )
    assert updated.status_code == 200
    assert updated.get_json()["voice"]["description"] == "Warm and updated"

    listed = client.get("/api/voices")
    assert listed.status_code == 200
    assert listed.get_json()["voices"][0]["id"] == voice_id

    deleted = client.delete(f"/api/voices/{voice_id}")
    assert deleted.status_code == 200
