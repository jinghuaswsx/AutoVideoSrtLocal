from appcore.material_filename_rules import (
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
