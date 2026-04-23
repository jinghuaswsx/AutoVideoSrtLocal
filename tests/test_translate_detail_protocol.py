import pytest

from web.services.translate_detail_protocol import (
    build_voice_library_payload,
    normalize_confirm_voice_payload,
    resolve_round_file_entry,
)


def test_build_voice_library_payload_marks_ready_only_for_waiting_or_done():
    payload = build_voice_library_payload(
        state={"target_lang": "ja", "steps": {"extract": "done", "asr": "done", "voice_match": "running"}},
        owner_user_id=7,
        items=[{"voice_id": "v1"}],
        total=1,
        default_voice=None,
    )

    assert payload["voice_match_ready"] is False
    assert payload["pipeline"]["voice_match"] == "running"


def test_normalize_confirm_voice_payload_falls_back_to_defaults():
    normalized = normalize_confirm_voice_payload(
        body={},
        lang="ja",
        default_voice_id="voice-default",
    )

    assert normalized["voice_id"] == "voice-default"
    assert normalized["subtitle_font"] == "Impact"
    assert normalized["subtitle_size"] == 14
    assert normalized["subtitle_position_y"] == 0.68


def test_resolve_round_file_entry_rejects_unknown_kind():
    with pytest.raises(KeyError):
        resolve_round_file_entry({"localized_translation": ("x.json", "application/json")}, 1, "missing")
