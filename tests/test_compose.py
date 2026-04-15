"""pipeline/compose.py 单元测试"""
import logging
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from pipeline.compose import _build_subtitle_filter, _get_video_height


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
