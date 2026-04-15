from types import SimpleNamespace

from pipeline.compose import _compose_hard, _compute_font_size, _compute_margin_v


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


def test_compute_font_size_medium_at_1080p():
    assert _compute_font_size(1080, "medium") == 14


def test_compute_font_size_small_at_1080p():
    assert _compute_font_size(1080, "small") == 11


def test_compute_font_size_large_at_1080p():
    assert _compute_font_size(1080, "large") == 18


def test_compute_font_size_scales_with_height():
    # 720p medium: round(720/1080*14) = round(9.33) = 9
    assert _compute_font_size(720, "medium") == 9
    # 1920p large: round(1920/1080*18) = round(32.0) = 32
    assert _compute_font_size(1920, "large") == 32


def test_compute_font_size_unknown_preset_falls_back_to_medium():
    assert _compute_font_size(1080, "xlarge") == 14


def test_compute_margin_v_default_position():
    # position_y=0.68 → margin_v = round(1080*(1-0.68)) = round(345.6) = 346
    assert _compute_margin_v(1080, 0.68) == 346


def test_compute_margin_v_bottom():
    # position_y=0.95 → margin_v = round(1080*0.05) = 54
    assert _compute_margin_v(1080, 0.95) == 54


def test_compute_margin_v_top():
    # position_y=0.1 → margin_v = round(1080*0.9) = 972
    assert _compute_margin_v(1080, 0.1) == 972
