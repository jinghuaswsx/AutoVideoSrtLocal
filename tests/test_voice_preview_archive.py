from __future__ import annotations

from pathlib import Path


def test_attach_local_preview_urls_prefers_ready_existing_archive(tmp_path, monkeypatch):
    from appcore import voice_preview_archive as archive

    audio = tmp_path / "voice.mp3"
    audio.write_bytes(b"audio")
    monkeypatch.setattr(archive, "UPLOAD_DIR", str(tmp_path))
    remote_url = "https://example.com/preview.mp3"
    preview_hash = archive.hash_preview_url(remote_url)

    def fake_query(sql, params=()):
        assert "FROM voice_preview_archives" in sql
        return [{
            "voice_id": "voice-1",
            "language": "en",
            "preview_url_hash": preview_hash,
            "local_path": str(audio),
            "duration_seconds": 12.345,
            "transcript_text": "Hello from the preview.",
            "status": "ready",
        }]

    monkeypatch.setattr(archive, "query", fake_query)

    items = [{
        "voice_id": "voice-1",
        "language": "en",
        "preview_url": remote_url,
    }]

    annotated = archive.attach_local_preview_urls(items, language="en")

    assert annotated[0]["preview_url"] == remote_url
    assert annotated[0]["preview_url_hash"] == preview_hash
    assert annotated[0]["preview_local_url"] == (
        f"/voice-library/api/preview/en/voice-1?hash={preview_hash}"
    )
    assert annotated[0]["preview_duration_seconds"] == 12.345
    assert annotated[0]["preview_transcript_text"] == "Hello from the preview."


def test_attach_local_preview_urls_keeps_remote_fallback_when_file_missing(tmp_path, monkeypatch):
    from appcore import voice_preview_archive as archive

    remote_url = "https://example.com/missing.mp3"
    monkeypatch.setattr(archive, "UPLOAD_DIR", str(tmp_path))
    preview_hash = archive.hash_preview_url(remote_url)

    monkeypatch.setattr(
        archive,
        "query",
        lambda *a, **kw: [{
            "voice_id": "voice-1",
            "language": "en",
            "preview_url_hash": preview_hash,
            "local_path": str(tmp_path / "missing.mp3"),
            "duration_seconds": 4.0,
            "transcript_text": "old",
            "status": "ready",
        }],
    )

    annotated = archive.attach_local_preview_urls(
        [{"voice_id": "voice-1", "language": "en", "preview_url": remote_url}],
        language="en",
    )

    assert "preview_local_url" not in annotated[0]
    assert annotated[0]["preview_url"] == remote_url
    assert annotated[0]["preview_url_hash"] == preview_hash


def test_archive_preview_target_downloads_measures_transcribes_and_upserts(tmp_path, monkeypatch):
    from appcore import voice_preview_archive as archive

    writes = []
    rate_writes = []

    def fake_download(url: str, dest: Path) -> str:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"mp3")
        return str(dest)

    monkeypatch.setattr(archive, "_download_preview", fake_download)
    monkeypatch.setattr(archive, "_get_audio_duration", lambda path: 7.25)
    monkeypatch.setattr(
        archive,
        "_transcribe_preview",
        lambda path, lang: [
            {
                "text": "Hello world",
                "start_time": 0.0,
                "end_time": 2.0,
                "words": [
                    {"text": "Hello", "start_time": 0.0, "end_time": 0.8},
                    {"text": "world", "start_time": 1.0, "end_time": 2.0},
                ],
            }
        ],
    )
    monkeypatch.setattr(archive, "upsert_archive", lambda **kw: writes.append(kw))
    monkeypatch.setattr(
        archive.voice_preview_speech_rate,
        "upsert_rate",
        lambda **kw: rate_writes.append(kw),
    )

    result = archive.archive_preview_target(
        {
            "voice_id": "voice-1",
            "language": "en",
            "preview_url": "https://example.com/preview.mp3",
            "preview_url_hash": archive.hash_preview_url("https://example.com/preview.mp3"),
        },
        archive_dir=str(tmp_path / "archive"),
    )

    assert result["status"] == "ready"
    assert result["duration_seconds"] == 7.25
    assert result["transcript_text"] == "Hello world"
    assert Path(result["local_path"]).is_file()
    assert writes[0]["voice_id"] == "voice-1"
    assert writes[0]["utterances_json"][0]["text"] == "Hello world"
    assert writes[0]["asr_source"] == "preview_asr:elevenlabs_scribe"
    assert rate_writes[0]["voice_id"] == "voice-1"
    assert rate_writes[0]["sample_duration"] == 2.0


def test_archive_missing_voice_previews_supports_worker_pool(monkeypatch):
    from appcore import voice_preview_archive as archive

    processed = []
    progress = []
    monkeypatch.setattr(
        archive,
        "list_preview_archive_targets",
        lambda **kw: [
            {"voice_id": "v1", "language": "en", "preview_url": "https://e/v1.mp3"},
            {"voice_id": "v2", "language": "en", "preview_url": "https://e/v2.mp3"},
        ],
    )

    def fake_archive_target(target, *, archive_dir=None):
        processed.append(target["voice_id"])
        return {"status": "ready", "voice_id": target["voice_id"]}

    monkeypatch.setattr(archive, "archive_preview_target", fake_archive_target)

    result = archive.archive_missing_voice_previews(
        language="en",
        workers=2,
        on_progress=lambda done, total, voice_id, ok: progress.append((done, total, voice_id, ok)),
    )

    assert result == {"total": 2, "archived": 2, "failed": 0, "skipped": 0}
    assert sorted(processed) == ["v1", "v2"]
    assert len(progress) == 2
    assert {row[1] for row in progress} == {2}
    assert {row[3] for row in progress} == {True}
