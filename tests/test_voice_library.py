from pathlib import Path

from pipeline.voice_library import VoiceLibrary


def test_voice_library_crud_round_trip(tmp_path):
    voice_file = tmp_path / "voices.json"
    lib = VoiceLibrary(Path(voice_file))

    created = lib.create_voice(
        {
            "name": "Alex",
            "gender": "male",
            "elevenlabs_voice_id": "voice_123",
            "description": "Test voice",
            "style_tags": ["tech", "clear"],
        }
    )

    assert created["id"] == "alex"

    updated = lib.update_voice(
        "alex",
        {
            "description": "Updated",
            "style_tags": ["tech"],
            "is_default_male": True,
        },
    )

    assert updated["description"] == "Updated"
    assert lib.list_voices()[0]["is_default_male"] is True

    lib.delete_voice("alex")

    assert lib.list_voices() == []


def test_voice_library_recommendation_prefers_beauty_friendly_voice(tmp_path):
    voice_file = tmp_path / "voices.json"
    lib = VoiceLibrary(Path(voice_file))
    lib.create_voice(
        {
            "name": "Adam",
            "gender": "male",
            "elevenlabs_voice_id": "male_voice",
            "description": "Energetic male for gadgets",
            "style_tags": ["energetic", "tech"],
            "is_default_male": True,
        }
    )
    lib.create_voice(
        {
            "name": "Rachel",
            "gender": "female",
            "elevenlabs_voice_id": "female_voice",
            "description": "Warm female for skincare",
            "style_tags": ["warm", "beauty"],
            "is_default_female": True,
        }
    )

    recommended = lib.recommend_voice("这款精华和面霜太绝了，妆前用起来很服帖")

    assert recommended["name"] == "Rachel"
