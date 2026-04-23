"""视频翻译 12 项参数默认值常量测试。

设计文档第 3.3 节: docs/superpowers/specs/2026-04-18-bulk-translate-design.md
"""
from appcore.video_translate_defaults import (
    SYSTEM_DEFAULTS,
    TTS_VOICE_DEFAULTS,
    VIDEO_SUPPORTED_LANGS,
)


def test_system_defaults_has_all_12_params():
    """12 项参数完整覆盖(基础 8 + 进阶 4 + 高级 4 = 16 个键)。"""
    required = {
        # 基础档
        "subtitle_font", "subtitle_size", "subtitle_position_y",
        "subtitle_color", "subtitle_stroke_color", "subtitle_stroke_width",
        "subtitle_burn_in", "subtitle_export_srt",
        # 进阶档
        "subtitle_background",
        "tts_speed", "background_audio", "background_audio_db",
        "max_line_width",
        # 高级档
        "output_resolution", "output_codec", "output_bitrate_kbps",
        "output_format",
    }
    assert required.issubset(SYSTEM_DEFAULTS.keys()), \
        f"缺少键: {required - set(SYSTEM_DEFAULTS.keys())}"


def test_system_defaults_values_match_design():
    """默认值与设计文档第 3.3 节一致。"""
    assert SYSTEM_DEFAULTS["subtitle_font"] == "Noto Sans"
    assert SYSTEM_DEFAULTS["subtitle_size"] == 14
    assert SYSTEM_DEFAULTS["subtitle_position_y"] == 0.88
    assert SYSTEM_DEFAULTS["subtitle_color"] == "#FFFFFF"
    assert SYSTEM_DEFAULTS["subtitle_stroke_color"] == "#000000"
    assert SYSTEM_DEFAULTS["subtitle_stroke_width"] == 2
    assert SYSTEM_DEFAULTS["subtitle_burn_in"] is True
    assert SYSTEM_DEFAULTS["subtitle_export_srt"] is True
    assert SYSTEM_DEFAULTS["subtitle_background"] == "none"
    assert SYSTEM_DEFAULTS["tts_speed"] == 1.0
    assert SYSTEM_DEFAULTS["background_audio"] == "keep"
    assert SYSTEM_DEFAULTS["background_audio_db"] == -18
    assert SYSTEM_DEFAULTS["max_line_width"] == 42
    assert SYSTEM_DEFAULTS["output_resolution"] == "source"
    assert SYSTEM_DEFAULTS["output_codec"] == "h264"
    assert SYSTEM_DEFAULTS["output_bitrate_kbps"] == 2000
    assert SYSTEM_DEFAULTS["output_format"] == "mp4"


def test_video_supported_langs_match_multi_translate_languages():
    """视频翻译支持集应覆盖当前多语种视频流水线。"""
    assert VIDEO_SUPPORTED_LANGS == {"de", "fr", "es", "it", "pt", "ja", "nl", "sv", "fi"}


def test_tts_voice_defaults_has_de_and_fr():
    """de/fr 两语种至少有默认音色名。"""
    assert "de" in TTS_VOICE_DEFAULTS
    assert "fr" in TTS_VOICE_DEFAULTS
    assert TTS_VOICE_DEFAULTS["de"]
    assert TTS_VOICE_DEFAULTS["fr"]


def test_system_defaults_is_immutable_reference():
    """SYSTEM_DEFAULTS 被外部 mutate 不应影响模块内原本的 dict(复制安全)。"""
    copy1 = dict(SYSTEM_DEFAULTS)
    copy1["subtitle_size"] = 999
    # 重新导入,确保模块内常量没被污染
    from importlib import reload
    import appcore.video_translate_defaults as mod
    reload(mod)
    assert mod.SYSTEM_DEFAULTS["subtitle_size"] == 14


# ============================================================
# resolve_default_voice — TTS 音色探测(Task 4)
# ============================================================

def test_resolve_default_voice_returns_none_when_list_empty(monkeypatch):
    """该语种没有任何可用音色时,返回 None。"""
    from appcore import video_translate_defaults as mod
    monkeypatch.setattr(mod, "_list_voices_by_lang", lambda lang: [])
    assert mod.resolve_default_voice("de") is None


def test_resolve_default_voice_prefers_mapped_name(monkeypatch):
    """TTS_VOICE_DEFAULTS 映射的名字(Anke / Céline)能在列表里匹配到时,优先选中。"""
    from appcore import video_translate_defaults as mod
    monkeypatch.setattr(mod, "_list_voices_by_lang", lambda lang: [
        {"voice_id": "v_other", "name": "Random Voice"},
        {"voice_id": "v_anke", "name": "Anke Müller"},
        {"voice_id": "v_third", "name": "Other"},
    ])
    assert mod.resolve_default_voice("de") == "v_anke"


def test_resolve_default_voice_falls_back_to_first_when_no_match(monkeypatch):
    """映射名字在列表里找不到时,返回列表第一个。"""
    from appcore import video_translate_defaults as mod
    monkeypatch.setattr(mod, "_list_voices_by_lang", lambda lang: [
        {"voice_id": "v_first", "name": "First Voice"},
        {"voice_id": "v_second", "name": "Second Voice"},
    ])
    assert mod.resolve_default_voice("de") == "v_first"


def test_resolve_default_voice_is_case_insensitive(monkeypatch):
    """名字匹配应该大小写不敏感。"""
    from appcore import video_translate_defaults as mod
    monkeypatch.setattr(mod, "_list_voices_by_lang", lambda lang: [
        {"voice_id": "v_celine", "name": "céline (female)"},
    ])
    assert mod.resolve_default_voice("fr") == "v_celine"


def test_resolve_default_voice_handles_missing_name_gracefully(monkeypatch):
    """voice dict 里 name 为 None 或缺失时不崩溃。"""
    from appcore import video_translate_defaults as mod
    monkeypatch.setattr(mod, "_list_voices_by_lang", lambda lang: [
        {"voice_id": "v_no_name", "name": None},
        {"voice_id": "v_anke", "name": "Anke"},
    ])
    assert mod.resolve_default_voice("de") == "v_anke"


def test_resolve_default_voice_unknown_lang_returns_first(monkeypatch):
    """未在 TTS_VOICE_DEFAULTS 中的语种,直接取列表第一项。"""
    from appcore import video_translate_defaults as mod
    monkeypatch.setattr(mod, "_list_voices_by_lang", lambda lang: [
        {"voice_id": "v1", "name": "Maria"},
        {"voice_id": "v2", "name": "Sofia"},
    ])
    assert mod.resolve_default_voice("es") == "v1"
