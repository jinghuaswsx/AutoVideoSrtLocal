"""视频翻译参数三层回填 DAO 测试。

三层回填优先级(细→粗):
    (user × product × lang) → (user × product) → (user) → SYSTEM_DEFAULTS

前置条件: MySQL 运行 + 本期迁移已应用(db/migrations/2026_04_18_bulk_translate_schema.sql)。
"""
import pytest

from appcore.db import execute, query_one
from appcore.video_translate_defaults import (
    SYSTEM_DEFAULTS,
    load_effective_params,
    save_profile,
)

USER_ID = "__test_user_vt_profile__"
PRODUCT_ID = "__test_product_vt_profile__"


@pytest.fixture
def clean_profiles():
    """每个测试前后都清空该测试用户的 profiles。"""
    execute(
        "DELETE FROM media_video_translate_profiles WHERE user_id = %s",
        (USER_ID,),
    )
    yield
    execute(
        "DELETE FROM media_video_translate_profiles WHERE user_id = %s",
        (USER_ID,),
    )


def test_load_returns_system_defaults_when_no_profile(clean_profiles):
    """无任何 profile 时,返回 SYSTEM_DEFAULTS 的副本。"""
    result = load_effective_params(USER_ID, PRODUCT_ID, "de")
    # 所有 SYSTEM_DEFAULTS 键值都应该能读到
    for k, v in SYSTEM_DEFAULTS.items():
        assert result[k] == v


def test_user_level_profile_overrides_defaults(clean_profiles):
    """用户级(product_id=NULL, lang=NULL) profile 覆盖系统默认。"""
    save_profile(USER_ID, product_id=None, lang=None,
                 params={"subtitle_size": 18})
    result = load_effective_params(USER_ID, PRODUCT_ID, "de")
    assert result["subtitle_size"] == 18
    # 其他键保持系统默认
    assert result["subtitle_color"] == SYSTEM_DEFAULTS["subtitle_color"]
    assert result["tts_speed"] == SYSTEM_DEFAULTS["tts_speed"]


def test_product_level_profile_overrides_user_level(clean_profiles):
    """产品级(lang=NULL)覆盖用户级,未设的字段逐级回退。"""
    save_profile(USER_ID, product_id=None, lang=None,
                 params={"subtitle_size": 18, "tts_speed": 1.2})
    save_profile(USER_ID, product_id=PRODUCT_ID, lang=None,
                 params={"subtitle_size": 20})
    result = load_effective_params(USER_ID, PRODUCT_ID, "de")
    assert result["subtitle_size"] == 20    # 产品级覆盖
    assert result["tts_speed"] == 1.2       # 产品级未设,回退用户级
    assert result["subtitle_color"] == SYSTEM_DEFAULTS["subtitle_color"]


def test_product_lang_level_overrides_product_level(clean_profiles):
    """最细粒度(product + lang)覆盖产品级。"""
    save_profile(USER_ID, product_id=PRODUCT_ID, lang=None,
                 params={"subtitle_size": 20})
    save_profile(USER_ID, product_id=PRODUCT_ID, lang="de",
                 params={"subtitle_size": 22, "tts_speed": 0.9})
    result = load_effective_params(USER_ID, PRODUCT_ID, "de")
    assert result["subtitle_size"] == 22
    assert result["tts_speed"] == 0.9


def test_fr_does_not_inherit_de_specific_profile(clean_profiles):
    """lang='de' 的 profile 不应影响 lang='fr' 的查询结果。"""
    save_profile(USER_ID, product_id=PRODUCT_ID, lang="de",
                 params={"subtitle_size": 22})
    result = load_effective_params(USER_ID, PRODUCT_ID, "fr")
    # fr 没有该语言级配置,回退到系统默认(因为也没有产品级、用户级)
    assert result["subtitle_size"] == SYSTEM_DEFAULTS["subtitle_size"]


def test_save_upsert_updates_existing(clean_profiles):
    """同一 (user, product, lang) 第二次 save 是 upsert 而不是插入重复。"""
    save_profile(USER_ID, PRODUCT_ID, "de", {"subtitle_size": 20})
    save_profile(USER_ID, PRODUCT_ID, "de", {"subtitle_size": 24})

    result = load_effective_params(USER_ID, PRODUCT_ID, "de")
    assert result["subtitle_size"] == 24

    # 确认 DB 里只有一条记录
    from appcore.db import query
    rows = query(
        "SELECT id FROM media_video_translate_profiles "
        "WHERE user_id=%s AND product_id=%s AND lang=%s",
        (USER_ID, PRODUCT_ID, "de"),
    )
    assert len(rows) == 1


def test_load_with_null_product_and_lang(clean_profiles):
    """load_effective_params(user, None, None) 只查用户级。"""
    save_profile(USER_ID, None, None, {"subtitle_size": 18})
    result = load_effective_params(USER_ID, None, None)
    assert result["subtitle_size"] == 18
