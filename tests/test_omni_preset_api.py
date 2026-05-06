"""Tests for web/routes/omni_preset_api.py (Phase 1).

DAO 用 monkeypatch 完全 stub 掉，专注测：
- 权限矩阵（admin / 普通 user / 未登录）
- 输入校验（name 长度、plugin_config 合法性）
- 路由响应 schema（含 capability_groups）
- silent fix 通过 API 也生效
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# In-memory DAO stub
# ---------------------------------------------------------------------------


class _DAOStub:
    def __init__(self):
        self.presets: dict[int, dict] = {}
        self.next_id = 0
        self.default_id: int | None = None

    def list_for_user(self, user_id):
        return [
            p for p in self.presets.values()
            if p["scope"] == "system" or p.get("user_id") == user_id
        ]

    def list_system(self):
        return [p for p in self.presets.values() if p["scope"] == "system"]

    def get(self, preset_id):
        return self.presets.get(preset_id)

    def create_user_preset(self, user_id, name, description, plugin_config):
        self.next_id += 1
        self.presets[self.next_id] = {
            "id": self.next_id, "scope": "user", "user_id": user_id,
            "name": name, "description": description,
            "plugin_config": plugin_config,
            "created_at": None, "updated_at": None,
        }
        return self.next_id

    def create_system_preset(self, name, description, plugin_config):
        self.next_id += 1
        self.presets[self.next_id] = {
            "id": self.next_id, "scope": "system", "user_id": None,
            "name": name, "description": description,
            "plugin_config": plugin_config,
            "created_at": None, "updated_at": None,
        }
        return self.next_id

    def update(self, preset_id, *, name=None, description=None, plugin_config=None):
        p = self.presets.get(preset_id)
        if not p:
            return False
        if name is not None:
            p["name"] = name
        if description is not None:
            p["description"] = description
        if plugin_config is not None:
            p["plugin_config"] = plugin_config
        return True

    def delete(self, preset_id):
        if self.default_id == preset_id:
            return False
        return self.presets.pop(preset_id, None) is not None

    def get_default_id(self):
        return self.default_id

    def get_default(self):
        if self.default_id is not None:
            return self.presets.get(self.default_id)
        sys_list = self.list_system()
        return sys_list[0] if sys_list else None

    def set_default(self, preset_id):
        p = self.presets.get(preset_id)
        if not p or p["scope"] != "system":
            return False
        self.default_id = preset_id
        return True


@pytest.fixture
def stub_dao(monkeypatch):
    dao = _DAOStub()
    for fn_name in [
        "list_for_user", "list_system", "get",
        "create_user_preset", "create_system_preset",
        "update", "delete",
        "get_default_id", "get_default", "set_default",
    ]:
        monkeypatch.setattr(
            f"appcore.omni_preset_dao.{fn_name}", getattr(dao, fn_name)
        )
    # api 模块 import 时是 `from appcore import omni_preset_dao`，attr 是
    # module-level binding；patch module 内的 attr 即可影响 api blueprint。
    monkeypatch.setattr(
        "web.routes.omni_preset_api.omni_preset_dao",
        type("M", (), {n: getattr(dao, n) for n in [
            "list_for_user", "list_system", "get",
            "create_user_preset", "create_system_preset",
            "update", "delete",
            "get_default_id", "get_default", "set_default",
        ]}),
    )
    return dao


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def test_unauthenticated_get_redirects(monkeypatch):
    """未登录访问 /api/omni-presets 应该被 login_required 拦截。"""
    monkeypatch.setattr("web.app._run_startup_recovery", lambda: None)
    monkeypatch.setattr("web.app.recover_all_interrupted_tasks", lambda: None)
    monkeypatch.setattr("web.app.mark_interrupted_bulk_translate_tasks", lambda: None)
    monkeypatch.setattr("web.app._seed_default_prompts", lambda: None)
    monkeypatch.setattr("appcore.db.execute", lambda *a, **kw: None)

    from web.app import create_app
    client = create_app().test_client()
    resp = client.get("/api/omni-presets")
    # login_required 默认 302 → /login（也可能 401，看 flask_login 配置）
    assert resp.status_code in (302, 401)


# ---------------------------------------------------------------------------
# GET /api/omni-presets
# ---------------------------------------------------------------------------


def test_list_returns_visible_presets_plus_default_id_and_capability_groups(
    authed_client_no_db, stub_dao,
):
    stub_dao.create_system_preset("sys1", "", {"x": 1})
    stub_dao.create_user_preset(1, "alice-1", "", {"x": 2})
    stub_dao.set_default(1)

    resp = authed_client_no_db.get("/api/omni-presets")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["default_preset_id"] == 1
    names = {p["name"] for p in data["presets"]}
    assert names == {"sys1", "alice-1"}
    # capability_groups 元数据带回（前端表单用）
    assert isinstance(data["capability_groups"], list)
    assert len(data["capability_groups"]) == 8


# ---------------------------------------------------------------------------
# GET /api/omni-presets/default
# ---------------------------------------------------------------------------


def test_get_default_returns_preset_when_set(authed_client_no_db, stub_dao):
    pid = stub_dao.create_system_preset("sys", "", {"a": 1})
    stub_dao.set_default(pid)
    resp = authed_client_no_db.get("/api/omni-presets/default")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["preset"]["id"] == pid
    assert data["preset"]["plugin_config"] == {"a": 1}


def test_get_default_returns_fallback_config_when_no_preset(
    authed_client_no_db, stub_dao,
):
    resp = authed_client_no_db.get("/api/omni-presets/default")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["preset"] is None
    # 兜底 plugin_config 是 omni-current 基线
    assert data["fallback_plugin_config"]["asr_post"] == "asr_clean"


# ---------------------------------------------------------------------------
# POST /api/omni-presets — create
# ---------------------------------------------------------------------------


def _baseline_cfg():
    return {
        "asr_post": "asr_clean",
        "shot_decompose": False,
        "translate_algo": "standard",
        "source_anchored": True,
        "tts_strategy": "five_round_rewrite",
        "subtitle": "asr_realign",
        "voice_separation": True,
        "loudness_match": True,
    }


def test_create_user_preset_succeeds(authed_client_no_db, stub_dao):
    resp = authed_client_no_db.post(
        "/api/omni-presets",
        json={"name": "my preset", "description": "test",
              "plugin_config": _baseline_cfg()},
    )
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["preset"]["scope"] == "user"
    assert data["preset"]["user_id"] == 1
    assert data["preset"]["name"] == "my preset"


def test_create_rejects_empty_name(authed_client_no_db, stub_dao):
    resp = authed_client_no_db.post(
        "/api/omni-presets",
        json={"name": "  ", "plugin_config": _baseline_cfg()},
    )
    assert resp.status_code == 400


def test_create_rejects_oversized_name(authed_client_no_db, stub_dao):
    resp = authed_client_no_db.post(
        "/api/omni-presets",
        json={"name": "a" * 65, "plugin_config": _baseline_cfg()},
    )
    assert resp.status_code == 400


def test_create_rejects_invalid_plugin_config(authed_client_no_db, stub_dao):
    bad = _baseline_cfg()
    bad["asr_post"] = "magic"
    resp = authed_client_no_db.post(
        "/api/omni-presets",
        json={"name": "x", "plugin_config": bad},
    )
    assert resp.status_code == 400


def test_create_silent_fixes_av_sentence_with_source_anchored(
    authed_client_no_db, stub_dao,
):
    cfg = _baseline_cfg()
    cfg["translate_algo"] = "av_sentence"
    cfg["source_anchored"] = True
    resp = authed_client_no_db.post(
        "/api/omni-presets",
        json={"name": "x", "plugin_config": cfg},
    )
    assert resp.status_code == 201
    saved = resp.get_json()["preset"]["plugin_config"]
    # source_anchored 在 validator 里被 silent fix 成 False
    assert saved["source_anchored"] is False


def test_admin_can_create_system_preset(authed_client_no_db, stub_dao):
    resp = authed_client_no_db.post(
        "/api/omni-presets",
        json={"name": "sys1", "scope": "system", "plugin_config": _baseline_cfg()},
    )
    assert resp.status_code == 201
    assert resp.get_json()["preset"]["scope"] == "system"


def test_normal_user_cannot_create_system_preset(authed_user_client_no_db, stub_dao):
    resp = authed_user_client_no_db.post(
        "/api/omni-presets",
        json={"name": "sys1", "scope": "system", "plugin_config": _baseline_cfg()},
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# PUT /api/omni-presets/<id>
# ---------------------------------------------------------------------------


def test_user_can_update_own_preset(authed_client_no_db, stub_dao):
    pid = stub_dao.create_user_preset(1, "old", "", _baseline_cfg())
    resp = authed_client_no_db.put(
        f"/api/omni-presets/{pid}", json={"name": "new"},
    )
    assert resp.status_code == 200
    assert stub_dao.presets[pid]["name"] == "new"


def test_user_cannot_update_others_preset(authed_user_client_no_db, stub_dao):
    pid = stub_dao.create_user_preset(99, "other", "", _baseline_cfg())
    resp = authed_user_client_no_db.put(
        f"/api/omni-presets/{pid}", json={"name": "hijack"},
    )
    assert resp.status_code == 403


def test_normal_user_cannot_update_system_preset(authed_user_client_no_db, stub_dao):
    pid = stub_dao.create_system_preset("sys", "", _baseline_cfg())
    resp = authed_user_client_no_db.put(
        f"/api/omni-presets/{pid}", json={"name": "hack"},
    )
    assert resp.status_code == 403


def test_admin_can_update_system_preset(authed_client_no_db, stub_dao):
    pid = stub_dao.create_system_preset("sys", "", _baseline_cfg())
    resp = authed_client_no_db.put(
        f"/api/omni-presets/{pid}", json={"name": "renamed"},
    )
    assert resp.status_code == 200
    assert stub_dao.presets[pid]["name"] == "renamed"


def test_update_unknown_id_returns_404(authed_client_no_db, stub_dao):
    resp = authed_client_no_db.put("/api/omni-presets/9999", json={"name": "x"})
    assert resp.status_code == 404


def test_update_invalid_plugin_config_returns_400(authed_client_no_db, stub_dao):
    pid = stub_dao.create_user_preset(1, "x", "", _baseline_cfg())
    bad = _baseline_cfg()
    bad["tts_strategy"] = "wat"
    resp = authed_client_no_db.put(
        f"/api/omni-presets/{pid}", json={"plugin_config": bad},
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# DELETE /api/omni-presets/<id>
# ---------------------------------------------------------------------------


def test_user_can_delete_own_preset(authed_client_no_db, stub_dao):
    pid = stub_dao.create_user_preset(1, "x", "", _baseline_cfg())
    resp = authed_client_no_db.delete(f"/api/omni-presets/{pid}")
    assert resp.status_code == 200
    assert pid not in stub_dao.presets


def test_user_cannot_delete_others_preset(authed_user_client_no_db, stub_dao):
    pid = stub_dao.create_user_preset(99, "x", "", _baseline_cfg())
    resp = authed_user_client_no_db.delete(f"/api/omni-presets/{pid}")
    assert resp.status_code == 403


def test_delete_refuses_default_preset(authed_client_no_db, stub_dao):
    pid = stub_dao.create_system_preset("sys", "", _baseline_cfg())
    stub_dao.set_default(pid)
    resp = authed_client_no_db.delete(f"/api/omni-presets/{pid}")
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# POST /api/omni-presets/<id>/set-as-default
# ---------------------------------------------------------------------------


def test_admin_can_set_default(authed_client_no_db, stub_dao):
    pid = stub_dao.create_system_preset("sys", "", _baseline_cfg())
    resp = authed_client_no_db.post(f"/api/omni-presets/{pid}/set-as-default")
    assert resp.status_code == 200
    assert stub_dao.default_id == pid


def test_normal_user_cannot_set_default(authed_user_client_no_db, stub_dao):
    pid = stub_dao.create_system_preset("sys", "", _baseline_cfg())
    resp = authed_user_client_no_db.post(f"/api/omni-presets/{pid}/set-as-default")
    assert resp.status_code == 403


def test_set_default_rejects_user_preset(authed_client_no_db, stub_dao):
    pid = stub_dao.create_user_preset(1, "u", "", _baseline_cfg())
    resp = authed_client_no_db.post(f"/api/omni-presets/{pid}/set-as-default")
    assert resp.status_code == 400
