"""基础设施凭据 DAO + sync_to_runtime 行为测试。

不依赖真实 MySQL：通过 monkeypatch 替换 ``infra_credentials.list_configs``
注入假数据，验证：
  * schema 字段映射齐全
  * sync_to_runtime 把 DB JSON 字段写到 config 模块属性 + os.environ
  * DB 留空字段不会覆盖 .env 兜底值
  * tos_backup 的 ak/sk 留空时回落到 tos_main
  * DB 不可达时只 log warning，不抛错
  * save_config 拒绝未声明的 code
"""
from __future__ import annotations

import os

import pytest

from appcore import infra_credentials as ic


def test_schema_covers_three_known_codes():
    assert set(ic._CREDENTIAL_SCHEMA.keys()) == {"tos_main", "tos_backup", "vod_main"}
    assert ic.known_codes() == ["tos_main", "tos_backup", "vod_main"]


def test_schema_marks_ak_sk_as_secret_for_each_code():
    for code in ic.known_codes():
        secrets = {f.json_key for f in ic.schema_for(code) if f.is_secret}
        assert {"access_key", "secret_key"}.issubset(secrets), code


def test_schema_maps_json_keys_to_config_attrs():
    main = {f.json_key: f.config_attr for f in ic.schema_for("tos_main")}
    assert main["access_key"] == "TOS_ACCESS_KEY"
    assert main["secret_key"] == "TOS_SECRET_KEY"
    assert main["region"] == "TOS_REGION"
    assert main["media_bucket"] == "TOS_MEDIA_BUCKET"

    vod = {f.json_key: f.config_attr for f in ic.schema_for("vod_main")}
    assert vod["access_key"] == "VOD_ACCESS_KEY"
    assert vod["secret_key"] == "VOD_SECRET_KEY"


def test_sync_to_runtime_writes_db_values_to_config_and_environ(monkeypatch):
    fake_rows = [
        ic.InfraCredential(
            code="tos_main",
            display_name="x",
            group_code="object_storage",
            config={
                "access_key": "AK_FROM_DB",
                "secret_key": "SK_FROM_DB",
                "region": "cn-shanghai",
                "media_bucket": "media-bucket-from-db",
            },
            enabled=True,
        ),
        ic.InfraCredential(
            code="vod_main",
            display_name="y",
            group_code="object_storage",
            config={"access_key": "VOD_AK", "secret_key": "VOD_SK"},
            enabled=True,
        ),
    ]
    monkeypatch.setattr(ic, "list_configs", lambda: fake_rows)

    import config

    monkeypatch.setattr(config, "TOS_ACCESS_KEY", "OLD_AK")
    monkeypatch.setattr(config, "TOS_SECRET_KEY", "OLD_SK")
    monkeypatch.setattr(config, "TOS_REGION", "old-region")
    monkeypatch.setattr(config, "TOS_MEDIA_BUCKET", "old-bucket")
    monkeypatch.setattr(config, "VOD_ACCESS_KEY", "OLD_VOD_AK")
    monkeypatch.setattr(config, "VOD_SECRET_KEY", "OLD_VOD_SK")

    ic.sync_to_runtime()

    assert config.TOS_ACCESS_KEY == "AK_FROM_DB"
    assert config.TOS_SECRET_KEY == "SK_FROM_DB"
    assert config.TOS_REGION == "cn-shanghai"
    assert config.TOS_MEDIA_BUCKET == "media-bucket-from-db"
    assert config.VOD_ACCESS_KEY == "VOD_AK"
    assert config.VOD_SECRET_KEY == "VOD_SK"

    assert os.environ["TOS_ACCESS_KEY"] == "AK_FROM_DB"
    assert os.environ["TOS_SECRET_KEY"] == "SK_FROM_DB"
    assert os.environ["VOD_ACCESS_KEY"] == "VOD_AK"


def test_sync_to_runtime_does_not_overwrite_with_empty_db_value(monkeypatch):
    fake_rows = [
        ic.InfraCredential(
            code="tos_main",
            display_name="x",
            group_code="object_storage",
            config={"access_key": "", "secret_key": "  "},  # 留空 + 全空白
            enabled=True,
        ),
    ]
    monkeypatch.setattr(ic, "list_configs", lambda: fake_rows)

    import config

    monkeypatch.setattr(config, "TOS_ACCESS_KEY", "FROM_ENV_AK")
    monkeypatch.setattr(config, "TOS_SECRET_KEY", "FROM_ENV_SK")

    ic.sync_to_runtime()

    assert config.TOS_ACCESS_KEY == "FROM_ENV_AK"
    assert config.TOS_SECRET_KEY == "FROM_ENV_SK"


