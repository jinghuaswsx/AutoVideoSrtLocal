from web import store
from web.app import create_app


def test_index_page_contains_alignment_and_voice_controls():
    app = create_app()
    client = app.test_client()

    response = client.get("/")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "voiceSelect" in body
    assert "alignmentReview" in body


def test_index_page_contains_step_preview_container():
    app = create_app()
    client = app.test_client()

    response = client.get("/")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "step-preview" in body
    assert "renderStepPreviews" in body


def test_index_page_supports_new_localization_preview_types():
    app = create_app()
    client = app.test_client()

    response = client.get("/")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "item.type === \"sentences\"" in body
    assert "item.type === \"tts_blocks\"" in body
    assert "item.type === \"subtitle_chunks\"" in body


def test_task_detail_returns_artifacts_structure():
    app = create_app()
    client = app.test_client()

    store.create("task-preview", "video.mp4", "output/task-preview")

    response = client.get("/api/tasks/task-preview")

    assert response.status_code == 200
    payload = response.get_json()
    assert "artifacts" in payload
    assert payload["artifacts"] == {}


def test_artifact_route_serves_whitelisted_preview_file(tmp_path):
    app = create_app()
    client = app.test_client()

    audio_path = tmp_path / "preview.mp3"
    audio_path.write_bytes(b"audio-preview")
    store.create("task-file", "video.mp4", str(tmp_path))
    store.update("task-file", preview_files={"audio_extract": str(audio_path)})

    response = client.get("/api/tasks/task-file/artifact/audio_extract")

    assert response.status_code == 200
    assert response.data == b"audio-preview"


def test_artifact_route_rejects_unknown_name(tmp_path):
    app = create_app()
    client = app.test_client()

    store.create("task-bad", "video.mp4", str(tmp_path))

    response = client.get("/api/tasks/task-bad/artifact/not_allowed")

    assert response.status_code == 404


def test_alignment_route_compiles_script_segments():
    app = create_app()
    client = app.test_client()

    task = store.create("task-1", "video.mp4", "output/task-1")
    task["utterances"] = [
        {"text": "浣犲ソ", "start_time": 0.0, "end_time": 0.8, "words": []},
        {"text": "涓栫晫", "start_time": 0.8, "end_time": 1.6, "words": []},
    ]

    response = client.put(
        "/api/tasks/task-1/alignment",
        json={"break_after": [False, True]},
    )

    assert response.status_code == 200
    saved = store.get("task-1")
    assert saved["_alignment_confirmed"] is True
    assert saved["script_segments"][0]["text"] == "浣犲ソ涓栫晫"
    assert saved["artifacts"]["alignment"]["items"][1]["segments"][0]["text"] == "浣犲ソ涓栫晫"


def test_segments_route_updates_translate_artifact():
    app = create_app()
    client = app.test_client()

    store.create("task-translate", "video.mp4", "output/task-translate")
    store.update(
        "task-translate",
        script_segments=[{"text": "你好世界", "translated": "Hello world", "start_time": 0.0, "end_time": 1.6}],
        segments=[{"text": "你好世界", "translated": "Hello world", "start_time": 0.0, "end_time": 1.6}],
    )

    response = client.put(
        "/api/tasks/task-translate/segments",
        json={"segments": [{"text": "你好世界", "translated": "Hello there", "start_time": 0.0, "end_time": 1.6}]},
    )

    assert response.status_code == 200
    saved = store.get("task-translate")
    assert saved["_segments_confirmed"] is True
    assert saved["artifacts"]["translate"]["items"][0]["segments"][0]["translated"] == "Hello there"


def test_segments_route_updates_localized_translation_for_future_tts():
    app = create_app()
    client = app.test_client()

    store.create("task-translate-localized", "video.mp4", "output/task-translate-localized")
    store.update(
        "task-translate-localized",
        source_full_text_zh="你好世界",
        script_segments=[{"index": 0, "text": "你好世界", "start_time": 0.0, "end_time": 1.6}],
        segments=[{"index": 0, "text": "你好世界", "translated": "Hello world", "start_time": 0.0, "end_time": 1.6}],
    )

    response = client.put(
        "/api/tasks/task-translate-localized/segments",
        json={"segments": [{"index": 0, "text": "你好世界", "translated": "Hello there", "start_time": 0.0, "end_time": 1.6}]},
    )

    assert response.status_code == 200
    saved = store.get("task-translate-localized")
    assert saved["script_segments"][0]["text"] == "你好世界"
    assert saved["localized_translation"]["full_text"] == "Hello there"
    assert saved["localized_translation"]["sentences"][0]["source_segment_indices"] == [0]


def test_task_payload_exposes_tts_script_and_corrected_subtitle():
    app = create_app()
    client = app.test_client()
    store.create("task-payload", "video.mp4", "output/task-payload")
    store.update(
        "task-payload",
        tts_script={"full_text": "Say it smooth.", "blocks": [], "subtitle_chunks": []},
        corrected_subtitle={"chunks": [], "srt_content": "1\n00:00:00,000 --> 00:00:01,000\nSay it smooth.\n"},
    )

    response = client.get("/api/tasks/task-payload")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["tts_script"]["full_text"] == "Say it smooth."
    assert "Say it smooth." in payload["corrected_subtitle"]["srt_content"]


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
