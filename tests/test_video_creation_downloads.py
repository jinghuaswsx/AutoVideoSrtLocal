from appcore import video_creation_downloads


class _FakeDownloadResponse:
    def __init__(self, content: bytes):
        self.content = content
        self.raise_for_status_called = False

    def raise_for_status(self):
        self.raise_for_status_called = True


def test_download_generated_video_result_writes_generated_video(tmp_path, monkeypatch):
    captured = {}
    response = _FakeDownloadResponse(b"video-bytes")

    def fake_get(url, timeout):
        captured["url"] = url
        captured["timeout"] = timeout
        return response

    monkeypatch.setattr(video_creation_downloads.requests, "get", fake_get)

    result = video_creation_downloads.download_generated_video_result(
        "https://example.test/generated.mp4",
        str(tmp_path),
    )

    expected = tmp_path / "generated_video.mp4"
    assert result == str(expected)
    assert expected.read_bytes() == b"video-bytes"
    assert captured == {"url": "https://example.test/generated.mp4", "timeout": 120}
    assert response.raise_for_status_called is True


def test_download_generated_video_result_returns_none_without_url(tmp_path):
    result = video_creation_downloads.download_generated_video_result("", str(tmp_path))

    assert result is None
    assert not (tmp_path / "generated_video.mp4").exists()
