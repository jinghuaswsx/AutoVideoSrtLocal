from appcore.material_filename_rules import (
    build_translated_material_filename,
    validate_initial_material_filename,
    validate_material_filename,
)


def test_initial_material_filename_accepts_user_filename_for_any_language():
    result = validate_initial_material_filename(
        "2024.01.06-逝后指南-混剪-李文龙.mp4",
        "逝后指南",
        "fr",
        {"en": "英语", "fr": "法语"},
    )

    assert result.ok
    assert result.errors == ()
    assert result.effective_lang == "fr"


def test_edit_material_filename_rejects_loose_localized_filename():
    result = validate_material_filename(
        "2024.01.06-逝后指南-混剪-李文龙.mp4",
        "逝后指南",
        "fr",
        {"en": "英语", "fr": "法语"},
    )

    assert not result.ok
    assert result.effective_lang == "fr"


def test_edit_english_material_filename_uses_loose_rule():
    assert validate_material_filename(
        "2024.01.06-逝后指南-混剪-李文龙.mp4",
        "逝后指南",
        "en",
        {"en": "英语", "fr": "法语"},
    ).ok


def test_edit_localized_material_filename_accepts_supplement_slot_letter():
    result = validate_material_filename(
        "2024.01.06-逝后指南-原素材-补充素材B(法语)-指派-蔡靖华.mp4",
        "逝后指南",
        "fr",
        {"en": "英语", "fr": "法语"},
    )

    assert result.ok
    assert result.effective_lang == "fr"


def test_edit_localized_material_filename_accepts_multi_owner_tail():
    result = validate_material_filename(
        "2026.05.13-手机屏幕放大器-原素材-补充素材(法语)-顾倩multi-蔡靖华.mp4",
        "手机屏幕放大器",
        "fr",
        {"en": "英语", "fr": "法语"},
    )

    assert result.ok
    assert result.effective_lang == "fr"


def test_edit_localized_material_filename_accepts_any_no_space_assignment_tail():
    result = validate_material_filename(
        "2026.05.13-手机屏幕放大器-原素材-补充素材(法语)-顾倩multi补拍A-蔡靖华.mp4",
        "手机屏幕放大器",
        "fr",
        {"en": "英语", "fr": "法语"},
    )

    assert result.ok
    assert result.effective_lang == "fr"


def test_translated_material_filename_uses_current_date_and_source_assignment(monkeypatch):
    from datetime import date as real_date
    import appcore.material_filename_rules as rules

    class FixedDate(real_date):
        @classmethod
        def today(cls):
            return cls(2026, 5, 22)

    monkeypatch.setattr(rules, "date", FixedDate)

    assert build_translated_material_filename(
        "2026.04.01-煮蛋器-原素材-指派-陈兆阳.mp4",
        "煮蛋器",
        "fr",
        {"en": "英语", "fr": "法语"},
    ) == "2026.05.22-煮蛋器-原素材-小语种翻译素材(法语)-20260401陈兆阳-蔡靖华.mp4"


def test_edit_localized_material_filename_accepts_new_translated_material_pattern():
    result = validate_material_filename(
        "2026.05.22-煮蛋器-原素材-小语种翻译素材(法语)-20260401陈兆阳-蔡靖华.mp4",
        "煮蛋器",
        "en",
        {"en": "英语", "fr": "法语"},
    )

    assert result.ok
    assert result.effective_lang == "fr"


def test_initial_material_filename_requires_only_date_product_tail_and_mp4():
    assert validate_initial_material_filename(
        "2024.01.06-逝后指南-混剪-李文龙.mp4",
        "逝后指南",
    ).ok
    assert not validate_material_filename(
        "2024-01-06-逝后指南-混剪-李文龙.mp4",
        "逝后指南",
    ).ok
    assert not validate_initial_material_filename(
        "2024.01.06-其他产品-混剪-李文龙.mp4",
        "逝后指南",
    ).ok
    assert not validate_initial_material_filename(
        "2024.01.06-逝后指南-混剪-李文龙.mov",
        "逝后指南",
    ).ok


