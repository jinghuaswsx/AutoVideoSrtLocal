"""bulk_translate estimator 单元测试。完全 mock DB 调用,不依赖真实数据库。"""
import pytest


# ------------------------------------------------------------
# Fake DB:根据 sql 关键字路由到预埋的 rows 列表
# ------------------------------------------------------------
class _FakeDB:
    """模拟 appcore.db 的 query / query_one。"""

    def __init__(self, copies_en=None, details_en=None, covers_en=None,
                 videos_en=None, existing=None):
        self.copies_en = copies_en or []
        self.details_en = details_en or []
        self.covers_en = covers_en or []
        self.videos_en = videos_en or []
        # existing: { (table, lang, source_ref_id) -> True }
        self.existing = existing or set()

    def query(self, sql, args=None):
        sql_lower = sql.lower()
        if "from media_copywritings" in sql_lower and "lang = 'en'" in sql_lower:
            return list(self.copies_en)
        if "from media_product_detail_images" in sql_lower and "lang = 'en'" in sql_lower:
            return [{"id": i} for i in self.details_en]
        if "from media_product_covers" in sql_lower and "lang = 'en'" in sql_lower:
            return [{"id": i} for i in self.covers_en]
        if "from media_items" in sql_lower and "lang = 'en'" in sql_lower:
            return list(self.videos_en)
        raise AssertionError(f"unexpected query: {sql}")

    def query_one(self, sql, args=None):
        sql_lower = sql.lower()
        # _translation_exists* 的签名统一:product_id, lang, source_ref_id
        _, lang, source_ref = args
        if "from media_copywritings" in sql_lower:
            table = "media_copywritings"
        elif "from media_product_detail_images" in sql_lower:
            table = "media_product_detail_images"
        elif "from media_product_covers" in sql_lower:
            table = "media_product_covers"
        elif "from media_items" in sql_lower:
            table = "media_items"
        else:
            raise AssertionError(f"unexpected query_one: {sql}")
        exists = (table, lang, source_ref) in self.existing
        return {"x": 1} if exists else None


def _patch_db(monkeypatch, fake):
    from appcore import bulk_translate_estimator as mod
    monkeypatch.setattr(mod, "query", fake.query)
    monkeypatch.setattr(mod, "query_one", fake.query_one)


# ------------------------------------------------------------
# Tests
# ------------------------------------------------------------

def test_copy_only_uses_first_english_copy_per_target_language(monkeypatch):
    """小语种只保留一条文案:多条英文文案时只估算第一条。"""
    fake = _FakeDB(copies_en=[
        {"id": 1, "len_title": 10, "len_body": 40, "len_description": 0,
         "len_ad_carrier": 0, "len_ad_copy": 0, "len_ad_keywords": 0},
        {"id": 2, "len_title": 20, "len_body": 60, "len_description": 10,
         "len_ad_carrier": 0, "len_ad_copy": 0, "len_ad_keywords": 0},
    ])
    _patch_db(monkeypatch, fake)

    from appcore.bulk_translate_estimator import estimate
    r = estimate(user_id=1, product_id=77,
                  target_langs=["de", "fr"],
                  content_types=["copy"],
                  force_retranslate=False)

    # 只取第一条英文文案:50 字符 × 2 语种 × 1.3 × 1.5
    assert r["copy_tokens"] == int(100 * CHARS_TO_TOKENS * TRANSLATION_EXPANSION)
    assert r["image_count"] == 0
    assert r["video_minutes"] == 0
    assert r["skipped"]["copy"] == 0
    assert r["estimated_cost_cny"] > 0


def test_skip_already_translated(monkeypatch):
    """只有 de 已翻译,应跳过 de,只算 fr。"""
    fake = _FakeDB(
        copies_en=[{"id": 1, "len_title": 10, "len_body": 40,
                    "len_description": 0, "len_ad_carrier": 0,
                    "len_ad_copy": 0, "len_ad_keywords": 0}],
        existing={("media_copywritings", "de", 1)},
    )
    _patch_db(monkeypatch, fake)

    from appcore.bulk_translate_estimator import estimate
    r = estimate(1, 77, ["de", "fr"], ["copy"], False)
    # de 跳过, 只算 fr:50 字 × 1.3 × 1.5
    assert r["copy_tokens"] == int(50 * CHARS_TO_TOKENS * TRANSLATION_EXPANSION)
    assert r["skipped"]["copy"] == 1


def test_force_retranslate_ignores_existing(monkeypatch):
    """force=True 时已存在的也重算。"""
    fake = _FakeDB(
        copies_en=[{"id": 1, "len_title": 10, "len_body": 40,
                    "len_description": 0, "len_ad_carrier": 0,
                    "len_ad_copy": 0, "len_ad_keywords": 0}],
        existing={("media_copywritings", "de", 1)},
    )
    _patch_db(monkeypatch, fake)

    from appcore.bulk_translate_estimator import estimate
    r = estimate(1, 77, ["de", "fr"], ["copy"], True)
    # 两个语种都算,50×2×1.3×1.5
    assert r["copy_tokens"] == int(100 * CHARS_TO_TOKENS * TRANSLATION_EXPANSION)
    assert r["skipped"]["copy"] == 0


