"""Pipeline 健壮性测试：

1. base64 编码时文件过大应拒绝
2. LLM 返回 None 时不应崩溃
3. JSON 解析应处理各种格式
4. 字幕截断应记录日志警告
"""
import json
import logging
import os

import pytest


# ── 1. base64 文件大小限制 ──

class TestBase64SizeLimit:
    """copywriting 的 _video_to_base64_url 应拒绝超大文件。"""

    def test_small_file_accepted(self, tmp_path):
        """小文件（<50MB）应正常编码。"""
        from pipeline.copywriting import _video_to_base64_url
        small_file = tmp_path / "small.mp4"
        small_file.write_bytes(b"\x00" * 1024)  # 1KB
        result = _video_to_base64_url(str(small_file))
        assert result.startswith("data:video/mp4;base64,")

    def test_oversized_file_rejected(self, tmp_path):
        """超大文件（>50MB）应抛出 ValueError。"""
        from pipeline.copywriting import _video_to_base64_url
        # 创建一个大文件（使用 seek 代替实际写入）
        big_file = tmp_path / "big.mp4"
        with open(big_file, "wb") as f:
            f.seek(51 * 1024 * 1024)  # 51MB
            f.write(b"\x00")
        with pytest.raises(ValueError, match="文件过大|too large|size"):
            _video_to_base64_url(str(big_file))

    def test_image_size_limit(self, tmp_path):
        """_image_to_base64_url 也应有大小限制。"""
        from pipeline.copywriting import _image_to_base64_url
        big_file = tmp_path / "big.jpg"
        with open(big_file, "wb") as f:
            f.seek(51 * 1024 * 1024)
            f.write(b"\x00")
        with pytest.raises(ValueError, match="文件过大|too large|size"):
            _image_to_base64_url(str(big_file))


# ── 2. LLM 返回 None 防护 ──

class TestLLMNoneGuard:
    """translate._parse_json_content 和 generate_localized_translation 处理 None。"""

    def test_parse_json_content_none_input(self):
        """None 输入不应抛 AttributeError。"""
        from pipeline.translate import _parse_json_content
        with pytest.raises((json.JSONDecodeError, TypeError, ValueError)):
            _parse_json_content(None)

    def test_parse_json_content_empty_string(self):
        """空字符串应抛合理的异常。"""
        from pipeline.translate import _parse_json_content
        with pytest.raises((json.JSONDecodeError, ValueError)):
            _parse_json_content("")

    def test_parse_json_content_valid_json(self):
        """正常 JSON 应正确解析。"""
        from pipeline.translate import _parse_json_content
        result = _parse_json_content('[{"index": 0, "translated": "hello"}]')
        assert isinstance(result, list)
        assert result[0]["translated"] == "hello"

    def test_parse_json_content_markdown_wrapped(self):
        """markdown 包裹的 JSON 应正确解析。"""
        from pipeline.translate import _parse_json_content
        raw = '```json\n[{"index": 0, "translated": "hello"}]\n```'
        result = _parse_json_content(raw)
        assert isinstance(result, list)


# ── 3. video_review JSON 解析 ──

class TestVideoReviewJsonParse:

    def test_normal_json(self):
        from pipeline.video_review import _parse_json_response
        data = {"scoring": {"total_score": 85}}
        result = _parse_json_response(json.dumps(data))
        assert result["scoring"]["total_score"] == 85

    def test_markdown_wrapped_json(self):
        from pipeline.video_review import _parse_json_response
        data = {"scoring": {"total_score": 75}}
        raw = f"```json\n{json.dumps(data)}\n```"
        result = _parse_json_response(raw)
        assert result["scoring"]["total_score"] == 75

    def test_garbage_returns_parse_error(self):
        from pipeline.video_review import _parse_json_response
        result = _parse_json_response("this is not json at all")
        assert result.get("_parse_error") is True

    def test_empty_string(self):
        from pipeline.video_review import _parse_json_response
        result = _parse_json_response("")
        assert result.get("_parse_error") is True


# ── 4. 字幕截断日志 ──

class TestSubtitleTruncationWarning:
    """当文本超出两行容量被截断时，应记录 warning 日志。"""

    def test_long_text_logs_warning(self, caplog):
        """超长文本应在日志中记录截断警告。"""
        from pipeline.subtitle import wrap_text
        # 构造一段超长文本（远超 2 行 × 42 字符 = 84 字符）
        long_text = " ".join(["superlongword"] * 20)  # ~260 字符

        with caplog.at_level(logging.WARNING, logger="pipeline.subtitle"):
            wrap_text(long_text)

        assert any("truncat" in r.message.lower() or "截断" in r.message for r in caplog.records), \
            "超长文本截断时应记录 warning 日志"

    def test_normal_text_no_warning(self, caplog):
        """正常长度文本不应记录截断警告。"""
        from pipeline.subtitle import wrap_text

        with caplog.at_level(logging.WARNING, logger="pipeline.subtitle"):
            wrap_text("Hello world, this is a short text")

        truncation_warnings = [r for r in caplog.records
                               if "truncat" in r.message.lower() or "截断" in r.message]
        assert len(truncation_warnings) == 0
