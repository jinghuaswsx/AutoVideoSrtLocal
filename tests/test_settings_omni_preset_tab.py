"""Tests for /settings Omni Preset admin tab (Phase 5).

不走真 DB 路由（settings.py index 需要大量 DAO 调用）。聚焦：
- settings.html 模板含 omni_preset tab 的 nav + tab body 的关键元素
- settings.py 的 allowed_tabs 把 omni_preset 加到 admin 集合里
- 路由是 @superadmin_required（普通 user / 未登录都不能进）
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def settings_html():
    root = Path(__file__).resolve().parents[1]
    return (root / "web" / "templates" / "settings.html").read_text(encoding="utf-8")


@pytest.fixture
def settings_py():
    root = Path(__file__).resolve().parents[1]
    return (root / "web" / "routes" / "settings.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Template: nav + tab body
# ---------------------------------------------------------------------------


def test_template_has_omni_preset_nav_link(settings_html):
    assert "tab='omni_preset'" in settings_html
    assert "Omni 实验预设" in settings_html


def test_template_omni_preset_nav_link_inside_admin_block(settings_html):
    """nav link 必须在 can_manage_pricing 块里（普通 user 看不到）。"""
    # 找 admin tabs 区域开始 + 结束
    admin_block_start = settings_html.find("{% if can_manage_pricing %}")
    # 在 settings-tabs 容器内
    admin_block_end = settings_html.find("{% endif %}", admin_block_start)
    omni_link_pos = settings_html.find("tab='omni_preset'")
    assert admin_block_start != -1
    assert admin_block_start < omni_link_pos < admin_block_end


def test_template_has_omni_preset_tab_body(settings_html):
    assert "{% if can_manage_pricing and active_tab == 'omni_preset' %}" in settings_html


def test_template_tab_body_has_global_default_dropdown(settings_html):
    assert 'id="omniGlobalDefault"' in settings_html


def test_template_tab_body_has_system_preset_table(settings_html):
    assert 'id="omniSystemPresetTable"' in settings_html


def test_template_tab_body_has_user_preset_table(settings_html):
    assert 'id="omniUserPresetTable"' in settings_html


def test_template_has_create_buttons(settings_html):
    assert 'id="omniCreateSystemBtn"' in settings_html
    assert 'id="omniCreateUserBtn"' in settings_html


def test_template_has_edit_modal(settings_html):
    assert 'id="omniPresetEditOverlay"' in settings_html
    assert 'id="omniPresetEditName"' in settings_html
    assert 'id="omniPresetEditDesc"' in settings_html
    assert 'id="omniPresetEditCapForm"' in settings_html
    assert 'id="omniPresetEditSaveBtn"' in settings_html


def test_javascript_calls_set_as_default_endpoint(settings_html):
    assert "/set-as-default" in settings_html


def test_javascript_calls_omni_presets_crud(settings_html):
    """全 CRUD 端点都被 JS 触达。"""
    assert "/api/omni-presets" in settings_html


def test_javascript_implements_dependency_locks_in_edit_modal(settings_html):
    """编辑弹窗内也跑互斥/依赖联动。"""
    assert "applyEditDependencyLocks" in settings_html
    assert "shot_char_limit" in settings_html
    assert "av_sentence" in settings_html
    assert "sentence_units" in settings_html
    assert "loudness_match" in settings_html


def test_javascript_loads_capability_groups_dynamically(settings_html):
    """前端不能 hardcode 12 个能力点；从 API 拿 capability_groups。"""
    assert "capability_groups" in settings_html


def test_template_no_purple_oklch_hue(settings_html):
    """Ocean Blue Admin 硬约束：所有 oklch hue 在 200-240 区间，不能出现 ≥260。"""
    import re
    matches = re.findall(r"oklch\([^)]*?(\d{2,3})\s*\)", settings_html)
    for hue_str in matches:
        try:
            hue = int(hue_str)
        except ValueError:
            continue
        if hue >= 260 and hue < 360:
            pytest.fail(f"detected purple hue {hue} in settings template")


# ---------------------------------------------------------------------------
# Route: tab whitelist + admin guard
# ---------------------------------------------------------------------------


def test_route_allows_omni_preset_tab_for_admin(settings_py):
    assert 'allowed_tabs.add("omni_preset")' in settings_py


def test_route_uses_superadmin_required(settings_py):
    """/settings 整个走 superadmin_required —— 普通 user 自动看不到任何 tab。"""
    # 找 index() 函数前的装饰器
    idx = settings_py.find('def index():')
    assert idx > 0
    # 装饰器在 def 前面
    decorator_block = settings_py[max(0, idx - 200):idx]
    assert "@superadmin_required" in decorator_block


# ---------------------------------------------------------------------------
# Live: 普通 user 访问 /settings → 被拒
# ---------------------------------------------------------------------------


def test_normal_user_cannot_open_settings(monkeypatch):
    """普通 user 进 /settings → 拒绝（403 或 redirect）。"""
    monkeypatch.setattr("web.app._run_startup_recovery", lambda: None)
    monkeypatch.setattr("web.app.recover_all_interrupted_tasks", lambda: None)
    monkeypatch.setattr("web.app.mark_interrupted_bulk_translate_tasks", lambda: None)
    monkeypatch.setattr("web.app._seed_default_prompts", lambda: None)
    monkeypatch.setattr("appcore.db.execute", lambda *a, **kw: None)

    normal_user = {"id": 2, "username": "u", "role": "user", "is_active": 1}
    monkeypatch.setattr(
        "web.auth.get_by_id",
        lambda uid: normal_user if int(uid) == 2 else None,
    )

    from web.app import create_app
    client = create_app().test_client()
    with client.session_transaction() as sess:
        sess["_user_id"] = "2"
        sess["_fresh"] = True

    resp = client.get("/settings?tab=omni_preset")
    # superadmin_required 拒绝普通 user — 403 / 302 / 401 都算正确
    assert resp.status_code in (302, 401, 403)