def test_video_supported_languages_counted_and_unknown_skipped(monkeypatch):
    """视频支持语言计入;未知语言直接跳过。"""
    fake = _FakeDB(videos_en=[
        {"id": 1, "duration_seconds": 120},  # 2 分钟
        {"id": 2, "duration_seconds": 60},   # 1 分钟
    ])
    _patch_db(monkeypatch, fake)

    from appcore.bulk_translate_estimator import estimate
    r = estimate(1, 77, ["de", "fr", "nl", "sv", "fi", "xx"], ["video"], False)
    # 5 个支持语种 × (2+1) 分钟 = 15 分钟；未知 xx 跳过 2 个视频。
    assert r["video_minutes"] == pytest.approx(15.0)
    assert r["skipped"]["video"] == 2


def test_image_cover_and_detail_sum(monkeypatch):
    """cover + detail 都勾时,image_count 是两者之和。"""
    fake = _FakeDB(
        details_en=[10, 11, 12],
        covers_en=[20],
    )
    _patch_db(monkeypatch, fake)

    from appcore.bulk_translate_estimator import estimate
    r = estimate(1, 77, ["de", "fr"], ["cover", "detail"], False)
    # detail 3 张 × 2 语种 = 6,cover 1 张 × 2 语种 = 2
    assert r["image_count"] == 8


def test_empty_product_returns_zero(monkeypatch):
    """产品没任何英文素材时,全零。"""
    fake = _FakeDB()
    _patch_db(monkeypatch, fake)

    from appcore.bulk_translate_estimator import estimate
    r = estimate(1, 77, ["de", "fr"],
                  ["copy", "detail", "cover", "video"], False)
    assert r["copy_tokens"] == 0
    assert r["image_count"] == 0
    assert r["video_minutes"] == 0
    assert r["estimated_cost_cny"] == 0


def test_cost_breakdown_structure(monkeypatch):
    """返回结构包含 breakdown 三项。"""
    fake = _FakeDB(
        copies_en=[{"id": 1, "len_title": 100, "len_body": 0,
                    "len_description": 0, "len_ad_carrier": 0,
                    "len_ad_copy": 0, "len_ad_keywords": 0}],
        details_en=[10],
        videos_en=[{"id": 1, "duration_seconds": 60}],
    )
    _patch_db(monkeypatch, fake)

    from appcore.bulk_translate_estimator import estimate
    r = estimate(1, 77, ["de"], ["copy", "detail", "video"], False)

    assert set(r["breakdown"].keys()) == {"copy_cny", "image_cny", "video_cny"}
    # 汇总等于 breakdown 之和
    b = r["breakdown"]
    assert abs(r["estimated_cost_cny"]
               - (b["copy_cny"] + b["image_cny"] + b["video_cny"])) < 0.01


def test_new_material_content_types_are_estimated(monkeypatch):
    """素材管理新版 content_types 也应进入同一套费用预估。"""
    fake = _FakeDB(
        copies_en=[{"id": 1, "len_title": 10, "len_body": 40,
                    "len_description": 0, "len_ad_carrier": 0,
                    "len_ad_copy": 0, "len_ad_keywords": 0}],
        details_en=[10],
        covers_en=[20],
        videos_en=[{"id": 1, "duration_seconds": 60}],
    )
    _patch_db(monkeypatch, fake)

    from appcore.bulk_translate_estimator import estimate
    r = estimate(
        1,
        77,
        ["de"],
        ["copywriting", "detail_images", "video_covers", "videos"],
        False,
    )

    assert r["copy_tokens"] == int(50 * CHARS_TO_TOKENS * TRANSLATION_EXPANSION)
    assert r["image_count"] == 2
    assert r["video_minutes"] == pytest.approx(1.0)
    assert r["estimated_cost_cny"] > 0


def test_content_types_filtering(monkeypatch):
    """只勾 copy 时,即使 DB 里有图/视频也不计。"""
    fake = _FakeDB(
        copies_en=[{"id": 1, "len_title": 10, "len_body": 0,
                    "len_description": 0, "len_ad_carrier": 0,
                    "len_ad_copy": 0, "len_ad_keywords": 0}],
        details_en=[10, 11],
        videos_en=[{"id": 1, "duration_seconds": 60}],
    )
    _patch_db(monkeypatch, fake)

    from appcore.bulk_translate_estimator import estimate
    r = estimate(1, 77, ["de"], ["copy"], False)
    assert r["copy_tokens"] > 0
    assert r["image_count"] == 0
    assert r["video_minutes"] == 0


# 让测试文件可读性更好,直接 import 常量
from appcore.bulk_translate_estimator import (
    CHARS_TO_TOKENS, TRANSLATION_EXPANSION,
)
