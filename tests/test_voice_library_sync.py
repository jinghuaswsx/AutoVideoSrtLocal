from unittest.mock import patch, MagicMock
from pipeline.voice_library_sync import fetch_shared_voices_page, sync_all_shared_voices


def test_fetch_shared_voices_page_uses_page_param():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "voices": [
            {"voice_id": "v1", "name": "Rachel", "gender": "female",
             "language": "en", "preview_url": "http://a.mp3",
             "use_case": "news", "category": "professional"}
        ],
        "has_more": True,
        "total_count": 6308,
    }
    with patch("pipeline.voice_library_sync.requests.get",
               return_value=mock_response) as getter:
        voices, has_more, total_count = fetch_shared_voices_page(
            api_key="dummy", page=2, page_size=100, language="en",
        )
    call_kwargs = getter.call_args.kwargs
    assert call_kwargs["params"]["page"] == 2
    assert call_kwargs["params"]["page_size"] == 100
    assert call_kwargs["params"]["language"] == "en"
    assert voices[0]["voice_id"] == "v1"
    assert has_more is True
    assert total_count == 6308


def test_fetch_shared_voices_page_stops_when_no_more():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "voices": [],
        "has_more": False,
        "total_count": 100,
    }
    with patch("pipeline.voice_library_sync.requests.get",
               return_value=mock_response):
        voices, has_more, total_count = fetch_shared_voices_page(
            api_key="dummy", page=4,
        )
    assert voices == []
    assert has_more is False
    assert total_count == 100


def test_sync_all_forwards_language_filter():
    seen_params = {}
    def fake_get(url, headers, params, timeout):
        seen_params.update(params)
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"voices": [], "has_more": False,
                                   "total_count": 0}
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


def test_sync_all_invokes_on_page(monkeypatch):
    pages = [
        ([{"voice_id": "v1", "name": "A", "labels": {}}], True, 2),
        ([{"voice_id": "v2", "name": "B", "labels": {}}], False, 2),
    ]
    calls = iter(pages)

    def fake_fetch(**kwargs):
        return next(calls)

    monkeypatch.setattr(
        "pipeline.voice_library_sync.fetch_shared_voices_page", fake_fetch
    )
    monkeypatch.setattr(
        "pipeline.voice_library_sync.upsert_voice", lambda v: None
    )

    seen = []
    def on_page(idx, voices):
        seen.append((idx, [v["voice_id"] for v in voices]))

    total = sync_all_shared_voices(api_key="dummy", on_page=on_page)
    assert total == 2
    assert seen == [(0, ["v1"]), (1, ["v2"])]


def test_embed_missing_invokes_on_progress(tmp_path, monkeypatch):
    voices = [
        {"voice_id": "v1", "preview_url": "http://a.mp3"},
        {"voice_id": "v2", "preview_url": "http://b.mp3"},
    ]
    monkeypatch.setattr(
        "pipeline.voice_library_sync._list_voices_without_embedding",
        lambda limit=None, language=None: voices,
    )
    monkeypatch.setattr(
        "pipeline.voice_library_sync._download_preview",
        lambda url, dest: str(dest),
    )
    monkeypatch.setattr(
        "pipeline.voice_library_sync.embed_audio_file",
        lambda path: np.full(256, 0.5, dtype=np.float32),
    )
    monkeypatch.setattr(
        "pipeline.voice_library_sync._update_embedding",
        lambda vid, blob: None,
    )

    progress = []
    def on_progress(done, total, vid, ok):
        progress.append((done, total, vid, ok))

    from pipeline.voice_library_sync import embed_missing_voices
    count = embed_missing_voices(
        cache_dir=str(tmp_path), on_progress=on_progress
    )
    assert count == 2
    assert progress == [
        (1, 2, "v1", True),
        (2, 2, "v2", True),
    ]


