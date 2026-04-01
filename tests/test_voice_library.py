import pytest
from unittest.mock import patch, MagicMock


def _mock_query(sql, args=()):
    """Mock appcore.db.query for voice library tests."""
    return []


def _mock_execute(sql, args=()):
    """Mock appcore.db.execute for voice library tests."""
    return 1


def _mock_query_one(sql, args=()):
    return None


@patch("pipeline.voice_library.db_query", _mock_query)
@patch("pipeline.voice_library.db_execute", _mock_execute)
@patch("pipeline.voice_library.db_query_one", _mock_query_one)
def test_ensure_defaults_inserts_for_new_user():
    from pipeline.voice_library import VoiceLibrary
    lib = VoiceLibrary()
    calls = []
    with patch("pipeline.voice_library.db_execute", side_effect=lambda sql, args=(): calls.append((sql, args)) or 1):
        with patch("pipeline.voice_library.db_query", return_value=[]):
            lib.ensure_defaults(user_id=99)
    assert len(calls) == 2  # Adam + Rachel
    assert any("pNInz6obpgDQGcFmaJgB" in str(c) for c in calls)
    assert any("21m00Tcm4TlvDq8ikWAM" in str(c) for c in calls)


@patch("pipeline.voice_library.db_query", _mock_query)
@patch("pipeline.voice_library.db_execute", _mock_execute)
@patch("pipeline.voice_library.db_query_one", _mock_query_one)
def test_list_voices_filters_by_user_id():
    from pipeline.voice_library import VoiceLibrary
    lib = VoiceLibrary()
    captured = []
    def mock_q(sql, args=()):
        captured.append((sql, args))
        return [{"id": 1, "name": "Test", "gender": "male", "elevenlabs_voice_id": "abc"}]
    with patch("pipeline.voice_library.db_query", mock_q):
        result = lib.list_voices(user_id=5)
    assert len(result) == 1
    assert "user_id" in captured[0][0]
    assert captured[0][1] == (5,)
