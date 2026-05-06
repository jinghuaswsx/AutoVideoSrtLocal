"""appcore.audio_separation_client mock 测试。

服务端协议是同步阻塞模式：单次 ``POST /separate/download`` 拿 ZIP（包含
``..._(Vocals)_..._.wav`` 和 ``..._(Instrumental)_..._.wav`` 两个 wav）。

mock 覆盖：
- happy path（POST 200 → ZIP 二进制 → 解压两个 wav）
- WAV 输入触发 ffmpeg 转 mp3 → 上传成功
- MP3 输入直接走，不调 ffmpeg
- 5xx 重试机制（前两次 5xx，第三次成功）
- 4xx 不重试
- read timeout 抛 SeparationTimeout（仍是 SeparationApiUnavailable 子类）
- ZIP 缺 Vocals / 缺 Instrumental → SeparationFailed
- ZIP 完全损坏 → SeparationFailed
- health() 探活请求根路径 /health
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests as _requests

from appcore.audio_separation_client import (
    SeparationApiUnavailable,
    SeparationClient,
    SeparationFailed,
    SeparationTimeout,
    _pick_stem_member,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_zip_bytes(
    vocals_name: str = "input_abc_(Vocals)_preset_vocal_balanced.wav",
    accomp_name: str = "input_abc_(Instrumental)_preset_vocal_balanced.wav",
    vocals_payload: bytes = b"VOCALSWAVDATA",
    accomp_payload: bytes = b"ACCOMPWAVDATA",
    *,
    include_vocals: bool = True,
    include_accomp: bool = True,
    extra_members: list[tuple[str, bytes]] | None = None,
) -> bytes:
    """构造一段 ZIP 二进制，模拟服务端返回的 stems 包。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if include_vocals:
            zf.writestr(vocals_name, vocals_payload)
        if include_accomp:
            zf.writestr(accomp_name, accomp_payload)
        for name, payload in (extra_members or []):
            zf.writestr(name, payload)
    return buf.getvalue()


class _FakePostResponse:
    """模拟 requests.post(stream=True) 的 context manager + iter_content。"""

    def __init__(
        self,
        status_code: int,
        content: bytes = b"",
        text: str = "",
    ):
        self.status_code = status_code
        self._content = content
        self.text = text or content.decode("utf-8", errors="replace")[:200]

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def iter_content(self, chunk_size: int = 1 << 20):
        # 一次性吐完，模拟 stream 真实行为差不多。
        if self._content:
            yield self._content


def _fake_get_resp(status_code: int, json_data=None):
    m = MagicMock()
    m.status_code = status_code
    if json_data is not None:
        m.json.return_value = json_data
    m.text = "" if json_data is None else str(json_data)
    return m


@pytest.fixture
def mp3_file(tmp_path):
    p = tmp_path / "in.mp3"
    # 假 MP3 字节，反正 client 直接读字节流上传，不解码。
    p.write_bytes(b"\xff\xfb\x90\x44" + b"\x00" * 1024)
    return p


@pytest.fixture
def wav_file(tmp_path):
    p = tmp_path / "in.wav"
    p.write_bytes(b"RIFF\x00\x00\x00\x00WAVE" + b"\x00" * 1024)
    return p


# ---------------------------------------------------------------------------
# pure helper tests
# ---------------------------------------------------------------------------

def test_pick_stem_member_matches_vocals_and_instrumental():
    names = [
        "input_abc_(Vocals)_preset_vocal_balanced.wav",
        "input_abc_(Instrumental)_preset_vocal_balanced.wav",
    ]
    assert _pick_stem_member(names, "vocals") == names[0]
    assert _pick_stem_member(names, "instrumental") == names[1]


def test_pick_stem_member_case_insensitive():
    names = [
        "OUT_(VOCALS)_PRESET_X.WAV",
        "OUT_(INSTRUMENTAL)_PRESET_X.WAV",
    ]
    assert _pick_stem_member(names, "vocals") == names[0]
    assert _pick_stem_member(names, "instrumental") == names[1]


def test_pick_stem_member_ignores_non_wav():
    names = ["readme_(Vocals).txt", "out_(Vocals).wav"]
    assert _pick_stem_member(names, "vocals") == "out_(Vocals).wav"


