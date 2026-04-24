"""Unit tests for appcore.llm_provider_configs DAO.

关键契约：
  - 所有供应商凭据只从 llm_provider_configs 表读取，绝不回落到 os.environ / dotenv
  - 每个功能入口独立 provider_code，不做跨 provider fallback
  - 缺 api_key 时抛出带 provider_code 的明确错误
  - save 是部分字段更新，未传的字段保留 DB 当前值
"""
from __future__ import annotations

import inspect
import json

import pytest

from appcore import llm_provider_configs as lpc


# ---------------------------------------------------------------------------
# In-memory fake DB
# ---------------------------------------------------------------------------

class _FakeDB:
    """模拟 llm_provider_configs 单表：provider_code 主键 upsert。"""

    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.executes: list[tuple[str, tuple]] = []

    def seed(self, provider_code: str, **kwargs) -> None:
        base = {
            "provider_code": provider_code,
            "display_name": kwargs.get("display_name", provider_code),
            "group_code": kwargs.get("group_code", "llm"),
            "api_key": None,
            "base_url": None,
            "model_id": None,
            "extra_config": None,
            "enabled": 1,
            "updated_by": None,
        }
        base.update({k: v for k, v in kwargs.items() if k != "display_name"})
        self.rows[provider_code] = base

    def query(self, sql: str, args: tuple = ()) -> list[dict]:
        low = sql.lower()
        if "where provider_code" in low:
            row = self.rows.get(args[0])
            return [dict(row)] if row else []
        ordered = sorted(self.rows.values(), key=lambda r: (r.get("group_code") or "", r["provider_code"]))
        return [dict(r) for r in ordered]

    def query_one(self, sql: str, args: tuple = ()):
        out = self.query(sql, args)
        return out[0] if out else None

    def execute(self, sql: str, args: tuple = ()) -> int:
        self.executes.append((sql, args))
        # DAO always passes full row on upsert:
        # (provider_code, display_name, group_code, api_key, base_url,
        #  model_id, extra_config, enabled, updated_by)
        (provider_code, display_name, group_code, api_key, base_url,
         model_id, extra_config, enabled, updated_by) = args
        self.rows[provider_code] = {
            "provider_code": provider_code,
            "display_name": display_name,
            "group_code": group_code,
            "api_key": api_key,
            "base_url": base_url,
            "model_id": model_id,
            "extra_config": extra_config,
            "enabled": enabled,
            "updated_by": updated_by,
        }
        return 1


@pytest.fixture
def fake_db(monkeypatch):
    db = _FakeDB()
    monkeypatch.setattr(lpc, "query", db.query)
    monkeypatch.setattr(lpc, "query_one", db.query_one)
    monkeypatch.setattr(lpc, "execute", db.execute)
    return db


# ---------------------------------------------------------------------------
# get_provider_config / list_provider_configs
# ---------------------------------------------------------------------------

def test_get_provider_config_returns_none_when_missing(fake_db):
    assert lpc.get_provider_config("not_seeded") is None


def test_get_provider_config_parses_json_extra_config(fake_db):
    fake_db.seed(
        "doubao_asr",
        api_key="ak-live",
        extra_config=json.dumps({"app_id": "123", "cluster": "volc.seedasr.auc"}),
    )
    cfg = lpc.get_provider_config("doubao_asr")
    assert isinstance(cfg, lpc.LlmProviderConfig)
    assert cfg.provider_code == "doubao_asr"
    assert cfg.api_key == "ak-live"
    assert cfg.extra_config == {"app_id": "123", "cluster": "volc.seedasr.auc"}


def test_get_provider_config_tolerates_dict_extra_config(fake_db):
    # MySQL JSON column can also be returned as dict by some drivers
    fake_db.seed("doubao_asr", extra_config={"app_id": "999"})
    cfg = lpc.get_provider_config("doubao_asr")
    assert cfg.extra_config == {"app_id": "999"}


def test_get_provider_config_empty_extra_config_becomes_dict(fake_db):
    fake_db.seed("openrouter_text", api_key="x")
    cfg = lpc.get_provider_config("openrouter_text")
    assert cfg.extra_config == {}


