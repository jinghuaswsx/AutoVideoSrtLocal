from appcore import tos_clients


def test_build_media_raw_source_video_key_layout():
    key = tos_clients.build_media_raw_source_key(
        user_id=42,
        product_id=100,
        kind="video",
        filename="hello.mp4",
    )
    assert key.startswith("42/medias/100/raw_sources/")
    assert key.endswith(".mp4")
    assert "hello" in key


def test_build_media_raw_source_cover_key_layout():
    key = tos_clients.build_media_raw_source_key(
        user_id=42,
        product_id=100,
        kind="cover",
        filename="cover.png",
    )
    assert key.startswith("42/medias/100/raw_sources/")
    assert ".cover." in key or key.endswith(".cover.png")
    assert key.endswith(".png")