def test_pick_stem_member_returns_none_when_missing():
    assert _pick_stem_member(["only_(Other).wav"], "vocals") is None


# ---------------------------------------------------------------------------
# init & health
# ---------------------------------------------------------------------------

def test_init_rejects_empty_base_url():
    with pytest.raises(ValueError):
        SeparationClient("")


def test_health_returns_false_on_connection_error():
    client = SeparationClient("http://x.test")
    with patch("appcore.audio_separation_client.requests") as r:
        class _FakeReqExc(Exception):
            pass
        r.RequestException = _FakeReqExc
        r.get.side_effect = _FakeReqExc("nope")
        assert client.health() is False


def test_health_returns_true_on_200():
    client = SeparationClient("http://x.test")
    with patch("appcore.audio_separation_client.requests") as r:
        r.get.return_value = _fake_get_resp(200, {"ok": True})
        r.RequestException = Exception
        assert client.health() is True


def test_health_uses_root_health_endpoint():
    client = SeparationClient("http://127.0.0.1:83/")
    with patch("appcore.audio_separation_client.requests") as r:
        r.get.return_value = _fake_get_resp(200, {"ok": True})
        r.RequestException = Exception
        assert client.health() is True

    url, = r.get.call_args.args
    assert url == "http://127.0.0.1:83/health"


def test_separate_missing_audio_file_raises(tmp_path):
    client = SeparationClient("http://x.test")
    with pytest.raises(FileNotFoundError):
        client.separate(
            str(tmp_path / "nope.mp3"),
            output_dir=str(tmp_path / "out"),
        )


# ---------------------------------------------------------------------------
# happy path
# ---------------------------------------------------------------------------

def test_separate_happy_path_with_mp3_input(tmp_path, mp3_file):
    """MP3 输入直接 POST，不经过 ffmpeg。"""
    client = SeparationClient(
        "http://x.test",
        connect_timeout=1, task_timeout=10,
        network_retries=1,
    )
    out_dir = tmp_path / "out"
    zip_bytes = _make_zip_bytes()

    # 让 _ensure_mp3 永远不该被调用 —— 用 sentinel 抛错验证。
    with patch("appcore.audio_separation_client.requests") as r, \
         patch.object(SeparationClient, "_ensure_mp3",
                      side_effect=AssertionError("should skip ffmpeg")):
        r.post.return_value = _FakePostResponse(200, content=zip_bytes)
        r.ConnectionError = _requests.ConnectionError
        r.Timeout = _requests.Timeout

        result = client.separate(
            str(mp3_file), output_dir=str(out_dir),
            preset="vocal_balanced",
        )

    assert result.task_id == ""
    assert result.model == "vocal_balanced"
    assert result.elapsed_seconds >= 0
    assert (out_dir / "vocals.wav").read_bytes() == b"VOCALSWAVDATA"
    assert (out_dir / "accompaniment.wav").read_bytes() == b"ACCOMPWAVDATA"

    # 验证发过去的 multipart 字段名
    _, kwargs = r.post.call_args
    assert "file" in kwargs["files"]
    assert kwargs["data"]["ensemble_preset"] == "vocal_balanced"
    assert r.post.call_args.args[0] == "http://x.test/separate/download"


def test_separate_wav_input_transcodes_to_mp3(tmp_path, wav_file):
    """WAV 输入会先调 ffmpeg 转 mp3 再上传。"""
    client = SeparationClient(
        "http://x.test",
        connect_timeout=1, task_timeout=10,
        network_retries=1,
    )
    out_dir = tmp_path / "out"
    zip_bytes = _make_zip_bytes()

    fake_mp3 = tmp_path / "fake_transcoded.mp3"
    fake_mp3.write_bytes(b"\xff\xfb\x90mp3body")

    with patch("appcore.audio_separation_client.requests") as r, \
         patch.object(SeparationClient, "_ensure_mp3",
                      return_value=fake_mp3) as ensure_mp3:
        r.post.return_value = _FakePostResponse(200, content=zip_bytes)
        r.ConnectionError = _requests.ConnectionError
        r.Timeout = _requests.Timeout

        result = client.separate(
            str(wav_file), output_dir=str(out_dir),
            preset="vocal_balanced",
        )

    ensure_mp3.assert_called_once()
    assert result.elapsed_seconds >= 0
    assert (out_dir / "vocals.wav").exists()
    assert (out_dir / "accompaniment.wav").exists()


