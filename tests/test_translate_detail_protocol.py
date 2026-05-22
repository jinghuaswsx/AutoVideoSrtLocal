from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from appcore.voice_ai_rank_cache import candidate_signature
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


def test_build_voice_library_payload_includes_voice_ai_rankings():
    with patch(
        "web.services.translate_detail_protocol.is_voice_ai_auto_select_enabled",
        return_value=False,
    ):
        payload = build_voice_library_payload(
            state={
                "target_lang": "de",
                "steps": {"voice_match": "waiting"},
                "voice_match_candidates": [
                    {"voice_id": "v1", "similarity": 0.91, "llm_rank": 2, "llm_reason_summary": "slightly flat"},
                ],
                "voice_ai_rankings": [
                    {"voice_id": "v1", "llm_rank": 2, "reason_summary": "slightly flat"},
                ],
                "voice_ai_rank_status": "done",
                "voice_ai_rank_usage_log_id": 34567,
                "voice_ai_rank_debug": {
                    "request": {"raw": {"model": "google/gemini-3.5-flash"}},
                    "result": {"raw": {"rankings": []}},
                },
            },
            owner_user_id=1,
            items=[{"voice_id": "v1", "name": "A"}],
            total=1,
        )

    assert payload["voice_ai_rankings"] == [
        {"voice_id": "v1", "llm_rank": 2, "reason_summary": "slightly flat"},
    ]
    assert payload["voice_ai_rank_status"] == "done"
    assert payload["voice_ai_rank_usage_log_id"] == 34567
    assert payload["voice_ai_auto_select_enabled"] is False
    assert payload["voice_ai_rank_debug"]["request"]["raw"]["model"] == "google/gemini-3.5-flash"
    assert payload["candidates"][0]["llm_rank"] == 2


def test_build_voice_library_payload_marks_legacy_running_ai_rank_as_interrupted():
    candidates = [{"voice_id": "v1", "similarity": 0.91}]
    state = {
        "target_lang": "es",
        "steps": {"voice_match": "waiting"},
        "voice_match_candidates": candidates,
        "voice_ai_rank_status": "running",
        "voice_ai_rank_candidate_signature": candidate_signature(candidates),
        "voice_ai_rank_usage_log_id": 74035,
    }

    payload = build_voice_library_payload(
        state=state,
        owner_user_id=1,
        items=[{"voice_id": "v1", "name": "A"}],
        total=1,
    )

    assert payload["voice_ai_rank_status"] == "interrupted"
    assert payload["voice_ai_rankings"] == []
    assert payload["voice_ai_rank_usage_log_id"] is None
    assert state["voice_ai_rank_status"] == "interrupted"
    assert payload["voice_ai_rank_recovery"]["start_step"] == "voice_match"
    assert payload["voice_ai_rank_recovery"]["actions"] == [
        "rerun_voice_ai_ranking",
        "force_speed_match",
        "rerun_voice_match_step",
    ]


def test_build_voice_library_payload_marks_old_running_ai_rank_as_interrupted():
    candidates = [{"voice_id": "v1", "similarity": 0.91}]
    state = {
        "target_lang": "es",
        "steps": {"voice_match": "waiting"},
        "voice_match_candidates": candidates,
        "voice_ai_rank_status": "running",
        "voice_ai_rank_started_at": (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat(),
        "voice_ai_rank_candidate_signature": candidate_signature(candidates),
    }

    payload = build_voice_library_payload(
        state=state,
        owner_user_id=1,
        items=[{"voice_id": "v1", "name": "A"}],
        total=1,
    )

    assert payload["voice_ai_rank_status"] == "interrupted"
    assert payload["voice_ai_rank_debug"]["status"] == "interrupted"


def test_build_voice_library_payload_keeps_fresh_running_ai_rank_pollable():
    candidates = [{"voice_id": "v1", "similarity": 0.91}]
    state = {
        "target_lang": "es",
        "steps": {"voice_match": "waiting"},
        "voice_match_candidates": candidates,
        "voice_ai_rank_status": "running",
        "voice_ai_rank_started_at": datetime.now(timezone.utc).isoformat(),
        "voice_ai_rank_candidate_signature": candidate_signature(candidates),
    }

    payload = build_voice_library_payload(
        state=state,
        owner_user_id=1,
        items=[{"voice_id": "v1", "name": "A"}],
        total=1,
    )

    assert payload["voice_ai_rank_status"] == "running"
    assert payload["voice_ai_rank_recovery"] is None


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
