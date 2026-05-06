"""Tests for appcore.omni_preset_dao (Phase 1).

DAO 用 monkeypatch 把 ``appcore.db`` 的 query / query_one / execute 替换成
in-memory store；专注测 DAO 层的 SQL 流和分支。SQL 字符串本身的语法由
``test_omni_preset_dao_smoke`` 在真 DB 上跑（如果有 DB）覆盖。
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from appcore import omni_preset_dao


@pytest.fixture
def fake_db(monkeypatch):
    """In-memory store + LAST_INSERT_ID + system_settings 双 patch。"""
    store: list[dict] = []
    settings_store: dict[str, str] = {}
    counter = {"next_id": 0, "last_insert_id": 0}

    def _query(sql, args=()):
        sql_norm = " ".join(sql.split())
        if "FROM omni_translate_presets" in sql_norm and "OR (scope = 'user'" in sql_norm:
            user_id = args[0]
            return [
                dict(r) for r in store
                if r["scope"] == "system"
                or (r["scope"] == "user" and r["user_id"] == user_id)
            ]
        if "FROM omni_translate_presets WHERE scope = 'system'" in sql_norm:
            rows = [dict(r) for r in store if r["scope"] == "system"]
            return sorted(rows, key=lambda r: r["name"])
        return []

    def _query_one(sql, args=()):
        sql_norm = " ".join(sql.split())
        if "LAST_INSERT_ID" in sql_norm:
            return {"id": counter["last_insert_id"]}
        if "WHERE id = %s" in sql_norm and "FROM omni_translate_presets" in sql_norm:
            preset_id = args[0]
            for r in store:
                if r["id"] == preset_id:
                    return dict(r)
            return None
        return None

    def _execute(sql, args=()):
        sql_norm = " ".join(sql.split())
        if sql_norm.startswith("INSERT INTO omni_translate_presets"):
            counter["next_id"] += 1
            new_id = counter["next_id"]
            counter["last_insert_id"] = new_id
            if "VALUES ('user'" in sql_norm:
                user_id, name, description, plugin_config = args
                store.append({
                    "id": new_id, "scope": "user", "user_id": user_id,
                    "name": name, "description": description,
                    "plugin_config": plugin_config,
                    "created_at": datetime.now(), "updated_at": datetime.now(),
                })
            elif "VALUES ('system'" in sql_norm:
                name, description, plugin_config = args
                store.append({
                    "id": new_id, "scope": "system", "user_id": None,
                    "name": name, "description": description,
                    "plugin_config": plugin_config,
                    "created_at": datetime.now(), "updated_at": datetime.now(),
                })
            return
        if sql_norm.startswith("UPDATE omni_translate_presets SET"):
            preset_id = args[-1]
            updates = list(args[:-1])
            for r in store:
                if r["id"] == preset_id:
                    if "name = %s" in sql_norm:
                        r["name"] = updates.pop(0)
                    if "description = %s" in sql_norm:
                        r["description"] = updates.pop(0)
                    if "plugin_config = %s" in sql_norm:
                        r["plugin_config"] = updates.pop(0)
                    r["updated_at"] = datetime.now()
                    return
            return
        if sql_norm.startswith("DELETE FROM omni_translate_presets"):
            preset_id = args[0]
            store[:] = [r for r in store if r["id"] != preset_id]
            return

    # patch DAO 内部用的 db helpers
    monkeypatch.setattr("appcore.omni_preset_dao._query", _query)
    monkeypatch.setattr("appcore.omni_preset_dao._query_one", _query_one)
    monkeypatch.setattr("appcore.omni_preset_dao._execute", _execute)
    # patch system_settings (DAO 通过 appcore.settings 拿/写默认 preset id)
    monkeypatch.setattr(
        "appcore.settings.get_setting",
        lambda key: settings_store.get(key),
    )
    monkeypatch.setattr(
        "appcore.settings.set_setting",
        lambda key, value: settings_store.__setitem__(key, value),
    )

    return {
        "store": store,
        "settings": settings_store,
        "counter": counter,
    }


# ---------------------------------------------------------------------------
# Create + Get
# ---------------------------------------------------------------------------


def test_create_user_preset_and_read_back(fake_db):
    pid = omni_preset_dao.create_user_preset(
        user_id=42, name="my preset", description="desc",
        plugin_config={"asr_post": "asr_clean"},
    )
    assert pid > 0
    preset = omni_preset_dao.get(pid)
    assert preset is not None
    assert preset["scope"] == "user"
    assert preset["user_id"] == 42
    assert preset["name"] == "my preset"
    assert preset["plugin_config"] == {"asr_post": "asr_clean"}


def test_create_system_preset(fake_db):
    pid = omni_preset_dao.create_system_preset(
        name="omni-current", description="default",
        plugin_config={"asr_post": "asr_clean"},
    )
    preset = omni_preset_dao.get(pid)
    assert preset["scope"] == "system"
    assert preset["user_id"] is None


def test_get_returns_none_for_unknown_id(fake_db):
    assert omni_preset_dao.get(999) is None


def test_plugin_config_json_decoded_when_stored_as_string(fake_db):
    """模拟真 pymysql 行为：JSON 列返回 str。"""
    fake_db["counter"]["next_id"] = 1
    fake_db["store"].append({
        "id": 1, "scope": "user", "user_id": 1,
        "name": "x", "description": None,
        "plugin_config": json.dumps({"foo": "bar"}),
        "created_at": datetime.now(), "updated_at": datetime.now(),
    })
    preset = omni_preset_dao.get(1)
    assert preset["plugin_config"] == {"foo": "bar"}


# ---------------------------------------------------------------------------
# List + scope isolation
# ---------------------------------------------------------------------------


def test_list_for_user_returns_system_plus_own_user_presets(fake_db):
    omni_preset_dao.create_system_preset("sys1", "", {"a": 1})
    omni_preset_dao.create_user_preset(42, "alice-1", "", {"a": 2})
    omni_preset_dao.create_user_preset(99, "bob-1", "", {"a": 3})

    alice_view = omni_preset_dao.list_for_user(42)
    names = {p["name"] for p in alice_view}
    assert names == {"sys1", "alice-1"}

    bob_view = omni_preset_dao.list_for_user(99)
    names = {p["name"] for p in bob_view}
    assert names == {"sys1", "bob-1"}


def test_list_system_returns_only_system_presets(fake_db):
    omni_preset_dao.create_system_preset("sys1", "", {})
    omni_preset_dao.create_user_preset(42, "user1", "", {})
    out = omni_preset_dao.list_system()
    assert len(out) == 1
    assert out[0]["scope"] == "system"


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


def test_update_partial_only_changes_specified_fields(fake_db):
    pid = omni_preset_dao.create_user_preset(1, "old name", "old desc", {"a": 1})
    omni_preset_dao.update(pid, name="new name")
    preset = omni_preset_dao.get(pid)
    assert preset["name"] == "new name"
    assert preset["description"] == "old desc"
    assert preset["plugin_config"] == {"a": 1}


def test_update_plugin_config(fake_db):
    pid = omni_preset_dao.create_user_preset(1, "n", None, {"a": 1})
    omni_preset_dao.update(pid, plugin_config={"a": 2, "b": 3})
    preset = omni_preset_dao.get(pid)
    assert preset["plugin_config"] == {"a": 2, "b": 3}


def test_update_unknown_id_returns_false(fake_db):
    assert omni_preset_dao.update(999, name="x") is False


# ---------------------------------------------------------------------------
# Delete + default lock
# ---------------------------------------------------------------------------


def test_delete_user_preset(fake_db):
    pid = omni_preset_dao.create_user_preset(1, "n", None, {})
    assert omni_preset_dao.delete(pid) is True
    assert omni_preset_dao.get(pid) is None


def test_delete_refuses_when_currently_default(fake_db):
    pid = omni_preset_dao.create_system_preset("sys", "", {})
    omni_preset_dao.set_default(pid)
    # delete should be refused
    assert omni_preset_dao.delete(pid) is False
    # preset still there
    assert omni_preset_dao.get(pid) is not None


# ---------------------------------------------------------------------------
# Default
# ---------------------------------------------------------------------------


def test_set_default_succeeds_for_system_preset(fake_db):
    pid = omni_preset_dao.create_system_preset("sys", "", {})
    assert omni_preset_dao.set_default(pid) is True
    assert omni_preset_dao.get_default_id() == pid


def test_set_default_refuses_user_preset(fake_db):
    pid = omni_preset_dao.create_user_preset(1, "u", None, {})
    assert omni_preset_dao.set_default(pid) is False


def test_set_default_refuses_unknown_preset(fake_db):
    assert omni_preset_dao.set_default(99999) is False


def test_get_default_returns_full_preset_when_set(fake_db):
    pid = omni_preset_dao.create_system_preset("sys", "", {"a": 1})
    omni_preset_dao.set_default(pid)
    preset = omni_preset_dao.get_default()
    assert preset is not None
    assert preset["id"] == pid


def test_get_default_falls_back_to_first_system_preset_when_unset(fake_db):
    omni_preset_dao.create_system_preset("z-sys", "", {})
    omni_preset_dao.create_system_preset("a-sys", "", {})
    # 没设默认 → 取系统级第一个（按 name ASC，应该是 "a-sys"）
    preset = omni_preset_dao.get_default()
    assert preset is not None
    assert preset["name"] == "a-sys"


def test_get_default_returns_none_when_no_presets(fake_db):
    assert omni_preset_dao.get_default() is None