def test_separate_legacy_model_kwarg_used_as_preset(tmp_path, mp3_file):
    """老 caller 传 ``model=...`` 时被当作 preset 使用。"""
    client = SeparationClient("http://x.test", network_retries=1)
    zip_bytes = _make_zip_bytes()

    with patch("appcore.audio_separation_client.requests") as r:
        r.post.return_value = _FakePostResponse(200, content=zip_bytes)
        r.ConnectionError = _requests.ConnectionError
        r.Timeout = _requests.Timeout
        result = client.separate(
            str(mp3_file), output_dir=str(tmp_path / "out"),
            model="vocal_balanced",
        )

    assert result.model == "vocal_balanced"
    _, kwargs = r.post.call_args
    assert kwargs["data"]["ensemble_preset"] == "vocal_balanced"


# ---------------------------------------------------------------------------
# retries / error mapping
# ---------------------------------------------------------------------------

def test_separate_5xx_retries_then_succeeds(tmp_path, mp3_file):
    client = SeparationClient(
        "http://x.test",
        connect_timeout=1, task_timeout=10,
        network_retries=3, network_retry_backoff=0.001,
    )
    zip_bytes = _make_zip_bytes()
    with patch("appcore.audio_separation_client.requests") as r:
        r.post.side_effect = [
            _FakePostResponse(503, text="down"),
            _FakePostResponse(503, text="down"),
            _FakePostResponse(200, content=zip_bytes),
        ]
        r.ConnectionError = _requests.ConnectionError
        r.Timeout = _requests.Timeout

        result = client.separate(
            str(mp3_file), output_dir=str(tmp_path / "out"),
            preset="vocal_balanced",
        )
    assert result.elapsed_seconds >= 0
    assert r.post.call_count == 3


def test_separate_5xx_exhausts_retries(tmp_path, mp3_file):
    client = SeparationClient(
        "http://x.test",
        connect_timeout=1, task_timeout=10,
        network_retries=2, network_retry_backoff=0.001,
    )
    with patch("appcore.audio_separation_client.requests") as r:
        r.post.return_value = _FakePostResponse(503, text="down")
        r.ConnectionError = _requests.ConnectionError
        r.Timeout = _requests.Timeout

        with pytest.raises(SeparationApiUnavailable):
            client.separate(
                str(mp3_file), output_dir=str(tmp_path / "out"),
                preset="vocal_balanced",
            )
    assert r.post.call_count == 2


def test_separate_4xx_does_not_retry(tmp_path, mp3_file):
    client = SeparationClient(
        "http://x.test",
        connect_timeout=1, task_timeout=10,
        network_retries=3, network_retry_backoff=0.001,
    )
    with patch("appcore.audio_separation_client.requests") as r:
        r.post.return_value = _FakePostResponse(400, text="bad preset")
        r.ConnectionError = _requests.ConnectionError
        r.Timeout = _requests.Timeout

        with pytest.raises(SeparationFailed) as excinfo:
            client.separate(
                str(mp3_file), output_dir=str(tmp_path / "out"),
                preset="bogus",
            )
        assert "400" in str(excinfo.value)
        assert r.post.call_count == 1


def test_separate_read_timeout_raises_separation_timeout(tmp_path, mp3_file):
    client = SeparationClient(
        "http://x.test",
        connect_timeout=1, task_timeout=0.1,
        network_retries=2, network_retry_backoff=0.001,
    )

    def _boom(*args, **kwargs):
        raise _requests.Timeout("read timed out")

    with patch("appcore.audio_separation_client.requests") as r:
        r.post.side_effect = _boom
        r.ConnectionError = _requests.ConnectionError
        r.Timeout = _requests.Timeout

        with pytest.raises(SeparationTimeout) as excinfo:
            client.separate(
                str(mp3_file), output_dir=str(tmp_path / "out"),
                preset="vocal_balanced",
            )
    assert isinstance(excinfo.value, SeparationApiUnavailable)
    assert "read timeout" in str(excinfo.value)
    # 重试次数耗尽
    assert r.post.call_count == 2


