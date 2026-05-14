from pathlib import Path


def test_ytdlp_dependency_is_declared_for_video_localization():
    requirements = Path("requirements.txt").read_text(encoding="utf-8")

    assert "yt-dlp" in requirements


def test_download_hot_post_videos_is_serial_and_waits_between_items(tmp_path, monkeypatch):
    from appcore.meta_hot_posts import store, video_localization

    rows = [
        {"id": 1, "video_url": "https://www.facebook.com/reel/1/"},
        {"id": 2, "video_url": "https://www.facebook.com/reel/2/"},
    ]
    events = []
    sleeps = []

    monkeypatch.setattr(store, "next_pending_local_videos", lambda limit, max_attempts=3: rows)
    monkeypatch.setattr(store, "mark_local_video_downloading", lambda post_id: events.append(("running", post_id)))
    monkeypatch.setattr(
        store,
        "finish_local_video_download",
        lambda post_id, local_video_path=None, error_message=None: events.append(
            ("finish", post_id, local_video_path, error_message)
        ),
    )

    def fake_download(row, *, cache_root, output_dir):
        events.append(("download", row["id"], str(cache_root), str(output_dir)))
        return f"meta_hot_posts/videos/{row['id']}.mp4"

    result = video_localization.download_hot_post_videos(
        limit=2,
        min_delay_seconds=2,
        cache_root=tmp_path / "videos",
        output_dir=tmp_path,
        download_fn=fake_download,
        sleep_fn=sleeps.append,
    )

    assert result == {"scanned": 2, "downloaded": 2, "failed": 0}
    assert events == [
        ("running", 1),
        ("download", 1, str(tmp_path / "videos"), str(tmp_path)),
        ("finish", 1, "meta_hot_posts/videos/1.mp4", None),
        ("running", 2),
        ("download", 2, str(tmp_path / "videos"), str(tmp_path)),
        ("finish", 2, "meta_hot_posts/videos/2.mp4", None),
    ]
    assert sleeps == [10.0]


def test_download_hot_post_videos_records_failure_and_still_waits_before_next(tmp_path, monkeypatch):
    from appcore.meta_hot_posts import store, video_localization

    rows = [
        {"id": 1, "video_url": "https://www.facebook.com/reel/1/"},
        {"id": 2, "video_url": "https://www.facebook.com/reel/2/"},
    ]
    finishes = []
    sleeps = []

    monkeypatch.setattr(store, "next_pending_local_videos", lambda limit, max_attempts=3: rows)
    monkeypatch.setattr(store, "mark_local_video_downloading", lambda post_id: None)
    monkeypatch.setattr(
        store,
        "finish_local_video_download",
        lambda post_id, local_video_path=None, error_message=None: finishes.append(
            (post_id, local_video_path, error_message)
        ),
    )

    def fake_download(row, *, cache_root, output_dir):
        if row["id"] == 1:
            raise RuntimeError("facebook throttled")
        return "meta_hot_posts/videos/2.mp4"

    result = video_localization.download_hot_post_videos(
        limit=2,
        cache_root=tmp_path / "videos",
        output_dir=tmp_path,
        download_fn=fake_download,
        sleep_fn=sleeps.append,
    )

    assert result == {"scanned": 2, "downloaded": 1, "failed": 1}
    assert finishes[0] == (1, None, "facebook throttled")
    assert finishes[1] == (2, "meta_hot_posts/videos/2.mp4", None)
    assert sleeps == [10.0]


def test_download_with_ytdlp_writes_under_cache_root_and_returns_relative_path(tmp_path):
    from appcore.meta_hot_posts import video_localization

    calls = []

    def fake_run(command, *, timeout, capture_output, text):
        calls.append((command, timeout, capture_output, text))
        Path(command[-2].replace("%(ext)s", "mp4")).write_bytes(b"video")
        return type("Proc", (), {"returncode": 0, "stderr": "", "stdout": ""})()

    result = video_localization.download_with_ytdlp(
        {"id": 42, "wedev_post_id": 99, "video_url": "https://www.facebook.com/reel/42/"},
        cache_root=tmp_path / "output" / "meta_hot_posts" / "videos",
        output_dir=tmp_path / "output",
        run_fn=fake_run,
        which_fn=lambda name: "yt-dlp",
    )

    assert result == "meta_hot_posts/videos/meta_hot_post_42.mp4"
    command = calls[0][0]
    assert command[0] == "yt-dlp"
    assert "--no-playlist" in command
    assert command[-1] == "https://www.facebook.com/reel/42/"


def test_resolve_local_video_path_rejects_paths_outside_output_dir(tmp_path):
    from appcore.meta_hot_posts import video_localization

    inside = tmp_path / "output" / "meta_hot_posts" / "videos" / "1.mp4"
    inside.parent.mkdir(parents=True)
    inside.write_bytes(b"video")

    assert video_localization.resolve_local_video_path(
        "meta_hot_posts/videos/1.mp4",
        output_dir=tmp_path / "output",
    ) == inside
    assert video_localization.resolve_local_video_path(
        "../secret.mp4",
        output_dir=tmp_path / "output",
    ) is None
