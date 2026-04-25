"""api_keys 在 2026-04-25 之后只剩"用户/管理员级非供应商配置"：

  - 供应商 service（openrouter/doubao_llm/...）→ 通过 _LEGACY_SERVICE_MAP 转发到
    llm_provider_configs；resolve_key 不再回落 env。
  - jianying 等用户级配置继续留在 api_keys 表。
  - admin 偏好 translate_pref 继续留在 api_keys 表。
"""
import json

import pytest

from appcore import api_keys, llm_provider_configs
from appcore.api_keys import get_all, get_key, resolve_extra, resolve_key, set_key


@pytest.fixture
def fake_stores(monkeypatch):
    """Stub out both api_keys 表 和 llm_provider_configs 表。

    api_keys rows: {(user_id, service): {"key_value", "extra_config"}}
    llm_provider_configs rows: {provider_code: {...}}
    """
    users = {
        1: {"id": 1, "username": "admin", "is_active": 1},
        2: {"id": 2, "username": "alice", "is_active": 1},
    }
    api_rows: dict[tuple[int, str], dict] = {}
    provider_rows: dict[str, dict] = {}

    def _api_query_one(sql, params=()):
        if "FROM users WHERE username = %s" in sql:
            username = params[0]
            return next((row for row in users.values() if row["username"] == username), None)
        if "FROM users WHERE id = %s" in sql:
            return users.get(int(params[0]))
        if "SELECT key_value FROM api_keys" in sql:
            row = api_rows.get((int(params[0]), params[1]))
            return {"key_value": row["key_value"]} if row else None
        if "SELECT extra_config FROM api_keys" in sql:
            row = api_rows.get((int(params[0]), params[1]))
            return {"extra_config": row["extra_config"]} if row else None
        return None

    def _api_query(sql, params=()):
        if "FROM api_keys WHERE user_id = %s" in sql:
            uid = int(params[0])
            return [
                {
                    "service": service,
                    "key_value": row["key_value"],
                    "extra_config": row["extra_config"],
                }
                for (row_uid, service), row in api_rows.items()
                if row_uid == uid
            ]
        return []

    def _api_execute(sql, params=()):
        uid, service, key_value, extra_json = params
        api_rows[(int(uid), service)] = {
            "key_value": key_value,
            "extra_config": extra_json,
        }
        return 1

    def _provider_query_one(sql, params=()):
        if "where provider_code = %s" in sql.lower():
            row = provider_rows.get(params[0])
            return dict(row) if row else None
        return None

    def _provider_query(sql, params=()):
        return [dict(r) for r in provider_rows.values()]

    def _provider_execute(sql, params=()):
        (code, display, group, api_key, base_url, model, extra, enabled, updated_by) = params
        provider_rows[code] = {
            "provider_code": code, "display_name": display, "group_code": group,
            "api_key": api_key, "base_url": base_url, "model_id": model,
            "extra_config": extra, "enabled": enabled, "updated_by": updated_by,
        }
        return 1

    monkeypatch.setattr("appcore.api_keys.query_one", _api_query_one)
    monkeypatch.setattr("appcore.api_keys.query", _api_query)
    monkeypatch.setattr("appcore.api_keys.execute", _api_execute)
    monkeypatch.setattr(llm_provider_configs, "query_one", _provider_query_one)
    monkeypatch.setattr(llm_provider_configs, "query", _provider_query)
    monkeypatch.setattr(llm_provider_configs, "execute", _provider_execute)

    class Handle:
        def seed_provider(self, code, **kwargs):
            base = {
                "provider_code": code,
                "display_name": kwargs.pop("display_name", code),
                "group_code": kwargs.pop("group_code", "llm"),
                "api_key": None, "base_url": None, "model_id": None,
                "extra_config": None, "enabled": 1, "updated_by": None,
            }
            base.update(kwargs)
            provider_rows[code] = base

        @property
        def api_rows(self):
            return api_rows

        @property
        def provider_rows(self):
            return provider_rows

    return Handle()


# ---------------------------------------------------------------------------
# resolve_key / resolve_extra —— 供应商 service 走 llm_provider_configs
# ---------------------------------------------------------------------------

def test_resolve_key_reads_provider_configs_for_openrouter(fake_stores):
    fake_stores.seed_provider("openrouter_text", api_key="db-openrouter")
    assert resolve_key(2, "openrouter") == "db-openrouter"
    # user_id 不影响供应商 key；任何用户都拿到相同的 admin 级 DB 值
    assert resolve_key(None, "openrouter") == "db-openrouter"