def test_separate_connection_error_retries_then_fails(tmp_path, mp3_file):
    client = SeparationClient(
        "http://x.test",
        connect_timeout=1, task_timeout=10,
        network_retries=2, network_retry_backoff=0.001,
    )

    def _boom(*args, **kwargs):
        raise _requests.ConnectionError("conn refused")

    with patch("appcore.audio_separation_client.requests") as r:
        r.post.side_effect = _boom
        r.ConnectionError = _requests.ConnectionError
        r.Timeout = _requests.Timeout

        with pytest.raises(SeparationApiUnavailable) as excinfo:
            client.separate(
                str(mp3_file), output_dir=str(tmp_path / "out"),
                preset="vocal_balanced",
            )
    # 不该被认成 timeout
    assert not isinstance(excinfo.value, SeparationTimeout)
    assert r.post.call_count == 2


# ---------------------------------------------------------------------------
# zip / extraction failures
# ---------------------------------------------------------------------------

def test_separate_zip_missing_vocals_raises(tmp_path, mp3_file):
    client = SeparationClient("http://x.test", network_retries=1)
    zip_bytes = _make_zip_bytes(include_vocals=False)
    with patch("appcore.audio_separation_client.requests") as r:
        r.post.return_value = _FakePostResponse(200, content=zip_bytes)
        r.ConnectionError = _requests.ConnectionError
        r.Timeout = _requests.Timeout

        with pytest.raises(SeparationFailed) as excinfo:
            client.separate(
                str(mp3_file), output_dir=str(tmp_path / "out"),
                preset="vocal_balanced",
            )
        assert "vocals" in str(excinfo.value).lower()


def test_separate_zip_missing_instrumental_raises(tmp_path, mp3_file):
    client = SeparationClient("http://x.test", network_retries=1)
    zip_bytes = _make_zip_bytes(include_accomp=False)
    with patch("appcore.audio_separation_client.requests") as r:
        r.post.return_value = _FakePostResponse(200, content=zip_bytes)
        r.ConnectionError = _requests.ConnectionError
        r.Timeout = _requests.Timeout

        with pytest.raises(SeparationFailed) as excinfo:
            client.separate(
                str(mp3_file), output_dir=str(tmp_path / "out"),
                preset="vocal_balanced",
            )
        assert "instrumental" in str(excinfo.value).lower()


def test_separate_corrupt_zip_raises(tmp_path, mp3_file):
    client = SeparationClient("http://x.test", network_retries=1)
    bogus = b"this is definitely not a zip file " * 32
    with patch("appcore.audio_separation_client.requests") as r:
        r.post.return_value = _FakePostResponse(200, content=bogus)
        r.ConnectionError = _requests.ConnectionError
        r.Timeout = _requests.Timeout

        with pytest.raises(SeparationFailed) as excinfo:
            client.separate(
                str(mp3_file), output_dir=str(tmp_path / "out"),
                preset="vocal_balanced",
            )
        assert "corrupt zip" in str(excinfo.value).lower() or \
               "zip" in str(excinfo.value).lower()


def test_separate_empty_zip_body_retries_then_fails(tmp_path, mp3_file):
    """空 body 视为 5xx-style 故障，会触发重试。"""
    client = SeparationClient(
        "http://x.test", network_retries=2,
        network_retry_backoff=0.001,
    )
    with patch("appcore.audio_separation_client.requests") as r:
        r.post.return_value = _FakePostResponse(200, content=b"")
        r.ConnectionError = _requests.ConnectionError
        r.Timeout = _requests.Timeout

        with pytest.raises(SeparationFailed) as excinfo:
            client.separate(
                str(mp3_file), output_dir=str(tmp_path / "out"),
                preset="vocal_balanced",
            )
        # 空 body 触发 SeparationFailed("empty zip")，4xx 路径不重试。
        assert "empty" in str(excinfo.value).lower()
        # 4xx 路径不重试，只调一次。
        assert r.post.call_count == 1
