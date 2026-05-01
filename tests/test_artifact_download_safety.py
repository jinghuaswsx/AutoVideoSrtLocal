from __future__ import annotations


def test_artifact_download_rejects_paths_outside_task_storage(monkeypatch, tmp_path):
    from flask import Flask
    from web.services import artifact_download

    task_dir = tmp_path / "task"
    task_dir.mkdir()
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"video")

    called = []
    monkeypatch.setattr(
        artifact_download,
        "send_file",
        lambda *args, **kwargs: called.append((args, kwargs)) or "sent",
    )

    app = Flask(__name__)
    with app.app_context():
        response, status = artifact_download.serve_artifact_download(
            {
                "task_dir": str(task_dir),
                "result": {"hard_video": str(outside)},
            },
            "task-1",
            "hard",
        )

    assert status == 404
    assert called == []


def test_artifact_download_rejects_directory_paths(monkeypatch, tmp_path):
    from flask import Flask
    from web.services import artifact_download

    task_dir = tmp_path / "task"
    task_dir.mkdir()
    directory_artifact = task_dir / "not-a-file"
    directory_artifact.mkdir()

    called = []
    monkeypatch.setattr(
        artifact_download,
        "send_file",
        lambda *args, **kwargs: called.append((args, kwargs)) or "sent",
    )

    app = Flask(__name__)
    with app.app_context():
        response, status = artifact_download.serve_artifact_download(
            {
                "task_dir": str(task_dir),
                "result": {"hard_video": str(directory_artifact)},
            },
            "task-1",
            "hard",
        )

    assert status == 404
    assert called == []


def test_safe_task_file_response_rejects_outside_path(monkeypatch, tmp_path):
    from flask import Flask
    from web.services import artifact_download

    task_dir = tmp_path / "task"
    task_dir.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")

    called = []
    monkeypatch.setattr(
        artifact_download,
        "send_file",
        lambda *args, **kwargs: called.append((args, kwargs)) or "sent",
    )

    app = Flask(__name__)
    with app.app_context():
        response, status = artifact_download.safe_task_file_response(
            {"task_dir": str(task_dir)},
            str(outside),
        )

    assert status == 404
    assert called == []


def test_safe_task_file_response_sends_allowed_path(monkeypatch, tmp_path):
    from flask import Flask
    from web.services import artifact_download

    task_dir = tmp_path / "task"
    task_dir.mkdir()
    inside = task_dir / "artifact.json"
    inside.write_text("{}", encoding="utf-8")

    called = []
    monkeypatch.setattr(
        artifact_download,
        "send_file",
        lambda *args, **kwargs: called.append((args, kwargs)) or "sent",
    )

    app = Flask(__name__)
    with app.app_context():
        result = artifact_download.safe_task_file_response(
            {"task_dir": str(task_dir)},
            str(inside),
            mimetype="application/json",
        )

    assert result == "sent"
    assert called[0][0][0].endswith("artifact.json")
    assert called[0][1]["mimetype"] == "application/json"
