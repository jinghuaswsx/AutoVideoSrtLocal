from appcore.llm_media_optimizer import OptimizedMedia
from appcore import video_ai_review


def test_fetch_and_prepare_media_video_uses_vertex_inline_optimizer(monkeypatch, tmp_path):
    raw = tmp_path / "raw.mp4"
    optimized = tmp_path / "raw.llm.mp4"
    raw.write_bytes(b"raw-video")
    optimized.write_bytes(b"optimized")
    captured = {}

    monkeypatch.setattr(video_ai_review, "_download_to_tmp", lambda file_url, label: str(raw))
    monkeypatch.setattr(
        video_ai_review,
        "_download_object_key_to_tmp",
        lambda object_key, label: (_ for _ in ()).throw(AssertionError("object_key fallback should not run")),
    )

    def fake_prepare(video_path, policy, output_dir=None):
        captured["video_path"] = str(video_path)
        captured["policy"] = policy
        captured["output_dir"] = output_dir
        return OptimizedMedia(
            original_path=str(raw),
            llm_path=str(optimized),
            optimized=True,
            cleanup_path=str(optimized),
            original_bytes=9,
            llm_bytes=9,
            command=["ffmpeg"],
            policy_name=policy.name,
        )

    monkeypatch.setattr(video_ai_review, "prepare_video_for_llm", fake_prepare)

    final_path, tmp_files = video_ai_review._fetch_and_prepare_media_video(
        "https://example.test/raw.mp4",
        "objects/raw.mp4",
        "target_1",
    )

    assert final_path == str(optimized)
    assert tmp_files == [str(raw), str(optimized)]
    assert captured["video_path"] == str(raw)
    assert captured["policy"].name == "vertex_inline_audio"


def test_fetch_and_prepare_media_video_falls_back_to_raw_when_optimizer_fails(monkeypatch, tmp_path):
    raw = tmp_path / "raw.mp4"
    raw.write_bytes(b"raw-video")

    monkeypatch.setattr(video_ai_review, "_download_to_tmp", lambda file_url, label: str(raw))

    def fake_prepare(video_path, policy, output_dir=None):
        return OptimizedMedia(
            original_path=str(raw),
            llm_path=str(raw),
            optimized=False,
            cleanup_path=None,
            original_bytes=9,
            llm_bytes=9,
            command=["ffmpeg"],
            error="ffmpeg failed",
            policy_name=policy.name,
        )

    monkeypatch.setattr(video_ai_review, "prepare_video_for_llm", fake_prepare)

    final_path, tmp_files = video_ai_review._fetch_and_prepare_media_video(
        "https://example.test/raw.mp4",
        None,
        "target_1",
    )

    assert final_path == str(raw)
    assert tmp_files == [str(raw)]