def test_list_provider_configs_returns_all_rows(fake_db):
    fake_db.seed("openrouter_text", api_key="a", group_code="text_llm")
    fake_db.seed("openrouter_image", api_key="b", group_code="image")
    fake_db.seed("doubao_asr", group_code="asr")
    codes = [cfg.provider_code for cfg in lpc.list_provider_configs()]
    assert set(codes) == {"openrouter_text", "openrouter_image", "doubao_asr"}


# ---------------------------------------------------------------------------
# require_provider_api_key 与错误信息
# ---------------------------------------------------------------------------

def test_require_provider_api_key_returns_value_when_present(fake_db):
    fake_db.seed("elevenlabs_tts", api_key="sk_live_xxx")
    assert lpc.require_provider_api_key("elevenlabs_tts") == "sk_live_xxx"


def test_require_provider_api_key_raises_with_provider_code_in_message(fake_db):
    fake_db.seed("doubao_seedream", api_key=None)
    with pytest.raises(lpc.ProviderConfigError) as exc_info:
        lpc.require_provider_api_key("doubao_seedream")
    msg = str(exc_info.value)
    assert "doubao_seedream" in msg
    assert "api_key" in msg
    # 必须明确指向 /settings，方便运维
    assert "settings" in msg.lower() or "配置" in msg


def test_require_provider_api_key_raises_when_row_missing(fake_db):
    # DB 完全空；不应偷偷读 env
    with pytest.raises(lpc.ProviderConfigError):
        lpc.require_provider_api_key("openrouter_text")


def test_seedream_does_not_reuse_doubao_llm_key(fake_db):
    """即使 doubao_llm 配好了，doubao_seedream 也必须独立报错。"""
    fake_db.seed("doubao_llm", api_key="ark-key-shared")
    with pytest.raises(lpc.ProviderConfigError) as exc_info:
        lpc.require_provider_api_key("doubao_seedream")
    assert "doubao_seedream" in str(exc_info.value)


def test_openrouter_text_and_image_are_independent(fake_db):
    fake_db.seed("openrouter_text", api_key="text-key")
    # openrouter_image 留空
    assert lpc.require_provider_api_key("openrouter_text") == "text-key"
    with pytest.raises(lpc.ProviderConfigError):
        lpc.require_provider_api_key("openrouter_image")


def test_gemini_aistudio_text_and_image_are_independent(fake_db):
    fake_db.seed("gemini_aistudio_text", api_key="t-key")
    with pytest.raises(lpc.ProviderConfigError):
        lpc.require_provider_api_key("gemini_aistudio_image")


def test_gemini_cloud_text_and_image_are_independent(fake_db):
    fake_db.seed("gemini_cloud_text", api_key="c-key")
    with pytest.raises(lpc.ProviderConfigError):
        lpc.require_provider_api_key("gemini_cloud_image")


# ---------------------------------------------------------------------------
# save_provider_config
# ---------------------------------------------------------------------------

def test_save_provider_config_partial_update_preserves_other_columns(fake_db):
    fake_db.seed(
        "openrouter_text",
        api_key="old-key",
        base_url="https://old.example/api",
        model_id="old-model",
    )
    lpc.save_provider_config("openrouter_text", {"api_key": "new-key"}, updated_by=42)

    cfg = lpc.get_provider_config("openrouter_text")
    assert cfg.api_key == "new-key"
    assert cfg.base_url == "https://old.example/api"
    assert cfg.model_id == "old-model"
    assert cfg.updated_by == 42


def test_save_provider_config_updates_extra_config_dict(fake_db):
    fake_db.seed("doubao_asr")
    lpc.save_provider_config(
        "doubao_asr",
        {"api_key": "ak", "extra_config": {"app_id": "321", "cluster": "c1"}},
        updated_by=7,
    )
    cfg = lpc.get_provider_config("doubao_asr")
    assert cfg.api_key == "ak"
    assert cfg.extra_config == {"app_id": "321", "cluster": "c1"}


def test_save_provider_config_clears_extra_config_with_empty_dict(fake_db):
    fake_db.seed("doubao_asr", extra_config=json.dumps({"app_id": "321"}))
    lpc.save_provider_config("doubao_asr", {"extra_config": {}}, updated_by=1)
    cfg = lpc.get_provider_config("doubao_asr")
    assert cfg.extra_config == {}


