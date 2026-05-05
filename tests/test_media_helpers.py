from __future__ import annotations


def test_parse_lang_returns_readable_error(monkeypatch):
    from web.routes.medias import _helpers

    monkeypatch.setattr(_helpers.medias, "is_valid_language", lambda lang: False)

    assert _helpers._parse_lang({"lang": "xx"}) == (None, "unsupported language: xx")


def test_download_image_to_local_media_returns_readable_download_errors(monkeypatch):
    from requests import RequestException

    from web.routes.medias import _helpers

    class FakeResponse:
        headers = {"content-type": "text/html"}

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            yield b"<html>"

    monkeypatch.setattr(_helpers, "_resolve_upload_user_id", lambda user_id=None: 7)
    monkeypatch.setattr(_helpers.requests, "get", lambda *args, **kwargs: FakeResponse())

    not_image = _helpers._download_image_to_local_media(
        "https://example.test/not-image",
        123,
        "cover",
    )
    assert not_image == (None, None, "下载内容不是图片: text/html")

    monkeypatch.setattr(
        _helpers.requests,
        "get",
        lambda *args, **kwargs: (_ for _ in ()).throw(RequestException("timeout")),
    )
    failed = _helpers._download_image_to_local_media(
        "https://example.test/fail",
        123,
        "cover",
    )
    assert failed == (None, None, "下载失败: timeout")


def test_validate_product_code_returns_readable_errors():
    from web.routes.medias import _helpers

    missing_ok, missing_error = _helpers._validate_product_code("")
    invalid_ok, invalid_error = _helpers._validate_product_code("bad__-rjc")

    assert missing_ok is False
    assert missing_error == "产品 ID 不能为空"
    assert invalid_ok is False
    assert invalid_error == "产品 ID 只能使用小写字母、数字和连字符，长度 3-128，且首尾不能是连字符"