def test_material_filename_accepts_spaces_by_stripping_them():
    languages = {"en": "英语", "fr": "法语"}

    for filename in (
        " 2026.04.17-窗帘挂钩-原素材.mp4",
        "2026.04.17-窗帘挂钩-原 素材.mp4",
        "2026.04.17-窗帘挂钩-原素材.mp4 ",
    ):
        result = validate_initial_material_filename(filename, "窗帘挂钩", "en", languages)
        assert result.ok
        assert result.errors == ()

    result = validate_material_filename(
        "2026.04.17-窗帘挂钩-原素材-补充素材 B(法语)-指派-蔡靖华.mp4",
        "窗帘挂钩",
        "fr",
        languages,
    )
    assert result.ok
    assert result.errors == ()

    result = validate_material_filename(
        "2026.05.22-窗帘挂钩-原素材-小语种翻译素材(法语)-20260417 张三-蔡靖华.mp4",
        "窗帘挂钩",
        "fr",
        languages,
    )
    assert result.ok
    assert result.errors == ()


def test_material_filename_accepts_product_name_with_spaces():
    languages = {"en": "英语", "de": "德语"}
    product_name_with_spaces = "Multi-Purpose Anti-Scald Bowl Holder Clip for Kitchen"

    # Test initial upload simple filename
    result_init = validate_initial_material_filename(
        "2026.05.25-Multi-PurposeAnti-ScaldBowlHolderClipforKitchen-素材.mp4",
        product_name_with_spaces,
        "en",
        languages
    )
    assert result_init.ok
    assert result_init.errors == ()

    # Test localized supplement filename
    result_supp = validate_material_filename(
        "2026.05.25-Multi-PurposeAnti-ScaldBowlHolderClipforKitchen-原素材-补充素材(德语)-指派-蔡靖华.mp4",
        product_name_with_spaces,
        "de",
        languages
    )
    assert result_supp.ok
    assert result_supp.errors == ()

    # Test localized translated filename
    result_trans = validate_material_filename(
        "2026.05.25-Multi-PurposeAnti-ScaldBowlHolderClipforKitchen-原素材-小语种翻译素材(德语)-20260525苏齐齐-蔡靖华.mp4",
        product_name_with_spaces,
        "de",
        languages
    )
    assert result_trans.ok
    assert result_trans.errors == ()


def test_translated_material_filename_extracts_author_from_legacy_and_custom_names(monkeypatch):
    from datetime import date as real_date
    import appcore.material_filename_rules as rules

    class FixedDate(real_date):
        @classmethod
        def today(cls):
            return cls(2026, 5, 27)

    monkeypatch.setattr(rules, "date", FixedDate)

    # 1. 经典的老式原素材 "-补充素材-E-谢心仪.mp4" 格式，提取出最后一个横杠后的 "谢心仪"
    res1 = build_translated_material_filename(
        "2026.02.28-轮胎压力传感器-原素材-补充素材-E-谢心仪.mp4",
        "轮胎压力传感器",
        "de",
        {"en": "英语", "de": "德语"},
    )
    assert res1 == "2026.05.27-轮胎压力传感器-原素材-小语种翻译素材(德语)-20260228谢心仪-蔡靖华.mp4"

    # 2. 多负责人格式，提取出最后一个横杠后的 "蔡靖华"
    res2 = build_translated_material_filename(
        "2026.05.13-手机屏幕放大器-原素材-补充素材(法语)-顾倩multi-蔡靖华.mp4",
        "手机屏幕放大器",
        "de",
        {"en": "英语", "de": "德语"},
    )
    assert res2 == "2026.05.27-手机屏幕放大器-原素材-小语种翻译素材(德语)-20260513蔡靖华-蔡靖华.mp4"

    # 3. 极简的原素材文件名 "2026.04.01-煮蛋器-素材.mp4"，提取出 "素材"
    res3 = build_translated_material_filename(
        "2026.04.01-煮蛋器-素材.mp4",
        "煮蛋器",
        "de",
        {"en": "英语", "de": "德语"},
    )
    assert res3 == "2026.05.27-煮蛋器-原素材-小语种翻译素材(德语)-20260401素材-蔡靖华.mp4"


