from web.preview_artifacts import (
    build_alignment_artifact,
    build_asr_artifact,
    build_compose_artifact,
    build_export_artifact,
    build_extract_artifact,
    build_subtitle_artifact,
    build_translate_artifact,
    build_variant_compare_artifact,
    build_tts_artifact,
    build_shot_translate_artifact,
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


def test_build_variant_compare_artifact_contains_two_named_columns():
    artifact = build_variant_compare_artifact(
        title="翻译本土化",
        variants={
            "normal": {
                "label": "普通版",
                "items": [{"type": "text", "label": "整段英文", "content": "A"}],
            },
            "hook_cta": {
                "label": "黄金3秒 + CTA版",
                "items": [{"type": "text", "label": "整段英文", "content": "B"}],
            },
        },
    )

    assert artifact["layout"] == "variant_compare"
    assert artifact["variants"]["normal"]["label"] == "普通版"
    assert artifact["variants"]["hook_cta"]["label"] == "黄金3秒 + CTA版"


def test_shot_translate_artifact_uses_asr_translation_units_as_process_rows():
    shots = [
        {"index": 1, "start": 0.0, "end": 3.0, "description": "hook visual", "source_text": "old shot text"},
        {"index": 2, "start": 3.0, "end": 6.0, "description": "demo visual", "source_text": ""},
        {"index": 3, "start": 6.0, "end": 10.33, "description": "storage visual", "source_text": "old shot text 3"},
    ]
    translations = [
        {
            "shot_index": 0,
            "asr_index": 0,
            "source_text": "Opening hook keeps speaking",
            "start_time": 0.179,
            "end_time": 4.159,
            "duration": 3.98,
            "description": "hook visual / demo visual",
            "translated_text": "Gancho inicial",
            "char_limit": 40,
            "char_count": 14,
            "retries": 0,
            "shot_context": [{"index": 1}, {"index": 2}],
        },
        {
            "shot_index": 1,
            "asr_index": 1,
            "source_text": "Second ASR sentence continues",
            "start_time": 4.319,
            "end_time": 8.679,
            "duration": 4.36,
            "description": "demo visual / storage visual",
            "translated_text": "Segunda frase",
            "char_limit": 44,
            "char_count": 13,
            "retries": 1,
            "shot_context": [{"index": 2}, {"index": 3}],
        },
    ]

    artifact = build_shot_translate_artifact(
        shots,
        translations,
        "Opening hook keeps speaking\nSecond ASR sentence continues",
        {"full_text": "Gancho inicial\nSegunda frase"},
    )

    summary = artifact["items"][0]
    rows = artifact["items"][1]["shots"]
    assert summary["total"] == 2
    assert rows[0]["index"] == 0
    assert rows[0]["source_text"] == "Opening hook keeps speaking"
    assert rows[0]["description"] == "hook visual / demo visual"
    assert rows[0]["shot_context"] == [{"index": 1}, {"index": 2}]
    assert [row["source_text"] for row in rows] == [
        "Opening hook keeps speaking",
        "Second ASR sentence continues",
    ]


def test_build_tts_artifact_does_not_embed_duration_rounds():
    """duration_rounds 由前端 #ttsDurationLog 专用容器独立渲染，不再混入 items。"""
    rounds = [
        {"round": 1, "audio_duration": 35.0, "char_count": 400, "video_duration": 30.0,
         "duration_lo": 27.0, "duration_hi": 30.0,
         "artifact_paths": {"tts_script": "tts_script.round_1.json"}},
    ]
    artifact = build_tts_artifact(
        {"full_text": "hi", "blocks": [], "subtitle_chunks": []},
        [{"index": 0, "tts_path": "/x/y.mp3", "tts_duration": 1.0}],
        duration_rounds=rounds,
    )
    items = artifact["items"]
    duration_items = [it for it in items if it.get("type") == "tts_duration_rounds"]
    assert len(duration_items) == 0


def test_build_tts_artifact_without_duration_rounds_is_backward_compatible():
    artifact = build_tts_artifact(
        {"full_text": "hi", "blocks": [], "subtitle_chunks": []},
        [{"index": 0, "tts_path": "/x/y.mp3", "tts_duration": 1.0}],
    )
    items = artifact["items"]
    duration_items = [it for it in items if it.get("type") == "tts_duration_rounds"]
    assert len(duration_items) == 0
