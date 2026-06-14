"""封面缩略图响应（product_cover_thumb_flask_response）单元测试。

根因背景：实时大盘产品列表一次渲染上百张封面原图（最大 7MB），经窄 SSH 隧道把连接和
带宽占满、连数据请求一起拖垮（2026-06-14 点击切换 20s 出不来数据）。缩略图把封面压到
最长边 240px。
"""
from pathlib import Path

from PIL import Image

from web.services import media_covers
from web.services.media_covers import ProductCoverFileResponse


def test_thumb_resizes_and_caches(monkeypatch, tmp_path):
    orig = tmp_path / "cover_en.jpg"
    Image.new("RGB", (2000, 2000), (10, 20, 30)).save(orig, "JPEG", quality=95)
    sent = {}
    monkeypatch.setattr(
        media_covers, "send_file",
        lambda path, mimetype=None: sent.update(path=str(path), mimetype=mimetype) or "RESP",
    )
    result = ProductCoverFileResponse(local_path=orig, mimetype="image/jpeg")

    media_covers.product_cover_thumb_flask_response(result)

    thumb = orig.with_name("cover_en_thumb.jpg")
    assert thumb.exists()                              # 缩略图已生成
    assert thumb.stat().st_size < orig.stat().st_size // 5   # 远小于原图
    with Image.open(thumb) as im:
        assert max(im.size) <= 240                     # 最长边 240px
    assert sent["path"] == str(thumb)                  # 返回的是缩略图，不是原图
    assert sent["mimetype"] == "image/jpeg"


def test_thumb_reuses_cache(monkeypatch, tmp_path):
    orig = tmp_path / "cover_en.jpg"
    Image.new("RGB", (800, 800), (5, 5, 5)).save(orig, "JPEG")
    result = ProductCoverFileResponse(local_path=orig, mimetype="image/jpeg")
    monkeypatch.setattr(media_covers, "send_file", lambda path, mimetype=None: None)

    media_covers.product_cover_thumb_flask_response(result)
    thumb = orig.with_name("cover_en_thumb.jpg")
    mtime1 = thumb.stat().st_mtime_ns

    # 第二次调用：原图未变 → 复用缓存、不重新生成
    media_covers.product_cover_thumb_flask_response(result)
    assert thumb.stat().st_mtime_ns == mtime1


def test_thumb_falls_back_to_original_on_error(monkeypatch, tmp_path):
    bad = tmp_path / "cover_en.jpg"
    bad.write_text("not an image")                     # 损坏文件 → resize 抛异常
    sent = {}
    monkeypatch.setattr(
        media_covers, "send_file",
        lambda path, mimetype=None: sent.update(path=str(path), mimetype=mimetype) or "RESP",
    )
    result = ProductCoverFileResponse(local_path=bad, mimetype="image/jpeg")

    media_covers.product_cover_thumb_flask_response(result)

    assert sent["path"] == str(bad)                    # 回退原图，绝不让封面整列裂掉
    assert sent["mimetype"] == "image/jpeg"
