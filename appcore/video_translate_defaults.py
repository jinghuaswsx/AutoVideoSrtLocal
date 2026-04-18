"""视频翻译 12 项参数默认值与三层回填逻辑。

设计文档: docs/superpowers/specs/2026-04-18-bulk-translate-design.md 第 3.3 / 3.4 节

三层回填优先级:
    (user × product × lang)
        → (user × product, lang=NULL)
            → (user, product=NULL, lang=NULL)
                → SYSTEM_DEFAULTS

DAO 实现在 Task 3 追加。
"""

# ============================================================
# 系统出厂默认值(最终兜底)
# ============================================================
SYSTEM_DEFAULTS = {
    # ---- 🟢 基础档(弹窗展开即见)----
    "subtitle_font": "Noto Sans",
    "subtitle_size": 14,
    "subtitle_position_y": 0.88,
    "subtitle_color": "#FFFFFF",
    "subtitle_stroke_color": "#000000",
    "subtitle_stroke_width": 2,
    "subtitle_burn_in": True,
    "subtitle_export_srt": True,

    # ---- 🟡 进阶档(默认折叠)----
    "subtitle_background": "none",
    "tts_speed": 1.0,
    "background_audio": "keep",
    "background_audio_db": -18,
    "max_line_width": 42,

    # ---- ⚪ 高级档(通常不改)----
    "output_resolution": "source",
    "output_codec": "h264",
    "output_bitrate_kbps": 2000,
    "output_format": "mp4",
}

# ============================================================
# TTS 音色默认名(最终 voice_id 在 resolve_default_voice 里解析)
# ============================================================
TTS_VOICE_DEFAULTS = {
    "de": "Anke",
    "fr": "Céline",
}

# ============================================================
# 视频翻译本期支持语言(设计文档第 0.4 节强约束)
# 其他语种(es/it/ja/pt 等)只翻译文案+图片,视频自动 skip
# ============================================================
VIDEO_SUPPORTED_LANGS = {"de", "fr"}
