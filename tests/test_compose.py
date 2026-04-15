"""pipeline/compose.py 单元测试"""
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from pipeline.compose import (
    _build_subtitle_filter,
    _compose_hard,
    _compute_font_size,
    _compute_margin_v,
    _get_video_height,
)


# ---------------------------------------------------------------------------
# _compose_hard 集成测试
# ---------------------------------------------------------------------------

def test_compose_hard_uses_filename_quoted_subtitle_filter_on_windows(monkeypatch, tmp_path):
    captured = {}

    def fake_run(cmd, capture_output=True, text=True):
        captured["cmd"] = cmd
        if cmd and "ffprobe" in cmd[0]:
            return SimpleNamespace(returncode=0, stdout="1080\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("pipeline.compose.subprocess.run", fake_run)

    video_path = str(tmp_path / "video_soft.mp4")
    output_path = str(tmp_path / "video_hard.mp4")
    windows_srt_path = r"G:\Code\AutoVideoSrt\output\task\subtitle.srt"

    _compose_hard(video_path, windows_srt_path, output_path)

    vf = captured["cmd"][captured["cmd"].index("-vf") + 1]
    assert "G\\:/Code/AutoVideoSrt/output/task/subtitle.srt" in vf
    assert "FontName=Impact" in vf
    assert "FontSize=14" in vf    # medium preset at 1080p
    assert "MarginV=346" in vf    # round(1080*(1-0.68))
    assert "Alignment=2" in vf


# ---------------------------------------------------------------------------
# _compute_font_size
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# _compute_margin_v
# ---------------------------------------------------------------------------

def test_compute_margin_v_default_position():
    # position_y=0.68 → margin_v = round(1080*(1-0.68)) = round(345.6) = 346
    assert _compute_margin_v(1080, 0.68) == 346


def test_compute_margin_v_bottom():
    # position_y=0.95 → margin_v = round(1080*0.05) = 54
    assert _compute_margin_v(1080, 0.95) == 54


def test_compute_margin_v_top():
    # position_y=0.1 → margin_v = round(1080*0.9) = 972
    assert _compute_margin_v(1080, 0.1) == 972


# ---------------------------------------------------------------------------
# _build_subtitle_filter — 内容校验
# ---------------------------------------------------------------------------

def test_build_subtitle_filter_includes_font_name():
    vf = _build_subtitle_filter("/tmp/sub.srt", "Anton", 14, 346)
    assert "FontName=Anton" in vf


def test_build_subtitle_filter_includes_font_size():
    vf = _build_subtitle_filter("/tmp/sub.srt", "Impact", 18, 50)
    assert "FontSize=18" in vf


def test_build_subtitle_filter_includes_margin_v():
    vf = _build_subtitle_filter("/tmp/sub.srt", "Impact", 14, 346)
    assert "MarginV=346" in vf
    assert "Alignment=2" in vf


def test_build_subtitle_filter_includes_fontsdir():
    vf = _build_subtitle_filter("/tmp/sub.srt", "Anton", 14, 346)
    assert "fontsdir=" in vf


# ---------------------------------------------------------------------------
# _build_subtitle_filter — font_name 校验（防注入）
# ---------------------------------------------------------------------------

class TestBuildSubtitleFilterSanitizesFontName:
    def test_valid_font_name_passes_through(self):
        result = _build_subtitle_filter("/tmp/sub.srt", "Impact", 14, 100)
        assert "FontName=Impact" in result

    def test_font_name_with_spaces_passes_through(self):
        result = _build_subtitle_filter("/tmp/sub.srt", "Arial Bold", 14, 100)
        assert "FontName=Arial Bold" in result

    def test_font_name_with_hyphen_passes_through(self):
        result = _build_subtitle_filter("/tmp/sub.srt", "Noto-Sans", 14, 100)
        assert "FontName=Noto-Sans" in result

    def test_font_name_with_comma_falls_back_to_impact(self):
        result = _build_subtitle_filter("/tmp/sub.srt", "Arial,Bold", 14, 100)
        assert "FontName=Impact" in result

    def test_font_name_with_single_quote_falls_back_to_impact(self):
        result = _build_subtitle_filter("/tmp/sub.srt", "Arial'Bold", 14, 100)
        assert "FontName=Impact" in result

    def test_font_name_with_colon_falls_back_to_impact(self):
        result = _build_subtitle_filter("/tmp/sub.srt", "Arial:Bold", 14, 100)
        assert "FontName=Impact" in result

    def test_empty_font_name_falls_back_to_impact(self):
        result = _build_subtitle_filter("/tmp/sub.srt", "", 14, 100)
        assert "FontName=Impact" in result


# ---------------------------------------------------------------------------
# _get_video_height — 错误处理
# ---------------------------------------------------------------------------

class TestGetVideoHeightReturnsDefaultOnFailure:
    def _make_result(self, returncode: int, stdout: str = "", stderr: str = "") -> MagicMock:
        mock = MagicMock()
        mock.returncode = returncode
        mock.stdout = stdout
        mock.stderr = stderr
        return mock

    def test_returns_height_on_success(self):
        with patch("pipeline.compose.subprocess.run", return_value=self._make_result(0, "1920\n")):
            assert _get_video_height("/fake/video.mp4") == 1920

    def test_returns_default_on_nonzero_returncode(self):
        with patch(
            "pipeline.compose.subprocess.run",
            return_value=self._make_result(1, "", "No such file"),
        ):
            assert _get_video_height("/fake/video.mp4") == 1080

    def test_returns_default_on_unparseable_stdout(self):
        with patch(
            "pipeline.compose.subprocess.run",
            return_value=self._make_result(0, "N/A\n"),
        ):
            assert _get_video_height("/fake/video.mp4") == 1080

    def test_logs_warning_on_nonzero_returncode(self, caplog):
        with patch(
            "pipeline.compose.subprocess.run",
            return_value=self._make_result(2, "", "ffprobe error detail"),
        ):
            with caplog.at_level(logging.WARNING, logger="pipeline.compose"):
                _get_video_height("/fake/video.mp4")
        assert "returncode=2" in caplog.text

    def test_logs_warning_on_unparseable_stdout(self, caplog):
        with patch(
            "pipeline.compose.subprocess.run",
            return_value=self._make_result(0, "garbage"),
        ):
            with caplog.at_level(logging.WARNING, logger="pipeline.compose"):
                _get_video_height("/fake/video.mp4")
        assert "无法解析" in caplog.text
