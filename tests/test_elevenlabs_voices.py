from pipeline.elevenlabs_voices import _map_shared_voice_to_local


def test_map_shared_voice_to_local_respects_language_override():
    shared = {
        "voice_id": "NxfO5zydfqwpYnWQJ7jJ",
        "name": "Rin",
        "description": "Japanese female voice",
        "labels": {"gender": "female"},
    }

    result = _map_shared_voice_to_local(shared, {"language": "ja", "gender": "female"})

    assert result["language"] == "ja"
    assert result["gender"] == "female"
    assert result["elevenlabs_voice_id"] == "NxfO5zydfqwpYnWQJ7jJ"
