from appcore.subtitle_preview_payload import build_product_preview_payload


def test_product_preview_prefers_first_english_video_with_source_raw():
    payload = build_product_preview_payload(
        product_id=123,
        items=[
            {
                "id": 9,
                "lang": "en",
                "object_key": "1/medias/123/en-final.mp4",
                "source_raw_id": 88,
            },
        ],
        raw_sources=[
            {"id": 88, "video_url": "/medias/raw-sources/88/video"},
            {"id": 89, "video_url": "/medias/raw-sources/89/video"},
        ],
        video_params={
            "subtitle_font": "Impact",
            "subtitle_size": 14,
            "subtitle_position_y": 0.88,
        },
    )

    assert payload["video_url"] == "/medias/object?object_key=1%2Fmedias%2F123%2Fen-final.mp4"
    assert payload["subtitle_font"] == "Impact"
    assert payload["subtitle_size"] == 14
    assert payload["subtitle_position_y"] == 0.88
    assert payload["sample_lines"] == [
        "Tiktok and facebook shot videos!",
        "Tiktok and facebook shot videos!",
    ]


def test_product_preview_falls_back_to_first_english_raw_source():
    payload = build_product_preview_payload(
        product_id=123,
        items=[],
        raw_sources=[
            {"id": 88, "video_url": "/medias/raw-sources/88/video"},
            {"id": 89, "video_url": "/medias/raw-sources/89/video"},
        ],
        video_params={
            "subtitle_font": "Anton",
            "subtitle_size": 18,
            "subtitle_position_y": 0.72,
        },
    )

    assert payload["video_url"] == "/medias/raw-sources/88/video"
    assert payload["subtitle_font"] == "Anton"
    assert payload["subtitle_size"] == 18
    assert payload["subtitle_position_y"] == 0.72
