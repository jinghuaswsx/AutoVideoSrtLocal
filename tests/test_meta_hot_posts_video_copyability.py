from pathlib import Path

from appcore.meta_hot_posts import video_copyability


def test_compress_video_for_analysis_uses_480p_15fps_600k(tmp_path):
    output_dir = tmp_path / "output"
    source = output_dir / "meta_hot_posts" / "videos" / "7.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source")
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        target = Path(command[-1])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"compressed")
        return type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    rel_path = video_copyability.compress_video_for_analysis(
        source,
        post_id=7,
        output_dir=output_dir,
        run_fn=fake_run,
        which_fn=lambda name: "ffmpeg" if name == "ffmpeg" else None,
    )

    command, kwargs = calls[0]
    assert rel_path == "meta_hot_posts/analysis_videos/meta_hot_post_7_480p15_600k.mp4"
    assert command[0] == "ffmpeg"
    assert command[command.index("-vf") + 1] == "scale=-2:480,fps=15"
    assert command[command.index("-b:v") + 1] == "600k"
    assert command[command.index("-maxrate") + 1] == "600k"
    assert command[command.index("-bufsize") + 1] == "1200k"
    assert "-movflags" in command
    assert kwargs["timeout"] == 600
    assert kwargs["capture_output"] is True


def test_analyze_video_copyability_invokes_openrouter_with_product_and_video(tmp_path):
    output_dir = tmp_path / "output"
    source = output_dir / "meta_hot_posts" / "videos" / "8.mp4"
    compressed = output_dir / "meta_hot_posts" / "analysis_videos" / "8.mp4"
    source.parent.mkdir(parents=True)
    compressed.parent.mkdir(parents=True)
    source.write_bytes(b"source")
    compressed.write_bytes(b"compressed")
    calls = []

    def fake_compress(local_video_path, *, post_id, output_dir, **kwargs):
        assert local_video_path == source
        assert post_id == 8
        return "meta_hot_posts/analysis_videos/8.mp4"

    def fake_invoke(use_case_code, **kwargs):
        calls.append((use_case_code, kwargs))
        return {
            "json": {
                "overall_score": 91,
                "copyability_score": 94,
                "meta_us_ad_fit_score": 89,
                "product_fit_score": 88,
                "compliance_risk_score": 12,
                "recommendation": "copy",
                "summary": "Strong hook and clear product demonstration.",
                "winning_angles": ["fast before-after"],
                "risk_notes": ["avoid guaranteed claims"],
            },
            "usage": {"input_tokens": 100, "output_tokens": 50},
        }

    result = video_copyability.analyze_video_copyability(
        {
            "analysis_id": 3,
            "hot_post_id": 8,
            "wedev_post_id": 801,
            "product_url": "https://example.com/products/socket",
            "post_url": "https://facebook.com/posts/8",
            "local_video_path": "meta_hot_posts/videos/8.mp4",
            "message_html": "<p>Charge everything faster.</p>",
            "product_title": "Flexible Charging Socket",
            "category_l1": "Tools & Hardware",
            "latest_likes": 1000,
            "latest_comments": 20,
            "latest_shares": 30,
        },
        output_dir=output_dir,
        compress_fn=fake_compress,
        invoke_fn=fake_invoke,
        user_id=9,
    )

    use_case_code, kwargs = calls[0]
    assert use_case_code == "meta_hot_posts.video_copyability"
    assert kwargs["provider_override"] == "openrouter"
    assert kwargs["model_override"] == "google/gemini-3-flash-preview"
    assert kwargs["user_id"] == 9
    assert kwargs["media"] == [compressed]
    assert "https://example.com/products/socket" in kwargs["prompt"]
    assert "US Meta ecosystem ads" in kwargs["prompt"]
    assert result["overall_score"] == 91
    assert result["provider"] == "openrouter"
    assert result["model"] == "google/gemini-3-flash-preview"
    assert result["compressed_video_path"] == "meta_hot_posts/analysis_videos/8.mp4"


def test_run_pending_video_copyability_analyses_persists_success_and_failure(monkeypatch):
    events = []
    sleep_calls = []

    monkeypatch.setattr(
        video_copyability.store,
        "ensure_video_copyability_candidates",
        lambda: events.append(("ensure",)) or 2,
    )
    monkeypatch.setattr(
        video_copyability.store,
        "next_pending_video_copyability_analyses",
        lambda limit: [
            {"analysis_id": 1, "hot_post_id": 10, "local_video_path": "a.mp4"},
            {"analysis_id": 2, "hot_post_id": 11, "local_video_path": "b.mp4"},
        ],
    )
    monkeypatch.setattr(
        video_copyability.store,
        "mark_video_copyability_running",
        lambda analysis_id: events.append(("mark", analysis_id)),
    )
    monkeypatch.setattr(
        video_copyability.store,
        "finish_video_copyability_analysis",
        lambda analysis_id, **kwargs: events.append(("finish", analysis_id, kwargs)),
    )

    def fake_analyze(row, **kwargs):
        if row["analysis_id"] == 2:
            raise RuntimeError("quota exhausted")
        return {"overall_score": 88, "recommendation": "copy"}

    summary = video_copyability.run_pending_video_copyability_analyses(
        limit=2,
        user_id=7,
        per_item_delay_seconds=20,
        sleep_fn=lambda seconds: sleep_calls.append(seconds),
        analyze_fn=fake_analyze,
    )

    assert summary == {"queued": 2, "scanned": 2, "done": 1, "failed": 1}
    assert events[0] == ("ensure",)
    assert events[1] == ("mark", 1)
    assert events[2][0:2] == ("finish", 1)
    assert events[2][2]["error_message"] is None
    assert events[3] == ("mark", 2)
    assert events[4][0:2] == ("finish", 2)
    assert "quota exhausted" in events[4][2]["error_message"]
    assert sleep_calls == [20]
