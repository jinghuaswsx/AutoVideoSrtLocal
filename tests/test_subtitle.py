from pipeline.subtitle import build_srt_from_manifest


def test_build_srt_from_manifest_uses_manifest_timing():
    manifest = {
        "segments": [
            {
                "index": 0,
                "translated": "hello there",
                "timeline_start": 0.0,
                "timeline_end": 1.25,
            },
            {
                "index": 1,
                "translated": "general kenobi",
                "timeline_start": 1.25,
                "timeline_end": 2.75,
            },
        ]
    }

    srt = build_srt_from_manifest(manifest)

    assert "00:00:00,000 --> 00:00:01,250" in srt
    assert "00:00:01,250 --> 00:00:02,750" in srt
    assert "Hello there" in srt