def test_upsert_voice_writes_use_case_from_top_level():
    from pipeline.voice_library_sync import upsert_voice
    captured = {}

    def fake_execute(sql, params):
        captured["sql"] = sql
        captured["params"] = params

    voice = {
        "voice_id": "v1",
        "name": "Rachel",
        "gender": "female",
        "age": "middle_aged",
        "language": "en",
        "accent": "american",
        "category": "professional",
        "descriptive": "calm",
        "use_case": "informative_educational",
        "preview_url": "http://a.mp3",
        "public_owner_id": "abc",
    }
    with patch("pipeline.voice_library_sync.execute", side_effect=fake_execute):
        upsert_voice(voice)

    params = captured["params"]
    # params layout: (voice_id, name, gender, age, language, accent, category,
    #                 descriptive, use_case, preview_url, labels_json, public_owner_id,
    #                 synced_at, updated_at)
    assert params[0] == "v1"
    assert params[8] == "informative_educational"
    import json as _json
    labels_json_str = params[10]
    payload = _json.loads(labels_json_str)
    assert payload["voice_id"] == "v1"
    assert payload["use_case"] == "informative_educational"


def test_upsert_voice_fallback_use_case_from_labels():
    """Legacy API shape: use_case nested inside labels dict."""
    from pipeline.voice_library_sync import upsert_voice
    captured = {}

    def fake_execute(sql, params):
        captured["params"] = params

    voice = {
        "voice_id": "v2",
        "name": "Bob",
        "labels": {"use_case": "narration"},
    }
    with patch("pipeline.voice_library_sync.execute", side_effect=fake_execute):
        upsert_voice(voice)
    assert captured["params"][8] == "narration"


def test_sync_all_respects_max_voices():
    """max_voices=300 时，翻 3 页 × 每页 150 条，应在达到 300 时停止。"""
    from pipeline.voice_library_sync import sync_all_shared_voices
    pages = [
        ([{"voice_id": f"p0_{i}", "name": "x"} for i in range(150)], True, 500),
        ([{"voice_id": f"p1_{i}", "name": "x"} for i in range(150)], True, 500),
        ([{"voice_id": f"p2_{i}", "name": "x"} for i in range(150)], False, 500),
    ]
    calls = {"i": 0}

    def fake_fetch(**kw):
        i = calls["i"]; calls["i"] += 1
        return pages[i]

    with patch("pipeline.voice_library_sync.fetch_shared_voices_page",
               side_effect=fake_fetch), \
         patch("pipeline.voice_library_sync.upsert_voice") as upsert:
        total = sync_all_shared_voices(api_key="k", language="en", max_voices=300)
    assert total == 300
    assert upsert.call_count == 300


def test_sync_all_invokes_on_total_count_first_page():
    from pipeline.voice_library_sync import sync_all_shared_voices
    pages = [([{"voice_id": "v1"}], False, 42)]
    calls = {"i": 0}

    def fake_fetch(**kw):
        i = calls["i"]; calls["i"] += 1
        return pages[i]

    received = {}
    def on_total(n): received["n"] = n

    with patch("pipeline.voice_library_sync.fetch_shared_voices_page",
               side_effect=fake_fetch), \
         patch("pipeline.voice_library_sync.upsert_voice"):
        sync_all_shared_voices(api_key="k", language="en",
                               on_total_count=on_total)
    assert received["n"] == 42


def test_sync_all_stops_when_has_more_false():
    from pipeline.voice_library_sync import sync_all_shared_voices
    pages = [
        ([{"voice_id": "v1"}, {"voice_id": "v2"}], True, 4),
        ([{"voice_id": "v3"}, {"voice_id": "v4"}], False, 4),
    ]
    calls = {"i": 0}
    def fake_fetch(**kw):
        i = calls["i"]; calls["i"] += 1
        return pages[i]

    with patch("pipeline.voice_library_sync.fetch_shared_voices_page",
               side_effect=fake_fetch), \
         patch("pipeline.voice_library_sync.upsert_voice") as upsert:
        total = sync_all_shared_voices(api_key="k")
    assert total == 4
    assert upsert.call_count == 4
