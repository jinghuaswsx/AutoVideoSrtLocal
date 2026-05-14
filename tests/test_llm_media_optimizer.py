import subprocess
from pathlib import Path

from appcore.llm_media_optimizer import (
    OptimizedMedia,
    REVIEW_480P_AUDIO,
    VERTEX_INLINE_AUDIO,
    VISUAL_480P_SILENT,
    cleanup_optimized_media,
    media_debug_snapshot,
    prepare_video_for_llm,
)


def test_prepare_video_for_llm_visual_policy_drops_audio(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    calls = []

    monkeypatch.setattr(
        "appcore.llm_media_optimizer.probe_media_info",
        lambda path: {"height": 1080, "duration": 10.0},
    )

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        Path(cmd[-1]).write_bytes(b"small")

    monkeypatch.setattr("appcore.llm_media_optimizer.subprocess.run", fake_run)

    media = prepare_video_for_llm(source, VISUAL_480P_SILENT, output_dir=tmp_path)

    assert media.optimized is True
    assert media.llm_path != str(source)
    assert media.cleanup_path == media.llm_path
    assert media.original_bytes == 6
    assert media.llm_bytes == 5
    assert media.policy_name == "visual_480p_silent"
    assert "-an" in calls[0]
    assert "-c:a" not in calls[0]
    assert "scale=-2:min(480\\,ih),fps=15" in calls[0]


def test_prepare_video_for_llm_audio_policy_preserves_audio(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    calls = []

    monkeypatch.setattr(
        "appcore.llm_media_optimizer.probe_media_info",
        lambda path: {"height": 720, "duration": 8.0},
    )

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        Path(cmd[-1]).write_bytes(b"small")

    monkeypatch.setattr("appcore.llm_media_optimizer.subprocess.run", fake_run)

    media = prepare_video_for_llm(source, REVIEW_480P_AUDIO, output_dir=tmp_path)

    assert media.optimized is True
    assert calls[0][calls[0].index("-vf") + 1] == "scale=-2:min(480\\,ih),fps=15"
    assert calls[0][calls[0].index("-b:v") + 1] == "600k"
    assert calls[0][calls[0].index("-maxrate") + 1] == "800k"
    assert calls[0][calls[0].index("-bufsize") + 1] == "1200k"
    assert "-an" not in calls[0]
    assert calls[0][calls[0].index("-c:a") + 1] == "aac"
    assert calls[0][calls[0].index("-b:a") + 1] == "64k"
    assert calls[0][calls[0].index("-ac") + 1] == "1"


def test_prepare_video_for_llm_missing_source_falls_back(tmp_path):
    missing = tmp_path / "missing.mp4"

    media = prepare_video_for_llm(missing, VISUAL_480P_SILENT, output_dir=tmp_path)

    assert media.optimized is False
    assert media.original_path == str(missing)
    assert media.llm_path == str(missing)
    assert media.cleanup_path is None
    assert media.error == "source_missing"


def test_prepare_video_for_llm_ffmpeg_failure_falls_back(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")

    monkeypatch.setattr(
        "appcore.llm_media_optimizer.probe_media_info",
        lambda path: {"height": 1080, "duration": 10.0},
    )

    def fail_run(cmd, **kwargs):
        raise subprocess.CalledProcessError(1, cmd, stderr="bad encode")

    monkeypatch.setattr("appcore.llm_media_optimizer.subprocess.run", fail_run)

    media = prepare_video_for_llm(source, VISUAL_480P_SILENT, output_dir=tmp_path)

    assert media.optimized is False
    assert media.llm_path == str(source)
    assert media.cleanup_path is None
    assert "bad encode" in (media.error or "")
    assert media.command


def test_cleanup_optimized_media_deletes_only_temp_file(tmp_path):
    source = tmp_path / "source.mp4"
    optimized = tmp_path / "source.llm.mp4"
    source.write_bytes(b"source")
    optimized.write_bytes(b"small")

    cleanup_optimized_media(
        OptimizedMedia(
            original_path=str(source),
            llm_path=str(optimized),
            optimized=True,
            cleanup_path=str(optimized),
            original_bytes=6,
            llm_bytes=5,
            command=["ffmpeg"],
            policy_name="visual_480p_silent",
        )
    )

    assert source.exists()
    assert not optimized.exists()


def test_vertex_inline_policy_uses_omni_default_480p_600k(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    calls = []

    monkeypatch.setattr(
        "appcore.llm_media_optimizer.probe_media_info",
        lambda path: {"height": 1080, "duration": 60.0},
    )

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        Path(cmd[-1]).write_bytes(b"small")

    monkeypatch.setattr("appcore.llm_media_optimizer.subprocess.run", fake_run)

    media = prepare_video_for_llm(source, VERTEX_INLINE_AUDIO, output_dir=tmp_path)

    assert media.optimized is True
    assert calls[0][calls[0].index("-vf") + 1] == "scale=-2:min(480\\,ih),fps=15"
    assert calls[0][calls[0].index("-b:v") + 1] == "600k"
    assert calls[0][calls[0].index("-maxrate") + 1] == "800k"
    assert calls[0][calls[0].index("-bufsize") + 1] == "1200k"
    assert calls[0][calls[0].index("-c:a") + 1] == "aac"


def test_media_debug_snapshot_records_original_and_llm_paths():
    media = OptimizedMedia(
        original_path="/tmp/source.mp4",
        llm_path="/tmp/source.llm.mp4",
        optimized=True,
        cleanup_path="/tmp/source.llm.mp4",
        original_bytes=1000,
        llm_bytes=100,
        command=["ffmpeg", "-i", "/tmp/source.mp4", "/tmp/source.llm.mp4"],
        policy_name="visual_480p_silent",
    )

    snapshot = media_debug_snapshot(media)

    assert snapshot["original_video_path"] == "/tmp/source.mp4"
    assert snapshot["llm_video_path"] == "/tmp/source.llm.mp4"
    assert snapshot["optimized"] is True
    assert snapshot["policy_name"] == "visual_480p_silent"
    assert snapshot["original_bytes"] == 1000
    assert snapshot["llm_bytes"] == 100
    assert snapshot["ffmpeg_command"] == "ffmpeg -i /tmp/source.mp4 /tmp/source.llm.mp4"
