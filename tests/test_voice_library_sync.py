from unittest.mock import patch, MagicMock
from pipeline.voice_library_sync import fetch_shared_voices_page, sync_all_shared_voices


def test_fetch_shared_voices_page_returns_voices_and_next_token():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "voices": [
            {"voice_id": "v1", "name": "Rachel", "gender": "female",
             "language": "en", "preview_url": "http://a.mp3",
             "labels": {"accent": "american"}, "category": "professional"}
        ],
        "has_more": True,
        "next_page_token": "token-next",
    }
    with patch("pipeline.voice_library_sync.requests.get", return_value=mock_response):
        voices, next_token = fetch_shared_voices_page(
            api_key="dummy",
            page_size=100,
            next_page_token=None,
            language=None,
        )
    assert len(voices) == 1
    assert voices[0]["voice_id"] == "v1"
    assert next_token == "token-next"


def test_fetch_shared_voices_page_returns_none_when_no_more():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "voices": [],
        "has_more": False,
        "next_page_token": None,
    }
    with patch("pipeline.voice_library_sync.requests.get", return_value=mock_response):
        voices, next_token = fetch_shared_voices_page(api_key="dummy")
    assert voices == []
    assert next_token is None


def test_sync_all_iterates_pages_until_no_more():
    pages = [
        {"voices": [{"voice_id": "v1", "name": "A", "labels": {}}],
         "has_more": True, "next_page_token": "t2"},
        {"voices": [{"voice_id": "v2", "name": "B", "labels": {}}],
         "has_more": False, "next_page_token": None},
    ]
    call_count = {"i": 0}

    def fake_get(url, headers, params, timeout):
        i = call_count["i"]
        call_count["i"] += 1
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = pages[i]
        resp.raise_for_status = MagicMock()
        return resp

    stored = []
    with patch("pipeline.voice_library_sync.requests.get", side_effect=fake_get), \
         patch("pipeline.voice_library_sync.upsert_voice",
               side_effect=lambda v: stored.append(v)):
        total = sync_all_shared_voices(api_key="dummy")
    assert total == 2
    assert [v["voice_id"] for v in stored] == ["v1", "v2"]


def test_sync_all_forwards_language_filter():
    seen_params = {}
    def fake_get(url, headers, params, timeout):
        seen_params.update(params)
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"voices": [], "has_more": False,
                                   "next_page_token": None}
        resp.raise_for_status = MagicMock()
        return resp

    with patch("pipeline.voice_library_sync.requests.get", side_effect=fake_get), \
         patch("pipeline.voice_library_sync.upsert_voice"):
        sync_all_shared_voices(api_key="dummy", language="en", gender="female")
    assert seen_params.get("language") == "en"
    assert seen_params.get("gender") == "female"
