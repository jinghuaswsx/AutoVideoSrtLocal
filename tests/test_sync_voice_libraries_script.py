from __future__ import annotations

from unittest.mock import MagicMock

import scripts.sync_voice_libraries as sync_driver


def test_get_api_key_reads_elevenlabs_provider_config(monkeypatch):
    monkeypatch.setattr(
        sync_driver,
        "require_provider_api_key",
        lambda provider_code: f"db-key-for-{provider_code}",
    )

    assert sync_driver._get_api_key() == "db-key-for-elevenlabs_tts"


def test_target_languages_default_to_enabled_media_languages(monkeypatch):
    monkeypatch.delenv("VOICE_SYNC_LANGUAGES", raising=False)
    monkeypatch.setattr(
        sync_driver.medias,
        "list_enabled_language_codes",
        lambda: ["en", "de", "nl", "sv", "fi"],
    )

    assert sync_driver._target_languages() == ["en", "de", "nl", "sv", "fi"]


def test_target_languages_can_be_overridden_by_env(monkeypatch):
    monkeypatch.setenv("VOICE_SYNC_LANGUAGES", " en, de ,, fi ")

    assert sync_driver._target_languages() == ["en", "de", "fi"]


def test_max_voices_per_language_can_be_overridden_by_env(monkeypatch):
    monkeypatch.setenv("VOICE_SYNC_MAX_PER_LANGUAGE", "250")

    assert sync_driver._max_voices_per_language() == 250


def test_sync_language_caps_metadata_to_1000_and_records_remote_total(monkeypatch):
    state = {"languages": {}}
    stats = {"total": 1000, "embedded": 1000}
    sync_calls: list[dict] = []
    upsert_stats = MagicMock()

    def fake_sync_shared_voice_variants(**kwargs):
        sync_calls.append(kwargs)
        kwargs["on_total_count"](6311)
        kwargs["on_page"](0, [{"voice_id": "v1"}])
        return 1000

    monkeypatch.setattr(sync_driver, "sync_shared_voice_variants", fake_sync_shared_voice_variants)
    monkeypatch.setattr(sync_driver, "embed_missing_voice_variants", lambda *args, **kwargs: 0)
    monkeypatch.setattr(sync_driver, "ensure_voice_variants_table", lambda: None)
    monkeypatch.setattr(sync_driver, "_summary_row", lambda lang: stats)
    monkeypatch.setattr(sync_driver, "_save_state", lambda state: None)
    monkeypatch.setattr(sync_driver, "upsert_library_stats", upsert_stats)

    sync_driver._sync_language("en", "api-key", state)

    assert sync_calls[0]["max_voices"] == 1000
    assert sync_calls[0]["language"] == "en"
    upsert_stats.assert_called_once_with("en", 6311)
    assert state["languages"]["en"]["status"] == "done"
    assert state["languages"]["en"]["remote_total"] == 6311
    assert state["languages"]["en"]["target_total"] == 1000


def test_sync_language_stays_partial_until_all_existing_rows_are_embedded(monkeypatch):
    state = {"languages": {}}

    def fake_sync_shared_voice_variants(**kwargs):
        kwargs["on_total_count"](6311)
        return 1000

    monkeypatch.setattr(sync_driver, "sync_shared_voice_variants", fake_sync_shared_voice_variants)
    monkeypatch.setattr(sync_driver, "embed_missing_voice_variants", lambda *args, **kwargs: 0)
    monkeypatch.setattr(sync_driver, "ensure_voice_variants_table", lambda: None)
    monkeypatch.setattr(sync_driver, "_summary_row", lambda lang: {"total": 1001, "embedded": 1000})
    monkeypatch.setattr(sync_driver, "_save_state", lambda state: None)
    monkeypatch.setattr(sync_driver, "upsert_library_stats", lambda lang, total: None)

    sync_driver._sync_language("en", "api-key", state)

    assert state["languages"]["en"]["status"] == "partial"


def test_target_from_entry_recomputes_target_when_max_increases():
    entry = {"target_total": 500, "remote_total": 927}

    assert sync_driver._target_from_entry(entry) == 927
