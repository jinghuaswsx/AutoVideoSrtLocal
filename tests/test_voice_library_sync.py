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


import numpy as np


def test_embed_missing_voices_downloads_and_stores(tmp_path):
    voices_needing_embed = [
        {"voice_id": "v1", "preview_url": "http://a.mp3"},
        {"voice_id": "v2", "preview_url": "http://b.mp3"},
    ]

    saved = {}

    def fake_download(url, dest):
        dest.write_bytes(b"x")
        return str(dest)

    def fake_embed(path):
        return np.full(256, 0.5, dtype=np.float32)

    def fake_update(voice_id, blob):
        saved[voice_id] = blob

    with patch("pipeline.voice_library_sync._list_voices_without_embedding",
               return_value=voices_needing_embed), \
         patch("pipeline.voice_library_sync._download_preview",
               side_effect=fake_download), \
         patch("pipeline.voice_library_sync.embed_audio_file",
               side_effect=fake_embed), \
         patch("pipeline.voice_library_sync._update_embedding",
               side_effect=fake_update):
        from pipeline.voice_library_sync import embed_missing_voices
        count = embed_missing_voices(cache_dir=str(tmp_path))
    assert count == 2
    assert set(saved.keys()) == {"v1", "v2"}


def test_embed_missing_voices_skips_voice_without_preview_url(tmp_path):
    voices = [
        {"voice_id": "v1", "preview_url": None},
        {"voice_id": "v2", "preview_url": "http://b.mp3"},
    ]
    saved = {}
    with patch("pipeline.voice_library_sync._list_voices_without_embedding",
               return_value=voices), \
         patch("pipeline.voice_library_sync._download_preview",
               side_effect=lambda u, d: d.write_bytes(b"x") or str(d)), \
         patch("pipeline.voice_library_sync.embed_audio_file",
               return_value=np.zeros(256, dtype=np.float32)), \
         patch("pipeline.voice_library_sync._update_embedding",
               side_effect=lambda vid, blob: saved.setdefault(vid, blob)):
        from pipeline.voice_library_sync import embed_missing_voices
        count = embed_missing_voices(cache_dir=str(tmp_path))
    assert count == 1
    assert saved == {"v2": saved["v2"]}


def test_embed_missing_voices_continues_on_single_failure(tmp_path):
    voices = [
        {"voice_id": "bad", "preview_url": "http://bad.mp3"},
        {"voice_id": "good", "preview_url": "http://good.mp3"},
    ]
    saved = {}
    def fake_download(url, dest):
        if "bad" in url:
            raise RuntimeError("download failed")
        dest.write_bytes(b"x")
        return str(dest)
    with patch("pipeline.voice_library_sync._list_voices_without_embedding",
               return_value=voices), \
         patch("pipeline.voice_library_sync._download_preview",
               side_effect=fake_download), \
         patch("pipeline.voice_library_sync.embed_audio_file",
               return_value=np.full(256, 0.3, dtype=np.float32)), \
         patch("pipeline.voice_library_sync._update_embedding",
               side_effect=lambda vid, blob: saved.update({vid: blob})):
        from pipeline.voice_library_sync import embed_missing_voices
        count = embed_missing_voices(cache_dir=str(tmp_path))
    assert count == 1
    assert "good" in saved
    assert "bad" not in saved
