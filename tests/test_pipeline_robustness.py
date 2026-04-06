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
    """translate.parse_json_content 和 generate_localized_translation 处理 None。"""

    def testparse_json_content_none_input(self):
        """None 输入不应抛 AttributeError。"""
        from pipeline.translate import parse_json_content
        with pytest.raises((json.JSONDecodeError, TypeError, ValueError)):
            parse_json_content(None)

    def testparse_json_content_empty_string(self):
        """空字符串应抛合理的异常。"""
        from pipeline.translate import parse_json_content
        with pytest.raises((json.JSONDecodeError, ValueError)):
            parse_json_content("")

    def testparse_json_content_valid_json(self):
        """正常 JSON 应正确解析。"""
        from pipeline.translate import parse_json_content
        result = parse_json_content('[{"index": 0, "translated": "hello"}]')
        assert isinstance(result, list)
        assert result[0]["translated"] == "hello"

    def testparse_json_content_markdown_wrapped(self):
        """markdown 包裹的 JSON 应正确解析。"""
        from pipeline.translate import parse_json_content
        raw = '```json\n[{"index": 0, "translated": "hello"}]\n```'
        result = parse_json_content(raw)
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


# ── 5. HTTP Range 请求健壮性 ──

class TestRangeRequest:
    """task._send_with_range 应安全处理异常 Range 头。"""

    @pytest.fixture
    def client_with_file(self, monkeypatch, tmp_path):
        """创建带测试文件的 client。"""
        fake_user = {"id": 1, "username": "test", "role": "admin", "is_active": 1}
        monkeypatch.setattr("web.auth.get_by_id", lambda uid: fake_user if int(uid) == 1 else None)

        # 创建测试文件
        test_file = tmp_path / "test.mp3"
        test_file.write_bytes(b"x" * 1000)

        from web.app import create_app
        app = create_app()
        c = app.test_client()
        with c.session_transaction() as sess:
            sess["_user_id"] = "1"
            sess["_fresh"] = True

        fake_task = {
            "_user_id": 1,
            "task_dir": str(tmp_path),
            "preview_files": {"audio_extract": str(test_file)},
        }
        monkeypatch.setattr("web.routes.task.store.get", lambda tid: fake_task)
        return c

    def test_valid_range_returns_206(self, client_with_file):
        resp = client_with_file.get(
            "/api/tasks/test-id/artifact/audio_extract",
            headers={"Range": "bytes=0-99"},
        )
        assert resp.status_code == 206
        assert resp.content_length == 100

    def test_inverted_range_returns_200(self, client_with_file):
        """start > end 应降级为完整 200 响应。"""
        resp = client_with_file.get(
            "/api/tasks/test-id/artifact/audio_extract",
            headers={"Range": "bytes=999-0"},
        )
        assert resp.status_code == 200
        assert resp.content_length == 1000

    def test_out_of_bounds_range_clamped(self, client_with_file):
        """超出文件大小的 end 应被夹紧。"""
        resp = client_with_file.get(
            "/api/tasks/test-id/artifact/audio_extract",
            headers={"Range": "bytes=0-99999"},
        )
        assert resp.status_code == 206
        assert resp.content_length == 1000

    def test_no_range_returns_200(self, client_with_file):
        resp = client_with_file.get("/api/tasks/test-id/artifact/audio_extract")
        assert resp.status_code == 200
        assert resp.content_length == 1000