def test_sync_to_runtime_skips_disabled_rows(monkeypatch):
    fake_rows = [
        ic.InfraCredential(
            code="tos_main",
            display_name="x",
            group_code="object_storage",
            config={"access_key": "AK"},
            enabled=False,  # 禁用
        ),
    ]
    monkeypatch.setattr(ic, "list_configs", lambda: fake_rows)

    import config

    monkeypatch.setattr(config, "TOS_ACCESS_KEY", "FROM_ENV")
    ic.sync_to_runtime()
    assert config.TOS_ACCESS_KEY == "FROM_ENV"


def test_sync_falls_back_tos_backup_to_main_when_empty(monkeypatch):
    fake_rows = [
        ic.InfraCredential(
            code="tos_main",
            display_name="x",
            group_code="object_storage",
            config={"access_key": "MAIN_AK", "secret_key": "MAIN_SK"},
            enabled=True,
        ),
        ic.InfraCredential(
            code="tos_backup",
            display_name="y",
            group_code="object_storage",
            config={"access_key": "", "secret_key": ""},
            enabled=True,
        ),
    ]
    monkeypatch.setattr(ic, "list_configs", lambda: fake_rows)

    import config

    monkeypatch.setattr(config, "TOS_BACKUP_ACCESS_KEY", "OLD")
    monkeypatch.setattr(config, "TOS_BACKUP_SECRET_KEY", "OLD")

    ic.sync_to_runtime()

    assert config.TOS_BACKUP_ACCESS_KEY == "MAIN_AK"
    assert config.TOS_BACKUP_SECRET_KEY == "MAIN_SK"


def test_sync_keeps_tos_backup_explicit_value_over_main(monkeypatch):
    fake_rows = [
        ic.InfraCredential(
            code="tos_main",
            display_name="x",
            group_code="object_storage",
            config={"access_key": "MAIN_AK", "secret_key": "MAIN_SK"},
            enabled=True,
        ),
        ic.InfraCredential(
            code="tos_backup",
            display_name="y",
            group_code="object_storage",
            config={"access_key": "BACKUP_AK", "secret_key": "BACKUP_SK"},
            enabled=True,
        ),
    ]
    monkeypatch.setattr(ic, "list_configs", lambda: fake_rows)

    import config

    monkeypatch.setattr(config, "TOS_BACKUP_ACCESS_KEY", "OLD")
    monkeypatch.setattr(config, "TOS_BACKUP_SECRET_KEY", "OLD")

    ic.sync_to_runtime()

    assert config.TOS_BACKUP_ACCESS_KEY == "BACKUP_AK"
    assert config.TOS_BACKUP_SECRET_KEY == "BACKUP_SK"


def test_sync_swallows_db_errors(monkeypatch):
    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(ic, "list_configs", boom)
    # Should NOT raise — sync gracefully skips so the process can still
    # boot from .env fallback.
    ic.sync_to_runtime()


def test_save_config_rejects_unknown_code():
    with pytest.raises(ValueError):
        ic.save_config("bogus_code", {"access_key": "x"}, updated_by=1)


def test_save_config_strips_unknown_fields(monkeypatch):
    captured: dict = {}

    def fake_execute(sql, args):
        captured["sql"] = sql
        captured["args"] = args
        return 1

    def fake_get_config(code):
        return None

    monkeypatch.setattr(ic, "execute", fake_execute)
    monkeypatch.setattr(ic, "get_config", fake_get_config)
    monkeypatch.setattr(ic, "sync_to_runtime", lambda: None)

    ic.save_config(
        "tos_main",
        {
            "access_key": "  AK  ",          # 应 strip
            "secret_key": "SK",
            "junk": "should be ignored",     # 不在 schema 里 → 忽略
        },
        updated_by=42,
    )

    # args order: code, display_name, group_code, json_config, enabled, updated_by
    assert captured["args"][0] == "tos_main"
    config_json = captured["args"][3]
    assert "AK" in config_json
    assert "SK" in config_json
    assert "junk" not in config_json
    assert "should be ignored" not in config_json
    assert captured["args"][5] == 42
