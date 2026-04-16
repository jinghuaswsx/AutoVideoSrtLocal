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


def test_compute_font_size_numeric_int_at_1080p():
    # 数字字号：1080p 下直接返回该值
    assert _compute_font_size(1080, 14) == 14
    assert _compute_font_size(1080, 20) == 20
    assert _compute_font_size(1080, 8) == 8


def test_compute_font_size_numeric_scales_with_height():
    # 720p 下按比例缩放: round(720/1080 * 14) = 9
    assert _compute_font_size(720, 14) == 9
    # 1920p 下放大: round(1920/1080 * 14) = round(24.89) = 25
    assert _compute_font_size(1920, 14) == 25


def test_compute_font_size_numeric_float():
    # 浮点数字号同样支持
    assert _compute_font_size(1080, 14.0) == 14


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


def test_build_subtitle_filter_uses_8char_hex_colors():
    # 必须使用完整 8 位十六进制（AABBGGRR），而不是 3 字节 &HFFFFFF —
    # 部分 libass 版本把 3 字节解析为半透明 alpha 导致字幕不可见
    vf = _build_subtitle_filter("/tmp/sub.srt", "Impact", 14, 346)
    assert "PrimaryColour=&H00FFFFFF" in vf
    assert "OutlineColour=&H00000000" in vf


def test_build_subtitle_filter_sets_border_style_1():
    # 显式 BorderStyle=1 确保走 outline+shadow 渲染，避免系统字体回退时
    # libass 走默认 BorderStyle 导致字幕不显示
    vf = _build_subtitle_filter("/tmp/sub.srt", "Impact", 14, 346)
    assert "BorderStyle=1" in vf


def test_build_subtitle_filter_omits_fontsdir_when_dir_missing(tmp_path, monkeypatch):
    # fonts 目录不存在时不应加入 fontsdir 参数，以免 libass 渲染失败
    import pipeline.compose as compose_mod
    monkeypatch.setattr(compose_mod, "_fonts_dir", lambda: str(tmp_path / "nonexistent"))
    vf = _build_subtitle_filter("/tmp/sub.srt", "Anton", 14, 346)
    assert "fontsdir=" not in vf


def test_build_subtitle_filter_includes_fontsdir_when_dir_exists(tmp_path, monkeypatch):
    import pipeline.compose as compose_mod
    fonts_dir = tmp_path / "fonts"
    fonts_dir.mkdir()
    monkeypatch.setattr(compose_mod, "_fonts_dir", lambda: str(fonts_dir))
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


# ---------------------------------------------------------------------------
# compose_video — with_soft 开关
# ---------------------------------------------------------------------------

class TestComposeVideoWithSoftFlag:
    """控制是否生成软字幕视频。"""

    def _patch_all(self, monkeypatch, calls):
        """让 _compose_soft_from_manifest / _compose_soft_legacy / _compose_hard
        都替换为只记录调用的 mock。"""
        from pipeline import compose as compose_mod

        def fake_soft_manifest(*args, **kwargs):
            calls.append(("soft_manifest", args, kwargs))
        def fake_soft_legacy(*args, **kwargs):
            calls.append(("soft_legacy", args, kwargs))
        def fake_hard(*args, **kwargs):
            calls.append(("hard", args, kwargs))

        monkeypatch.setattr(compose_mod, "_compose_soft_from_manifest", fake_soft_manifest)
        monkeypatch.setattr(compose_mod, "_compose_soft_legacy", fake_soft_legacy)
        monkeypatch.setattr(compose_mod, "_compose_hard", fake_hard)
        monkeypatch.setattr(compose_mod, "_get_duration", lambda p: 10.0)

    def test_with_soft_true_generates_both(self, tmp_path, monkeypatch):
        from pipeline.compose import compose_video
        calls = []
        self._patch_all(monkeypatch, calls)

        result = compose_video(
            video_path=str(tmp_path / "in.mp4"),
            tts_audio_path=str(tmp_path / "tts.mp3"),
            srt_path=str(tmp_path / "sub.srt"),
            output_dir=str(tmp_path),
            timeline_manifest={"segments": [{"video_ranges": [{"start": 0, "end": 1}]}],
                               "total_tts_duration": 1.0, "video_consumed_duration": 1.0},
            with_soft=True,
        )

        kinds = [c[0] for c in calls]
        assert "soft_manifest" in kinds
        assert "hard" in kinds
        assert result["soft_video"] and result["soft_video"].endswith("_soft.mp4")
        assert result["hard_video"] and result["hard_video"].endswith("_hard.mp4")

    def test_with_soft_false_skips_soft(self, tmp_path, monkeypatch):
        from pipeline.compose import compose_video
        calls = []
        self._patch_all(monkeypatch, calls)

        result = compose_video(
            video_path=str(tmp_path / "in.mp4"),
            tts_audio_path=str(tmp_path / "tts.mp3"),
            srt_path=str(tmp_path / "sub.srt"),
            output_dir=str(tmp_path),
            timeline_manifest={"segments": [{"video_ranges": [{"start": 0, "end": 1}]}],
                               "total_tts_duration": 1.0, "video_consumed_duration": 1.0},
            with_soft=False,
        )

        # 硬字幕仍需 soft 作为中间产物，所以 soft_manifest 会被调用
        kinds = [c[0] for c in calls]
        assert "hard" in kinds
        # 但返回值中 soft_video 为 None（中间文件已清理）
        assert result["soft_video"] is None
        assert result["hard_video"] and result["hard_video"].endswith("_hard.mp4")

    def test_default_with_soft_is_true(self, tmp_path, monkeypatch):
        """不传 with_soft 参数时默认生成软字幕，保持向后兼容。"""
        from pipeline.compose import compose_video
        calls = []
        self._patch_all(monkeypatch, calls)

        compose_video(
            video_path=str(tmp_path / "in.mp4"),
            tts_audio_path=str(tmp_path / "tts.mp3"),
            srt_path=str(tmp_path / "sub.srt"),
            output_dir=str(tmp_path),
            timeline_manifest={"segments": [{"video_ranges": [{"start": 0, "end": 1}]}],
                               "total_tts_duration": 1.0, "video_consumed_duration": 1.0},
        )

        kinds = [c[0] for c in calls]
        assert "soft_manifest" in kinds

    def test_with_soft_false_without_manifest_still_skips(self, tmp_path, monkeypatch):
        """legacy 分支（无 timeline_manifest）也要尊重 with_soft=False。"""
        from pipeline.compose import compose_video
        calls = []
        self._patch_all(monkeypatch, calls)

        result = compose_video(
            video_path=str(tmp_path / "in.mp4"),
            tts_audio_path=str(tmp_path / "tts.mp3"),
            srt_path=str(tmp_path / "sub.srt"),
            output_dir=str(tmp_path),
            timeline_manifest=None,
            with_soft=False,
        )

        kinds = [c[0] for c in calls]
        assert "hard" in kinds
        # 返回值 soft_video 为 None
        assert result["soft_video"] is None
