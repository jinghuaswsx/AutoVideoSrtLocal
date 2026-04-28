from scripts.backfill_usage_payload_network_estimate import enrich_request_data


def test_backfill_usage_payload_network_estimate_counts_media_files(tmp_path):
    image = tmp_path / "cover.jpg"
    video = tmp_path / "clip.mp4"
    image.write_bytes(b"a" * 3)
    video.write_bytes(b"b" * 5)

    payload, changed, missing = enrich_request_data(
        {
            "prompt": "evaluate",
            "inputs": {
                "image_path": str(image),
                "video_path": str(video),
            },
        },
        provider="openrouter",
    )

    assert changed is True
    assert missing == 0
    assert payload["network_route_intent"] == "proxy_required"
    assert payload["network_estimate"]["total_media_bytes"] == 8
    assert payload["network_estimate"]["estimated_base64_payload_bytes"] == 12
    assert [item["bytes"] for item in payload["network_estimate"]["media"]] == [3, 5]


def test_backfill_usage_payload_network_estimate_skips_missing_only_media(tmp_path):
    payload, changed, missing = enrich_request_data(
        {"media": [str(tmp_path / "missing.mp4")]},
        provider="doubao_asr",
    )

    assert changed is True
    assert missing == 1
    assert payload["network_route_intent"] == "direct_preferred"
    assert "network_estimate" not in payload
