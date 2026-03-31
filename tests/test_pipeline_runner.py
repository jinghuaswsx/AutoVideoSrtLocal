from web import store
from web.services import pipeline_runner


def test_step_alignment_auto_confirms_when_interactive_review_disabled(tmp_path, monkeypatch):
    task = store.create("task-auto-alignment", "video.mp4", str(tmp_path))
    task["utterances"] = [
        {"text": "你好", "start_time": 0.0, "end_time": 0.8},
        {"text": "世界", "start_time": 0.8, "end_time": 1.6},
    ]

    monkeypatch.setattr(pipeline_runner, "emit", lambda *args, **kwargs: None)
    monkeypatch.setattr("pipeline.alignment.detect_scene_cuts", lambda video_path: [])
    monkeypatch.setattr(
        "pipeline.alignment.compile_alignment",
        lambda utterances, scene_cuts=None: {
            "break_after": [False, True],
            "script_segments": [
                {"text": "你好世界", "start_time": 0.0, "end_time": 1.6},
            ],
        },
    )

    class FakeVoiceLibrary:
        def recommend_voice(self, text):
            return {"id": "adam"}

    monkeypatch.setattr("pipeline.voice_library.get_voice_library", lambda: FakeVoiceLibrary())

    pipeline_runner._step_alignment("task-auto-alignment", "video.mp4", str(tmp_path))

    saved = store.get("task-auto-alignment")
    assert saved["_alignment_confirmed"] is True
    assert saved["steps"]["alignment"] == "done"
    assert saved["script_segments"][0]["text"] == "你好世界"


def test_step_translate_auto_confirms_when_interactive_review_disabled(tmp_path, monkeypatch):
    task = store.create("task-auto-translate", "video.mp4", str(tmp_path))
    task["script_segments"] = [
        {"text": "你好世界", "start_time": 0.0, "end_time": 1.6},
    ]

    monkeypatch.setattr(pipeline_runner, "emit", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "pipeline.translate.translate_segments",
        lambda segments: [
            {"text": "你好世界", "translated": "Hello world", "start_time": 0.0, "end_time": 1.6},
        ],
    )

    pipeline_runner._step_translate("task-auto-translate")

    saved = store.get("task-auto-translate")
    assert saved["_segments_confirmed"] is True
    assert saved["steps"]["translate"] == "done"
    assert saved["script_segments"][0]["translated"] == "Hello world"


def test_start_route_defaults_interactive_review_to_false(monkeypatch):
    app = __import__("web.app", fromlist=["create_app"]).create_app()
    client = app.test_client()
    store.create("task-start-auto", "video.mp4", "output/task-start-auto")
    captured = {}

    monkeypatch.setattr("web.services.pipeline_runner.start", lambda task_id: captured.setdefault("task_id", task_id))

    response = client.post("/api/tasks/task-start-auto/start", json={})

    assert response.status_code == 200
    assert captured["task_id"] == "task-start-auto"
    assert store.get("task-start-auto")["interactive_review"] is False
