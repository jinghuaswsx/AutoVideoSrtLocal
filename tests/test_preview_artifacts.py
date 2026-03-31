from web.preview_artifacts import (
    build_alignment_artifact,
    build_asr_artifact,
    build_compose_artifact,
    build_export_artifact,
    build_extract_artifact,
    build_subtitle_artifact,
    build_translate_artifact,
    build_tts_artifact,
)


def test_preview_artifact_builders_cover_all_pipeline_steps():
    utterances = [
        {"text": "你好", "start_time": 0.0, "end_time": 0.8},
        {"text": "世界", "start_time": 0.8, "end_time": 1.6},
    ]
    segments = [
        {
            "text": "你好世界",
            "translated": "Hello world",
            "start_time": 0.0,
            "end_time": 1.6,
            "tts_duration": 1.8,
        }
    ]

    extract = build_extract_artifact()
    asr = build_asr_artifact(utterances)
    alignment = build_alignment_artifact([1.2], segments, [True, True])
    translate = build_translate_artifact(segments)
    tts = build_tts_artifact(segments)
    subtitle = build_subtitle_artifact("1\n00:00:00,000 --> 00:00:01,000\nHello\n")
    compose = build_compose_artifact()
    export = build_export_artifact('{"backend":"pyJianYingDraft"}')

    assert extract["items"][0]["artifact"] == "audio_extract"
    assert asr["items"][0]["type"] == "utterances"
    assert asr["items"][0]["utterances"][0]["text"] == "你好"
    assert alignment["items"][0]["type"] == "scene_cuts"
    assert alignment["items"][1]["segments"][0]["text"] == "你好世界"
    assert alignment["items"][1]["break_after"] == [True, True]
    assert translate["items"][0]["segments"][0]["translated"] == "Hello world"
    assert tts["items"][0]["artifact"] == "tts_full_audio"
    assert subtitle["items"][0]["content"].startswith("1\n00:00:00,000")
    assert compose["items"][0]["artifact"] == "soft_video"
    assert compose["items"][1]["artifact"] == "hard_video"
    assert export["items"][0]["type"] == "download"
    assert export["items"][1]["content"] == '{"backend":"pyJianYingDraft"}'
