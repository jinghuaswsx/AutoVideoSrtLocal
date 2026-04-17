"""字幕样式从硬字幕 → CapCut 工程包的参数映射单测。

这三个 helper 的稳定性决定了英/德/法三条导出链路上字体/字号/位置能否真正
复刻到 CapCut 草稿，是一组不依赖 pyJianYingDraft 的纯函数。
"""
from pipeline.capcut import (
    _resolve_capcut_font_enum_name,
    _resolve_capcut_font_size,
    _resolve_capcut_transform_y,
)


# ---------------------------------------------------------------------------
# 字号：硬字幕 preset/整数 → CapCut TextStyle.size（浮点）
# ---------------------------------------------------------------------------

def test_medium_maps_to_default_5_6():
    assert _resolve_capcut_font_size("medium") == 5.6


def test_small_maps_below_default():
    assert _resolve_capcut_font_size("small") == 4.4


def test_large_maps_above_default():
    assert _resolve_capcut_font_size("large") == 7.2


def test_numeric_14_matches_medium():
    # 线性换算 14pt → 5.6 维持 medium 视觉基准
    assert _resolve_capcut_font_size(14) == 5.6


def test_numeric_18_scales_up_linearly():
    # 18 / 14 * 5.6 = 7.2（保留 2 位小数）
    assert _resolve_capcut_font_size(18) == 7.2


def test_unknown_preset_falls_back_to_medium():
    assert _resolve_capcut_font_size("xxl") == 5.6


# ---------------------------------------------------------------------------
# 位置：硬字幕 position_y（0 顶 → 1 底）→ CapCut transform_y（+1 顶 → -1 底）
# ---------------------------------------------------------------------------

def test_default_position_maps_to_near_bottom_third():
    # position_y=0.68 → transform_y = 1 - 2*0.68 = -0.36
    assert _resolve_capcut_transform_y(0.68, "bottom") == -0.36


def test_top_position_y_maps_to_plus_one():
    assert _resolve_capcut_transform_y(0.0, "bottom") == 1.0


def test_bottom_position_y_maps_to_minus_one():
    assert _resolve_capcut_transform_y(1.0, "bottom") == -1.0


def test_middle_position_y_maps_to_zero():
    assert _resolve_capcut_transform_y(0.5, "bottom") == 0.0


def test_missing_position_y_falls_back_to_legacy():
    # 老任务无 subtitle_position_y 时回退到 top/middle/bottom 三档映射
    assert _resolve_capcut_transform_y(None, "bottom") == -0.78
    assert _resolve_capcut_transform_y(None, "middle") == 0.0
    assert _resolve_capcut_transform_y(None, "top") == 0.78


# ---------------------------------------------------------------------------
# 字体：UI 字体名 → CapCut FontType 枚举名，找不到时返回 None 走默认字体
# ---------------------------------------------------------------------------

def test_impact_alias_to_anton():
    # 硬字幕里 Impact 已 alias 到 Anton，CapCut 同样映射到 Anton 保持一致
    assert _resolve_capcut_font_enum_name("Impact") == "Anton"


def test_anton_maps_to_itself():
    assert _resolve_capcut_font_enum_name("Anton") == "Anton"


def test_poppins_variants_map_to_poppins_bold():
    assert _resolve_capcut_font_enum_name("Poppins Bold") == "Poppins_Bold"
    assert _resolve_capcut_font_enum_name("Poppins") == "Poppins_Bold"


def test_montserrat_variants_map_to_montserrat_black():
    assert _resolve_capcut_font_enum_name("Montserrat ExtraBold") == "Montserrat_Black"
    assert _resolve_capcut_font_enum_name("Montserrat") == "Montserrat_Black"


def test_oswald_has_no_capcut_counterpart():
    # Oswald/Bebas 在 CapCut FontType 枚举里无对应，返回 None → 导出时走剪映默认字体
    assert _resolve_capcut_font_enum_name("Oswald Bold") is None
    assert _resolve_capcut_font_enum_name("Oswald") is None


def test_bebas_has_no_capcut_counterpart():
    assert _resolve_capcut_font_enum_name("Bebas Neue") is None
    assert _resolve_capcut_font_enum_name("Bebas") is None


def test_unknown_font_returns_none():
    assert _resolve_capcut_font_enum_name("Helvetica") is None
    assert _resolve_capcut_font_enum_name("") is None
