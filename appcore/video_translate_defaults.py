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


# ============================================================
# 三层回填 DAO(Task 3)
# ============================================================
import json as _json

from appcore.db import query, query_one, execute


def _fetch_params(user_id, product_id, lang):
    """查询某一层 profile,返回 params dict 或 None。

    MySQL 的 `<=>` 是 null-safe 相等,确保 (product_id IS NULL) 和
    (product_id = 'xxx') 都能精确匹配 uk_scope 唯一索引。
    """
    row = query_one(
        """
        SELECT params_json
        FROM media_video_translate_profiles
        WHERE user_id = %s
          AND (product_id <=> %s)
          AND (lang <=> %s)
        LIMIT 1
        """,
        (user_id, product_id, lang),
    )
    if row is None:
        return None
    raw = row["params_json"]
    if isinstance(raw, dict):
        return raw
    return _json.loads(raw)


def load_effective_params(user_id, product_id, lang):
    """三层回填查询,返回合并后的完整参数 dict。

    覆盖顺序(由粗到细,后覆盖前):
        SYSTEM_DEFAULTS
          ← (user_id, None, None)        -- 用户级
          ← (user_id, product_id, None)  -- 产品级(忽略语言)
          ← (user_id, product_id, lang)  -- 最细(产品 × 语言)
    """
    effective = dict(SYSTEM_DEFAULTS)
    for scope in [
        (user_id, None, None),
        (user_id, product_id, None),
        (user_id, product_id, lang),
    ]:
        params = _fetch_params(*scope)
        if params:
            effective.update(params)
    return effective


def save_profile(user_id, product_id, lang, params):
    """upsert 一条 profile。params 必须是 dict。"""
    if not isinstance(params, dict) or not params:
        raise ValueError("params 必须是非空 dict")
    payload = _json.dumps(params, ensure_ascii=False)
    execute(
        """
        INSERT INTO media_video_translate_profiles
            (user_id, product_id, lang, params_json)
        VALUES (%s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE params_json = VALUES(params_json)
        """,
        (user_id, product_id, lang, payload),
    )


# ============================================================
# TTS 音色探测(Task 4)
# ============================================================

def _list_voices_by_lang(lang):
    """查询某语言下所有可用 TTS 音色,返回 [{voice_id, name, ...}]。

    复用 appcore.voice_library_browse.list_voices(language=...),
    它返回 {total, items}。异常/空库时返回空列表。
    """
    try:
        from appcore.voice_library_browse import list_voices
        result = list_voices(language=lang, page=1, page_size=200)
        return result.get("items") or []
    except Exception:
        return []


def resolve_default_voice(lang):
    """给定目标语言,返回推荐 voice_id:

    1. 优先匹配 TTS_VOICE_DEFAULTS 里的名字(大小写不敏感 contains 匹配)
    2. 名字不匹配时,取列表第一个
    3. 列表为空时返回 None
    """
    voices = _list_voices_by_lang(lang)
    if not voices:
        return None

    preferred = TTS_VOICE_DEFAULTS.get(lang)
    if preferred:
        key = preferred.lower()
        for v in voices:
            name = (v.get("name") or "").lower()
            if name and key in name:
                return v["voice_id"]

    return voices[0]["voice_id"]
