"""Tests for translate_lab deprecation (Phase 6).

不删代码 / 模板 / DB 表 / runtime — 只 deprecate 用户面入口：
- 列表页 banner + 「+ 新建任务」按钮 disabled
- POST /api/translate-lab 返回 410 Gone
- sidebar 已经没有 translate_lab 入口（spec 之前误信，实际 layout.html
  压根没有这个 link）
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def translate_lab_list_html():
    root = Path(__file__).resolve().parents[1]
    return (root / "web" / "templates" / "translate_lab_list.html").read_text(encoding="utf-8")


@pytest.fixture
def translate_lab_route_py():
    root = Path(__file__).resolve().parents[1]
    return (root / "web" / "routes" / "translate_lab.py").read_text(encoding="utf-8")


@pytest.fixture
def layout_html():
    root = Path(__file__).resolve().parents[1]
    return (root / "web" / "templates" / "layout.html").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Template: deprecation banner + new-task button disabled
# ---------------------------------------------------------------------------


def test_list_page_has_deprecation_banner(translate_lab_list_html):
    assert 'id="translateLabDeprecatedBanner"' in translate_lab_list_html
    assert "已 deprecated" in translate_lab_list_html
    assert "lab-current" in translate_lab_list_html
    assert "/omni-translate" in translate_lab_list_html


def test_new_task_button_is_disabled(translate_lab_list_html):
    """openCreateBtn 必须有 disabled 属性 + cursor:not-allowed。"""
    # 找 openCreateBtn 行
    lines = translate_lab_list_html.splitlines()
    btn_line = None
    for i, line in enumerate(lines):
        if 'id="openCreateBtn"' in line:
            btn_line = line + lines[i + 1] if i + 1 < len(lines) else line
            # multi-line button tag — concat to next 几行
            for j in range(1, 4):
                if i + j < len(lines):
                    btn_line += lines[i + j]
                if ">" in lines[i + j]:
                    break
            break
    assert btn_line is not None, "openCreateBtn 未找到"
    assert "disabled" in btn_line
    assert "cursor:not-allowed" in btn_line


def test_sync_voice_library_button_is_disabled(translate_lab_list_html):
    """同步音色库按钮也走 deprecated（不允许新增数据）。"""
    assert 'id="syncVoiceLibraryBtn"' in translate_lab_list_html
    # 行附近含 disabled
    idx = translate_lab_list_html.find('id="syncVoiceLibraryBtn"')
    fragment = translate_lab_list_html[idx:idx + 200]
    assert "disabled" in fragment


def test_banner_uses_ocean_blue_oklch_no_purple(translate_lab_list_html):
    """banner CSS 走 oklch hue 200-240（含 amber 警告色 hue 80-85）；不能 ≥260。"""
    import re
    matches = re.findall(r"oklch\([^)]*?(\d{2,3})\s*\)", translate_lab_list_html)
    for hue_str in matches:
        try:
            hue = int(hue_str)
        except ValueError:
            continue
        if hue >= 260 and hue < 360:
            pytest.fail(f"detected purple hue {hue} in deprecation banner CSS")


# ---------------------------------------------------------------------------
# Route: POST /api/translate-lab returns 410 Gone
# ---------------------------------------------------------------------------


def test_create_endpoint_returns_410_gone(authed_client_no_db):
    resp = authed_client_no_db.post("/api/translate-lab", data={})
    assert resp.status_code == 410


def test_create_endpoint_message_redirects_user_to_omni(authed_client_no_db):
    resp = authed_client_no_db.post("/api/translate-lab", data={})
    body = resp.get_json() or {}
    msg = (body.get("error") or body.get("message") or "")
    assert "/omni-translate" in msg
    assert "lab-current" in msg


def test_route_short_circuits_before_business_logic(translate_lab_route_py):
    """upload_and_create 函数 body 头部立即 return 410，老 body 仅作 dead code 保留（Phase 6 防御性）。"""
    idx = translate_lab_route_py.find("def upload_and_create():")
    assert idx > 0
    body_window = translate_lab_route_py[idx:idx + 1500]
    # 第一个 return 必须出现在 docstring 之后、其它逻辑之前
    return_idx = body_window.find("return translate_lab_flask_response")
    deprecated_msg_idx = body_window.find("deprecated")
    assert return_idx > 0
    assert deprecated_msg_idx > 0
    # deprecated marker 出现在 return 之前/之后均可，但 return 必须在 if "video" not in request 之前
    if_idx = body_window.find('if "video" not in request')
    assert if_idx > return_idx, "POST 创建必须在 410 返回之后才到达老 body"


# ---------------------------------------------------------------------------
# Sidebar: no translate_lab entry (already gone from layout.html historically)
# ---------------------------------------------------------------------------


def test_layout_sidebar_does_not_link_translate_lab(layout_html):
    assert "/translate-lab" not in layout_html
    assert "视频翻译（测试）" not in layout_html


# ---------------------------------------------------------------------------
# Defensive preservation: detail/get/start/resume routes still exist
# ---------------------------------------------------------------------------


def test_detail_get_routes_preserved(translate_lab_route_py):
    """老任务详情页 + GET 端点不动。"""
    assert '@bp.route("/translate-lab/<task_id>")' in translate_lab_route_py
    assert '@bp.route("/api/translate-lab/<task_id>", methods=["GET"])' in translate_lab_route_py


def test_runtime_v2_module_preserved():
    """runtime_v2.py 文件存在（不物理删除）。"""
    root = Path(__file__).resolve().parents[1]
    p = root / "appcore" / "runtime_v2.py"
    assert p.exists(), "runtime_v2.py 不能被删除（Phase 6 spec 防御性保留）"
