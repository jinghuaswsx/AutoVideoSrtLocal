from __future__ import annotations

import requests


def test_download_image_to_local_media_fetches_image_and_writes_object():
    from web.services.media_image_import import download_image_to_local_media

    captured = {}
    writes = []

    class FakeResponse:
        headers = {"content-type": "image/gif; charset=utf-8"}

        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def iter_content(chunk_size):
            captured["chunk_size"] = chunk_size
            yield b"GIF89a"
            yield b"-bytes"

    def fake_get(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return FakeResponse()

    result = download_image_to_local_media(
        "https://cdn.example.com/path/x.gif",
        99,
        "from_url_en_00",
        user_id=7,
        resolve_upload_user_id_fn=lambda user_id=None: user_id,
        build_media_object_key_fn=lambda user_id, pid, filename: f"{user_id}/{pid}/{filename}",
        write_media_object_fn=lambda key, data: writes.append((key, data)),
        http_get_fn=fake_get,
        max_image_bytes=1024,
    )

    assert result == ("7/99/from_url_en_00_x.gif", b"GIF89a-bytes", ".gif")
    assert captured["url"] == "https://cdn.example.com/path/x.gif"
    assert captured["kwargs"]["timeout"] == 20
    assert captured["kwargs"]["stream"] is True
    assert captured["kwargs"]["headers"]["User-Agent"] == "Mozilla/5.0 AutoVideoSrt-Importer"
    assert captured["chunk_size"] == 64 * 1024
    assert writes == [("7/99/from_url_en_00_x.gif", b"GIF89a-bytes")]


def test_download_image_to_local_media_returns_readable_errors():
    from web.services.media_image_import import download_image_to_local_media

    common = {
        "pid": 99,
        "prefix": "cover",
        "user_id": 7,
        "resolve_upload_user_id_fn": lambda user_id=None: user_id,
        "build_media_object_key_fn": lambda *_args: "unused",
        "write_media_object_fn": lambda *_args: None,
        "max_image_bytes": 1024,
    }

    class NotImageResponse:
        headers = {"content-type": "text/html"}

        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def iter_content(chunk_size):
            del chunk_size
            yield b"<html>"

    not_image = download_image_to_local_media(
        "https://cdn.example.com/not-image",
        **common,
        http_get_fn=lambda *_args, **_kwargs: NotImageResponse(),
    )

    failed = download_image_to_local_media(
        "https://cdn.example.com/fail",
        **common,
        http_get_fn=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            requests.RequestException("timeout")
        ),
    )

    assert not_image == (None, None, "下载内容不是图片: text/html")
    assert failed == (None, None, "下载失败: timeout")
