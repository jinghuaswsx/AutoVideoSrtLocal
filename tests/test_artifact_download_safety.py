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


def test_artifact_download_variant_falls_back_to_top_level_srt(monkeypatch, tmp_path):
    from flask import Flask
    from web.services import artifact_download

    task_dir = tmp_path / "task"
    task_dir.mkdir()
    srt = task_dir / "subtitle.av.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nBonjour\n", encoding="utf-8")

    called = []
    monkeypatch.setattr(
        artifact_download,
        "send_file",
        lambda *args, **kwargs: called.append((args, kwargs)) or "sent",
    )

    app = Flask(__name__)
    with app.test_request_context():
        result = artifact_download.serve_artifact_download(
            {
                "task_dir": str(task_dir),
                "result": {"srt": str(srt)},
                "variants": {"normal": {}},
            },
            "task-1",
            "srt",
            variant="normal",
        )

    assert result == "sent"
    assert called[0][0][0].endswith("subtitle.av.srt")


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


def test_safe_task_relative_file_response_sends_task_relative_path(monkeypatch, tmp_path):
    from flask import Flask
    from web.services import artifact_download

    task_dir = tmp_path / "task"
    nested = task_dir / "tts_segments"
    nested.mkdir(parents=True)
    inside = nested / "seg_0001.mp3"
    inside.write_bytes(b"audio")

    called = []
    monkeypatch.setattr(
        artifact_download,
        "send_file",
        lambda *args, **kwargs: called.append((args, kwargs)) or "sent",
    )

    app = Flask(__name__)
    with app.app_context():
        result = artifact_download.safe_task_relative_file_response(
            {"task_dir": str(task_dir)},
            "tts_segments/seg_0001.mp3",
            mimetype="audio/mpeg",
        )

    assert result == "sent"
    assert called[0][0][0].endswith("tts_segments/seg_0001.mp3")
    assert called[0][1]["mimetype"] == "audio/mpeg"


def test_safe_task_relative_file_response_rejects_traversal(monkeypatch, tmp_path):
    from flask import Flask
    from web.services import artifact_download

    task_dir = tmp_path / "task"
    task_dir.mkdir()
    outside = tmp_path / "outside.mp3"
    outside.write_bytes(b"outside")

    called = []
    monkeypatch.setattr(
        artifact_download,
        "send_file",
        lambda *args, **kwargs: called.append((args, kwargs)) or "sent",
    )

    app = Flask(__name__)
    with app.app_context():
        response, status = artifact_download.safe_task_relative_file_response(
            {"task_dir": str(task_dir)},
            "../outside.mp3",
        )

    assert status == 404
    assert called == []


def test_resolve_preview_artifact_path_prefers_variant_preview_file(tmp_path):
    from pathlib import Path
    from web.services import artifact_download

    task_dir = tmp_path / "task-preview"
    task_dir.mkdir()
    preview_file = task_dir / "variant-preview.mp4"
    fallback_file = task_dir / "task-preview_soft.normal.mp4"
    preview_file.write_bytes(b"preview")
    fallback_file.write_bytes(b"fallback")

    task = {
        "task_dir": str(task_dir),
        "variants": {
            "normal": {
                "preview_files": {"soft_video": str(preview_file)},
            },
        },
    }

    resolved = artifact_download.resolve_preview_artifact_path(
        "task-preview",
        "soft_video",
        task,
        variant="normal",
    )

    assert Path(resolved) == preview_file


def test_resolve_preview_artifact_path_rejects_paths_outside_allowed_roots(tmp_path):
    from web.services import artifact_download

    task_dir = tmp_path / "task-preview"
    task_dir.mkdir()
    outside_file = tmp_path / "outside.mp3"
    outside_file.write_bytes(b"outside")

    resolved = artifact_download.resolve_preview_artifact_path(
        "task-preview",
        "audio_extract",
        {
            "task_dir": str(task_dir),
            "preview_files": {"audio_extract": str(outside_file)},
        },
    )

    assert resolved is None


def test_send_file_with_range_returns_partial_response(tmp_path):
    from flask import Flask
    from web.services import artifact_download

    artifact = tmp_path / "clip.mp4"
    artifact.write_bytes(b"0123456789")

    app = Flask(__name__)
    with app.test_request_context(headers={"Range": "bytes=2-5"}):
        response = artifact_download.send_file_with_range(str(artifact))

    assert response.status_code == 206
    assert response.get_data() == b"2345"
    assert response.headers["Content-Range"] == "bytes 2-5/10"
    assert response.headers["Accept-Ranges"] == "bytes"
