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


def test_preset_selector_is_tall_enough_to_avoid_clipping(omni_list_html):
    import re

    match = re.search(r"\.preset-select\s*\{(?P<body>[^}]+)\}", omni_list_html)
    assert match, "missing .preset-select CSS rule"
    css = match.group("body")
    assert "height: 32px" not in css
    min_height = re.search(r"min-height:\s*(\d+)px", css)
    line_height = re.search(r"line-height:\s*(\d+(?:\.\d+)?)", css)
    assert min_height and int(min_height.group(1)) >= 40
    assert line_height and float(line_height.group(1)) >= 1.3


def test_create_project_modal_has_no_preset_crud_or_step_controls(omni_list_html):
    assert 'id="presetSaveAsBtn"' not in omni_list_html
    assert 'id="presetNewFlowBtn"' not in omni_list_html
    assert 'id="presetDeleteBtn"' not in omni_list_html
    assert 'id="presetFlowOverlay"' not in omni_list_html
    assert 'id="pluginCapabilityForm"' not in omni_list_html
    assert 'id="presetFlowCapabilityForm"' not in omni_list_html
    assert "+ 另存为" not in omni_list_html
    assert "+ 新建流程" not in omni_list_html
    assert "保存流程" not in omni_list_html


def test_create_project_modal_tells_admin_manages_presets(omni_list_html):
    assert "系统级 preset" in omni_list_html
    assert "管理员在系统设置统一维护" in omni_list_html


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


def test_preset_loading_error_message_is_escaped(omni_list_html):
    assert "preset 加载失败：' + _escapeHtml(err.message || err)" in omni_list_html
    assert "preset 加载失败：' + (err.message || err)" not in omni_list_html


def test_javascript_renders_only_system_presets(omni_list_html):
    assert "systemPresets = __omniPresetState.presets.filter" in omni_list_html
    assert "p.scope === 'system'" in omni_list_html
    assert "userPresets" not in omni_list_html
    assert "optgroup label=\"我的\"" not in omni_list_html


def test_javascript_does_not_create_or_delete_presets_from_create_modal(omni_list_html):
    assert "fetch('/api/omni-presets', {" not in omni_list_html
    assert "fetch('/api/omni-presets/'" not in omni_list_html


def test_project_card_menu_contains_duplicate_action(omni_list_html):
    assert "复制项目" in omni_list_html
    assert "duplicateTask(event" in omni_list_html
    assert "/api/omni-translate/' + taskId + '/duplicate" in omni_list_html


def test_duplicate_project_javascript_posts_with_csrf(omni_list_html):
    assert "async function duplicateTask" in omni_list_html
    assert "method: 'POST'" in omni_list_html
    assert "headers: { 'X-CSRFToken': csrfToken }" in omni_list_html
    assert "await _readOmniJsonResponse(res)" in omni_list_html


def test_duplicate_project_shows_progress_until_redirect(omni_list_html):
    assert 'id="duplicateProgress"' in omni_list_html
    assert "function setDuplicateProgress" in omni_list_html
    assert "正在复制项目" in omni_list_html
    assert "项目就绪后会自动打开" in omni_list_html
    assert "setDuplicateProgress(true, '正在复制项目" in omni_list_html
    assert "setDuplicateProgress(true, '项目已就绪" in omni_list_html
    assert "setDuplicateProgress(false)" in omni_list_html


def test_javascript_appends_plugin_config_to_form_data(omni_list_html):
    """submit 时 plugin_config 作为 JSON 加到 FormData。"""
    assert "formData.set('plugin_config'," in omni_list_html


def test_javascript_upload_submit_handles_non_json_error_response(omni_list_html):
    """后端 500/HTML 错误页不能被 res.json() 二次盖成 JSON 解析错误。"""
    assert "async function _readOmniJsonResponse" in omni_list_html
    assert "content-type" in omni_list_html
    assert "res.text()" in omni_list_html
    assert "await _readOmniJsonResponse(res)" in omni_list_html


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


def test_modal_contains_preset_initial_loading_message(omni_list_html):
    """没 fetch 完时显示加载提示。"""
    assert "preset 加载中" in omni_list_html
