from pathlib import Path


SCRIPT = Path("AutoPush/static/app.js").read_text(encoding="utf-8")


def test_autopush_static_app_sanitizes_preview_media_sources():
    payload_block = SCRIPT[
        SCRIPT.index("function renderPayloadView"):
        SCRIPT.index("function validatePayload")
    ]
    list_block = SCRIPT[
        SCRIPT.index("function renderBody()"):
        SCRIPT.index("// 产品 / 素材", SCRIPT.index("function renderBody()"))
    ]
    create_block = SCRIPT[
        SCRIPT.index("state.videos.forEach"):
        SCRIPT.index("async function doFetch")
    ]

    assert "function safeMediaSrc(url)" in SCRIPT
    assert "const coverSrc = safeMediaSrc(v.image_url);" in payload_block
    assert "const videoSrc = safeMediaSrc(v.url);" in payload_block
    assert "src: coverSrc" in payload_block
    assert "src: videoSrc" in payload_block
    assert "poster: coverSrc" in payload_block
    assert "const coverUrl = safeMediaSrc(item.cover_url);" in list_block
    assert "src: coverUrl" in list_block
    assert "const coverSrc = safeMediaSrc(v.image_url);" in create_block
    assert "const videoSrc = safeMediaSrc(v.url);" in create_block
    assert "poster: coverSrc" in create_block
    assert "src: v.url" not in create_block
