"""公共工具模块测试。

测试即将从各处抽取出来的公共工具函数：
1. pipeline.ffutil.get_media_duration — 统一的 ffprobe 时长获取
2. pipeline.llm_util.parse_json_response — 统一的 LLM JSON 解析
3. web.upload_util.validate_video_extension — 统一的上传扩展名校验
"""
import json

import pytest


# ── 1. ffutil: 统一 ffprobe 时长获取 ──

class TestGetMediaDuration:
    """pipeline.ffutil.get_media_duration 应存在且工作正确。"""

    def test_module_exists(self):
        """pipeline.ffutil 模块应存在。"""
        import pipeline.ffutil

    def test_valid_media_returns_float(self, tmp_path, monkeypatch):
        """对有效媒体文件返回 float 时长。"""
        from pipeline.ffutil import get_media_duration

        # mock subprocess 返回正常时长
        class FakeResult:
            stdout = "12.345\n"
            returncode = 0

        monkeypatch.setattr("subprocess.run", lambda *a, **kw: FakeResult())

        duration = get_media_duration(str(tmp_path / "test.mp4"))
        assert isinstance(duration, float)
        assert abs(duration - 12.345) < 0.001

    def test_invalid_path_returns_zero(self, monkeypatch):
        """路径无效时应返回 0.0。"""
        from pipeline.ffutil import get_media_duration

        class FakeResult:
            stdout = ""
            returncode = 1

        monkeypatch.setattr("subprocess.run", lambda *a, **kw: FakeResult())

        duration = get_media_duration("/nonexistent/file.mp4")
        assert duration == 0.0

    def test_ffprobe_exception_returns_zero(self, monkeypatch):
        """ffprobe 出异常时应返回 0.0 而非崩溃。"""
        from pipeline.ffutil import get_media_duration

        monkeypatch.setattr("subprocess.run",
                            lambda *a, **kw: (_ for _ in ()).throw(FileNotFoundError("ffprobe not found")))

        duration = get_media_duration("/some/file.mp4")
        assert duration == 0.0


# ── 2. llm_util: 统一 JSON 解析 ──

class TestParseJsonResponse:
    """pipeline.llm_util.parse_json_response 应存在且处理各种格式。"""

    def test_module_exists(self):
        """pipeline.llm_util 模块应存在。"""
        import pipeline.llm_util

    def test_plain_json(self):
        from pipeline.llm_util import parse_json_response
        data = {"key": "value"}
        result = parse_json_response(json.dumps(data))
        assert result == data

    def test_json_array(self):
        from pipeline.llm_util import parse_json_response
        data = [{"index": 0, "text": "hello"}]
        result = parse_json_response(json.dumps(data))
        assert result == data

    def test_markdown_code_block(self):
        from pipeline.llm_util import parse_json_response
        raw = '```json\n{"key": "value"}\n```'
        result = parse_json_response(raw)
        assert result == {"key": "value"}

    def test_markdown_no_lang_tag(self):
        from pipeline.llm_util import parse_json_response
        raw = '```\n{"key": "value"}\n```'
        result = parse_json_response(raw)
        assert result == {"key": "value"}

    def test_text_before_json(self):
        """JSON 前有解释文本的情况。"""
        from pipeline.llm_util import parse_json_response
        raw = 'Here is the result:\n{"key": "value"}'
        result = parse_json_response(raw)
        assert result == {"key": "value"}

    def test_none_raises(self):
        from pipeline.llm_util import parse_json_response
        with pytest.raises((TypeError, ValueError)):
            parse_json_response(None)

    def test_empty_raises(self):
        from pipeline.llm_util import parse_json_response
        with pytest.raises((json.JSONDecodeError, ValueError)):
            parse_json_response("")

    def test_garbage_raises(self):
        from pipeline.llm_util import parse_json_response
        with pytest.raises((json.JSONDecodeError, ValueError)):
            parse_json_response("this is not json")


# ── 3. upload_util: 统一上传扩展名校验 ──

class TestValidateVideoExtension:
    """web.upload_util.validate_video_extension 应存在且正确校验。"""

    def test_module_exists(self):
        import web.upload_util

    def test_mp4_accepted(self):
        from web.upload_util import validate_video_extension
        assert validate_video_extension("video.mp4") is True

    def test_mov_accepted(self):
        from web.upload_util import validate_video_extension
        assert validate_video_extension("video.mov") is True

    def test_webm_accepted(self):
        from web.upload_util import validate_video_extension
        assert validate_video_extension("video.webm") is True

    def test_mkv_accepted(self):
        from web.upload_util import validate_video_extension
        assert validate_video_extension("video.mkv") is True

    def test_avi_accepted(self):
        from web.upload_util import validate_video_extension
        assert validate_video_extension("video.avi") is True

    def test_case_insensitive(self):
        from web.upload_util import validate_video_extension
        assert validate_video_extension("video.MP4") is True

    def test_php_rejected(self):
        from web.upload_util import validate_video_extension
        assert validate_video_extension("shell.php") is False

    def test_html_rejected(self):
        from web.upload_util import validate_video_extension
        assert validate_video_extension("page.html") is False

    def test_exe_rejected(self):
        from web.upload_util import validate_video_extension
        assert validate_video_extension("virus.exe") is False

    def test_no_extension_rejected(self):
        from web.upload_util import validate_video_extension
        assert validate_video_extension("noext") is False

    def test_jpg_rejected(self):
        from web.upload_util import validate_video_extension
        assert validate_video_extension("photo.jpg") is False
