from unittest.mock import patch

import pytest

from web.services.translate_detail_protocol import (
    build_voice_library_payload,
    lookup_default_voice_row,
    normalize_confirm_voice_payload,
    resolve_round_file_entry,
)


def test_build_voice_library_payload_marks_ready_only_for_waiting_or_done():
    payload = build_voice_library_payload(
        state={"target_lang": "ja", "steps": {"extract": "done", "asr": "done", "voice_match": "running"}},
        owner_user_id=7,
        items=[{"voice_id": "v1"}],
        total=1,
    )

    assert payload["voice_match_ready"] is False
    assert payload["pipeline"]["voice_match"] == "running"


def test_normalize_confirm_voice_payload_requires_explicit_voice_id():
    with pytest.raises(ValueError, match="no voice_id provided"):
        normalize_confirm_voice_payload(
            body={},
            lang="ja",
        )


def test_normalize_confirm_voice_payload_accepts_explicit_voice_id():
    normalized = normalize_confirm_voice_payload(
        body={"voice_id": "voice-chosen"},
        lang="ja",
    )

    assert normalized["voice_id"] == "voice-chosen"
    assert normalized["subtitle_font"] == "Impact"
    assert normalized["subtitle_size"] == 14
    assert normalized["subtitle_position_y"] == 0.68


def test_resolve_round_file_entry_rejects_unknown_kind():
    with pytest.raises(KeyError):
        resolve_round_file_entry({"localized_translation": ("x.json", "application/json")}, 1, "missing")


def test_build_voice_library_payload_merges_missing_candidates_into_items():
    state = {
        "target_lang": "es",
        "steps": {"voice_match": "waiting"},
        "voice_match_candidates": [
            {"voice_id": "in-list", "similarity": 0.7},
            {"voice_id": "missing-1", "similarity": 0.6},
            {"voice_id": "missing-2", "similarity": 0.55},
        ],
    }
    items = [{"voice_id": "in-list", "name": "Already"}]
    extra_rows = [
        {"voice_id": "missing-1", "name": "M1"},
        {"voice_id": "missing-2", "name": "M2"},
    ]

    with patch(
        "appcore.voice_library_browse.fetch_voices_by_ids",
        return_value=extra_rows,
    ) as m_fetch:
        payload = build_voice_library_payload(
            state=state,
            owner_user_id=1,
            items=items,
            total=1000,
        )

    m_fetch.assert_called_once_with(
        language="es",
        voice_ids=["missing-1", "missing-2"],
    )
    voice_ids_in_items = [it["voice_id"] for it in payload["items"]]
    assert voice_ids_in_items == ["in-list", "missing-1", "missing-2"]
    assert payload["total"] == 1000


def test_build_voice_library_payload_skips_merge_when_no_candidates():
    items = [{"voice_id": "v1"}]

    with patch(
        "appcore.voice_library_browse.fetch_voices_by_ids",
    ) as m_fetch:
        payload = build_voice_library_payload(
            state={"target_lang": "ja", "steps": {}},
            owner_user_id=1,
            items=items,
            total=1,
        )

    m_fetch.assert_not_called()
    assert payload["items"] == items


def test_lookup_default_voice_row_uses_appcore_voice_lookup():
    with patch(
        "web.services.translate_detail_protocol.resolve_default_voice",
        return_value="voice-default",
    ) as m_default, patch(
        "web.services.translate_detail_protocol.fetch_voice_by_id",
        return_value={"voice_id": "voice-default", "descriptive": "Warm"},
    ) as m_fetch:
        row = lookup_default_voice_row("de", 7)

    assert row == {
        "voice_id": "voice-default",
        "descriptive": "Warm",
        "description": "Warm",
    }
    m_default.assert_called_once_with("de", user_id=7)
    m_fetch.assert_called_once_with(language="de", voice_id="voice-default")
