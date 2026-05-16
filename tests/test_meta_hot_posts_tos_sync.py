from pathlib import Path


def test_sync_localized_videos_reconciles_output_relative_paths(monkeypatch, tmp_path):
    from appcore.meta_hot_posts import tos_sync
    from appcore.tos_backup_storage import SyncResult

    output_dir = tmp_path / "output"
    video = output_dir / "meta_hot_posts" / "videos" / "meta_hot_post_20.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"video")
    calls = []

    def fake_query(sql, params=()):
        assert "FROM meta_hot_posts" in sql
        assert params == (10,)
        return [{"id": 20, "local_video_path": "meta_hot_posts/videos/meta_hot_post_20.mp4"}]

    def fake_reconcile(local_path):
        calls.append(Path(local_path))
        return SyncResult(
            local_path=str(local_path),
            object_key="FILES/test/output/meta_hot_post_20.mp4",
            action="uploaded",
            local_exists=True,
            remote_exists=False,
        )

    monkeypatch.setattr(tos_sync.config, "OUTPUT_DIR", str(output_dir))
    monkeypatch.setattr(tos_sync.tos_backup_storage, "is_enabled", lambda: True)

    summary = tos_sync.sync_localized_videos_to_tos(
        limit=10,
        query_fn=fake_query,
        reconcile_fn=fake_reconcile,
    )

    assert calls == [video]
    assert summary["files_checked"] == 1
    assert summary["actions"] == {"uploaded": 1}
    assert summary["failed"] == 0
    assert summary["errors"] == []


def test_sync_localized_videos_reports_invalid_or_missing_paths(monkeypatch, tmp_path):
    from appcore.meta_hot_posts import tos_sync

    output_dir = tmp_path / "output"
    monkeypatch.setattr(tos_sync.config, "OUTPUT_DIR", str(output_dir))
    monkeypatch.setattr(tos_sync.tos_backup_storage, "is_enabled", lambda: True)

    def fake_query(sql, params=()):
        return [
            {"id": 1, "local_video_path": "../escape.mp4"},
            {"id": 2, "local_video_path": "meta_hot_posts/videos/missing.mp4"},
        ]

    summary = tos_sync.sync_localized_videos_to_tos(
        limit=0,
        query_fn=fake_query,
        reconcile_fn=lambda local_path: (_ for _ in ()).throw(AssertionError("must not reconcile")),
    )

    assert summary["files_checked"] == 2
    assert summary["actions"] == {"failed": 2}
    assert summary["failed"] == 2
    assert {item["id"] for item in summary["errors"]} == {1, 2}


def test_run_scheduled_tos_video_sync_records_failed_summary(monkeypatch):
    from appcore.meta_hot_posts import tos_sync

    events = []
    monkeypatch.setattr(
        tos_sync.scheduled_tasks,
        "start_run",
        lambda task_code, **kwargs: events.append(("start", task_code, kwargs)) or 77,
    )
    monkeypatch.setattr(
        tos_sync.scheduled_tasks,
        "finish_run",
        lambda run_id, **kwargs: events.append(("finish", run_id, kwargs)),
    )
    monkeypatch.setattr(
        tos_sync,
        "sync_localized_videos_to_tos",
        lambda limit=200: {"files_checked": 1, "actions": {"failed": 1}, "failed": 1, "errors": []},
    )

    summary = tos_sync.run_scheduled_tos_video_sync()

    assert summary["failed"] == 1
    assert events[0][0:2] == ("start", tos_sync.TASK_CODE)
    assert events[1][0:2] == ("finish", 77)
    assert events[1][2]["status"] == "failed"
    assert "1 Meta hot-post video" in events[1][2]["error_message"]
