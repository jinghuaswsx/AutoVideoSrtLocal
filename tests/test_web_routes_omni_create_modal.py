"""Static-source tests for omni_translate_list.html "+ 新建任务" modal (Phase 4).

不走 Flask 路由（避免在测试环境拉真 DB），直接读模板文件 + layout.html 检查
关键 HTML / JS / CSS 元素都到位。这些断言聚焦于模板字面，渲染时 Jinja 会原样
透传，不会改写。
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def omni_list_html():
    root = Path(__file__).resolve().parents[1]
    return (root / "web" / "templates" / "omni_translate_list.html").read_text(encoding="utf-8")


@pytest.fixture
def layout_html():
    root = Path(__file__).resolve().parents[1]
    return (root / "web" / "templates" / "layout.html").read_text(encoding="utf-8")


def test_modal_contains_preset_selector(omni_list_html):
    assert 'id="presetSelect"' in omni_list_html
    assert 'class="preset-select"' in omni_list_html


def test_modal_contains_capability_form_placeholder(omni_list_html):
    assert 'id="pluginCapabilityForm"' in omni_list_html
    assert 'class="plugin-capability-form"' in omni_list_html


def test_modal_contains_save_as_preset_button(omni_list_html):
    assert 'id="presetSaveAsBtn"' in omni_list_html
    assert "+ 另存为" in omni_list_html


def test_modal_contains_modified_flag(omni_list_html):
    assert 'id="presetModifiedFlag"' in omni_list_html
    assert "已修改" in omni_list_html


def test_modal_contains_delete_button_for_user_presets(omni_list_html):
    assert 'id="presetDeleteBtn"' in omni_list_html


def test_modal_contains_upload_form(omni_list_html):
    assert 'id="uploadForm"' in omni_list_html
    assert 'enctype="multipart/form-data"' in omni_list_html


def test_modal_contains_target_lang_pills(omni_list_html):
    assert 'id="modalLangPills"' in omni_list_html
    # 模板里走 jinja for 循环，hard-code 字段断言不可靠；只检查容器
    assert "modalLangPills" in omni_list_html


def test_modal_contains_source_lang_pills(omni_list_html):
    assert 'id="modalSourceLangPills"' in omni_list_html
    # 11 种 source lang 都在 jinja for 循环里硬编码
    for lang in ("zh", "en", "es", "pt", "fr", "it", "de", "ja", "nl", "sv", "fi"):
        assert f"'{lang}'" in omni_list_html


def test_layout_provides_csrf_meta(layout_html):
    # layout.html 注入的 CSRF token meta，前端 _csrf() 读这个
    assert 'name="csrf-token"' in layout_html


def test_javascript_loads_presets_on_modal_open(omni_list_html):
    assert "_loadOmniPresets" in omni_list_html
    assert "/api/omni-presets" in omni_list_html
    assert "/api/omni-presets/default" in omni_list_html


def test_javascript_implements_dependency_locks(omni_list_html):
    """互斥/依赖联动逻辑在前端"""
    # 函数名 + 关键约束都在脚本里
    assert "_applyDependencyLocks" in omni_list_html
    assert "shot_char_limit" in omni_list_html
    assert "av_sentence" in omni_list_html
    assert "sentence_units" in omni_list_html
    assert "loudness_match" in omni_list_html


def test_javascript_implements_save_as_user_preset(omni_list_html):
    assert "presetSaveAsBtn" in omni_list_html
    # POST /api/omni-presets 创建 user-level
    assert "/api/omni-presets" in omni_list_html
    # 保存的 plugin_config 来自 currentCfg
    assert "currentCfg" in omni_list_html


def test_javascript_appends_plugin_config_to_form_data(omni_list_html):
    """submit 时 plugin_config 作为 JSON 加到 FormData。"""
    assert "formData.set('plugin_config'," in omni_list_html


def test_css_uses_ocean_blue_tokens_no_purple(omni_list_html):
    """CSS 走 oklch hue 200-240，不含紫色色相 (260+)。"""
    import re
    matches = re.findall(r"oklch\([^)]*?(\d{2,3})\s*\)", omni_list_html)
    for hue_str in matches:
        try:
            hue = int(hue_str)
        except ValueError:
            continue
        # hue 必须在 0-240 之间——不能 ≥ 260（紫色禁用区）
        if hue >= 260 and hue < 360:
            pytest.fail(f"detected purple hue {hue} in modal CSS")


def test_modal_contains_capability_form_initial_loading_message(omni_list_html):
    """没 fetch 完时显示加载提示。"""
    assert "能力点加载中" in omni_list_html
