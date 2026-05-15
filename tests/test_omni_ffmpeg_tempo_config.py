from __future__ import annotations

from appcore import omni_ffmpeg_tempo_config as config


def test_ffmpeg_tempo_fallback_defaults_disabled_when_missing(monkeypatch):
    monkeypatch.setattr(config.settings, "get_setting", lambda key: None)

    assert config.is_enabled() is False


def test_ffmpeg_tempo_fallback_defaults_disabled_when_setting_read_fails(monkeypatch):
    def raise_error(key):
        raise RuntimeError("settings unavailable")

    monkeypatch.setattr(config.settings, "get_setting", raise_error)

    assert config.is_enabled() is False


def test_ffmpeg_tempo_fallback_parses_truthy_values(monkeypatch):
    values = iter(["1", "true", "yes", "on"])
    monkeypatch.setattr(config.settings, "get_setting", lambda key: next(values))

    assert [config.is_enabled() for _ in range(4)] == [True, True, True, True]


def test_set_ffmpeg_tempo_fallback_writes_string_flag(monkeypatch):
    writes = []
    monkeypatch.setattr(config.settings, "set_setting", lambda key, value: writes.append((key, value)))

    config.set_enabled(True)
    config.set_enabled(False)

    assert writes == [
        (config.SETTING_KEY, "1"),
        (config.SETTING_KEY, "0"),
    ]
