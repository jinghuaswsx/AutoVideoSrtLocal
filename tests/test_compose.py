from types import SimpleNamespace

from pipeline.compose import _compose_hard


def test_compose_hard_uses_filename_quoted_subtitle_filter_on_windows(monkeypatch, tmp_path):
    captured = {}

    def fake_run(cmd, capture_output=True, text=True):
        captured["cmd"] = cmd
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr("pipeline.compose.subprocess.run", fake_run)

    video_path = str(tmp_path / "video_soft.mp4")
    output_path = str(tmp_path / "video_hard.mp4")
    windows_srt_path = r"G:\Code\AutoVideoSrt\output\task\subtitle.srt"

    _compose_hard(video_path, windows_srt_path, "bottom", output_path)

    vf = captured["cmd"][captured["cmd"].index("-vf") + 1]
    assert vf.startswith(
        "subtitles=filename='G\\:/Code/AutoVideoSrt/output/task/subtitle.srt':force_style='"
    )
    assert "Alignment=2,MarginV=50" in vf