def test_resolve_key_for_supplier_does_not_fall_back_to_env(fake_stores, monkeypatch):
    """核心安全属性：env 里再怎么写也不能参与供应商 key 解析。"""
    monkeypatch.setenv("OPENROUTER_API_KEY", "env-leak")
    monkeypatch.setenv("DOUBAO_LLM_API_KEY", "env-leak")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "env-leak")
    # DB 里没 seed → 应返回 None，而不是 env 值
    assert resolve_key(2, "openrouter") is None
    assert resolve_key(2, "doubao_llm") is None
    assert resolve_key(2, "elevenlabs") is None


def test_resolve_key_volc_legacy_service_routes_to_doubao_asr(fake_stores):
    fake_stores.seed_provider("doubao_asr", api_key="asr-db-key")
    # 老代码中 runtime.py 曾用 "volc" 作为 service 名
    assert resolve_key(1, "volc") == "asr-db-key"
    assert resolve_key(1, "doubao_asr") == "asr-db-key"


def test_resolve_extra_reads_provider_extra_and_base_url(fake_stores):
    fake_stores.seed_provider(
        "doubao_llm",
        api_key="k",
        base_url="https://ark.example/api/v3",
        model_id="custom-model",
        extra_config=json.dumps({"timeout": 30}),
    )
    extra = resolve_extra(2, "doubao_llm")
    assert extra["base_url"] == "https://ark.example/api/v3"
    assert extra["model_id"] == "custom-model"
    assert extra["timeout"] == 30


def test_resolve_key_unknown_service_returns_none(fake_stores):
    assert resolve_key(2, "totally_unknown_service") is None


# ---------------------------------------------------------------------------
# set_key —— 供应商 service 禁写，jianying/translate_pref 仍可写
# ---------------------------------------------------------------------------

def test_set_key_rejects_supplier_service_and_points_to_dao(fake_stores):
    """admin 意外直接 set_key("openrouter", ...) 时应被拒绝并指向 DAO。"""
    with pytest.raises(PermissionError, match="save_provider_config"):
        set_key(1, "openrouter", "should-not-write")
    assert (1, "openrouter") not in fake_stores.api_rows


def test_set_key_allows_jianying_for_any_user(fake_stores):
    set_key(2, "jianying", "", extra={"project_root": r"D:\Alice"})
    row = fake_stores.api_rows[(2, "jianying")]
    assert json.loads(row["extra_config"])["project_root"] == r"D:\Alice"


def test_set_key_allows_translate_pref_for_admin(fake_stores):
    set_key(1, "translate_pref", "vertex_gemini_31_pro")
    assert fake_stores.api_rows[(1, "translate_pref")]["key_value"] == "vertex_gemini_31_pro"


def test_set_key_blocks_non_admin_for_translate_pref(fake_stores):
    with pytest.raises(PermissionError):
        set_key(2, "translate_pref", "vertex_gemini_31_pro")


def test_resolve_extra_jianying_remains_user_scoped(fake_stores):
    set_key(1, "jianying", "", extra={"project_root": r"C:\AdminDrafts"})
    set_key(2, "jianying", "", extra={"project_root": r"D:\AliceDrafts"})
    assert resolve_extra(2, "jianying") == {"project_root": r"D:\AliceDrafts"}
    assert resolve_extra(1, "jianying") == {"project_root": r"C:\AdminDrafts"}


# ---------------------------------------------------------------------------
# get_all —— 合并 api_keys 表 + llm_provider_configs 以兼容老模板
# ---------------------------------------------------------------------------

def test_get_all_exposes_provider_configs_under_legacy_service_name(fake_stores):
    fake_stores.seed_provider(
        "openrouter_text", api_key="k1",
        base_url="https://openrouter.ai/api/v1", model_id="anthropic/claude-sonnet-4.6",
    )
    fake_stores.seed_provider("elevenlabs_tts", api_key="k2")
    result = get_all(2)
    assert result["openrouter"]["key_value"] == "k1"
    assert result["openrouter"]["extra"]["base_url"] == "https://openrouter.ai/api/v1"
    assert result["elevenlabs"]["key_value"] == "k2"


def test_get_all_prefers_explicit_api_keys_row_over_provider_config(fake_stores):
    """若 admin 在老 api_keys 表里手动写过 same service，保留老值（ensure 不破坏手动迁移路径）。"""
    set_key(1, "translate_pref", "vertex_gemini_31_pro")  # non-provider
    fake_stores.api_rows[(1, "openrouter")] = {"key_value": "manual-api-keys", "extra_config": None}
    fake_stores.seed_provider("openrouter_text", api_key="provider-dao")
    result = get_all(2)
    assert result["openrouter"]["key_value"] == "manual-api-keys"