def test_save_provider_config_rejects_unknown_provider_code(fake_db):
    with pytest.raises(lpc.ProviderConfigError):
        lpc.save_provider_config("not_registered", {"api_key": "x"}, updated_by=1)


def test_save_provider_config_trims_whitespace_in_string_fields(fake_db):
    fake_db.seed("openrouter_text")
    lpc.save_provider_config(
        "openrouter_text",
        {"api_key": "  ak-with-space  ", "base_url": "  "},
        updated_by=1,
    )
    cfg = lpc.get_provider_config("openrouter_text")
    assert cfg.api_key == "ak-with-space"
    # 全空白清空为 None
    assert cfg.base_url is None


def test_save_provider_config_creates_row_when_admin_first_saves(fake_db):
    # DB 本来没 row（比如 migration seed 被手动删除过），save 要能创建
    assert lpc.get_provider_config("apimart_image") is None
    lpc.save_provider_config(
        "apimart_image",
        {"api_key": "ak", "base_url": "https://api.apimart.ai"},
        updated_by=1,
    )
    cfg = lpc.get_provider_config("apimart_image")
    assert cfg is not None
    assert cfg.api_key == "ak"
    assert cfg.base_url == "https://api.apimart.ai"


# ---------------------------------------------------------------------------
# credential_provider_for_adapter
# ---------------------------------------------------------------------------

def test_credential_provider_for_adapter_splits_text_and_image():
    assert lpc.credential_provider_for_adapter("openrouter") == "openrouter_text"
    assert lpc.credential_provider_for_adapter("openrouter", media_kind="image") == "openrouter_image"
    assert lpc.credential_provider_for_adapter("gemini_aistudio") == "gemini_aistudio_text"
    assert lpc.credential_provider_for_adapter("gemini_aistudio", media_kind="image") == "gemini_aistudio_image"
    assert lpc.credential_provider_for_adapter("gemini_vertex") == "gemini_cloud_text"
    assert lpc.credential_provider_for_adapter("gemini_vertex", media_kind="image") == "gemini_cloud_image"


def test_credential_provider_for_adapter_single_row_providers():
    assert lpc.credential_provider_for_adapter("doubao") == "doubao_llm"
    assert lpc.credential_provider_for_adapter("doubao_asr") == "doubao_asr"
    assert lpc.credential_provider_for_adapter("doubao_seedream") == "doubao_seedream"
    assert lpc.credential_provider_for_adapter("seedance") == "seedance_video"
    assert lpc.credential_provider_for_adapter("elevenlabs") == "elevenlabs_tts"
    assert lpc.credential_provider_for_adapter("apimart") == "apimart_image"


def test_credential_provider_for_adapter_rejects_unknown():
    with pytest.raises(lpc.ProviderConfigError):
        lpc.credential_provider_for_adapter("made_up_provider")


# ---------------------------------------------------------------------------
# 静态检查：模块源码本身不得引用 env / dotenv
# ---------------------------------------------------------------------------

def test_dao_module_does_not_import_os_or_dotenv():
    src = inspect.getsource(lpc)
    assert "import os" not in src, "DAO 禁止 import os"
    assert "from os" not in src, "DAO 禁止 from os import ..."
    assert "import dotenv" not in src and "from dotenv" not in src, "DAO 禁止 dotenv"
    assert "os.environ" not in src
    assert "os.getenv" not in src
    assert "load_dotenv" not in src


# ---------------------------------------------------------------------------
# 环境变量穿透检查：env 里有也绝不能影响
# ---------------------------------------------------------------------------

def test_no_env_fallback_at_all(monkeypatch, fake_db):
    monkeypatch.setenv("OPENROUTER_API_KEY", "env-leak")
    monkeypatch.setenv("DOUBAO_LLM_API_KEY", "env-leak")
    monkeypatch.setenv("GEMINI_API_KEY", "env-leak")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "env-leak")
    assert lpc.get_provider_config("openrouter_text") is None
    assert lpc.get_provider_config("doubao_llm") is None
    assert lpc.get_provider_config("gemini_aistudio_text") is None
    assert lpc.get_provider_config("elevenlabs_tts") is None
    with pytest.raises(lpc.ProviderConfigError):
        lpc.require_provider_api_key("openrouter_text")
