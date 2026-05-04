"""基础设施凭据 DAO（火山 TOS / VOD / TOS 灾备）。

设计要点
--------
* DB 是凭据的唯一可信源（source of truth）。
* 业务代码继续读 ``config.TOS_ACCESS_KEY`` / ``config.VOD_ACCESS_KEY`` 等
  模块属性，不感知 DB 的存在。
* 启动时调用 :func:`sync_to_runtime` **一次**：从 DB 读 → 覆盖 ``config.XXX``
  模块属性 + ``os.environ``。
* admin 在 ``/settings?tab=infrastructure`` 保存后，:func:`save_config` 内部
  自动再调一次 :func:`sync_to_runtime`，并失效已经持有旧 ak/sk 的 SDK
  client 缓存，新值立即生效。
* 运行期间零 DB 查询，对数据库零负担。

与 ``llm_provider_configs`` 的区别
----------------------------------
* ``llm_provider_configs``：LLM/API 供应商凭据，每次业务调用现读 DB（连接池
  开销可忽略），UI 上脱敏（末四位）。
* ``infra_credentials``（本表）：基础设施凭据，启动时同步到 ``config`` +
  ``os.environ``，UI 上**明文**展示（admin 自己运维需要直接看到当前值）。
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

from appcore.db import execute, query, query_one

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CredentialField:
    """单个字段在 JSON / config 模块属性 / 环境变量之间的三向映射。"""

    json_key: str
    config_attr: str
    env_var: str
    label: str
    is_secret: bool = False


# 每个 code 的字段映射；JSON key 与 _DISPLAY_META、SQL migration 对齐。
_CREDENTIAL_SCHEMA: dict[str, list[CredentialField]] = {
    "tos_main": [
        CredentialField("access_key",            "TOS_ACCESS_KEY",            "TOS_ACCESS_KEY",            "Access Key", is_secret=True),
        CredentialField("secret_key",            "TOS_SECRET_KEY",            "TOS_SECRET_KEY",            "Secret Key", is_secret=True),
        CredentialField("region",                "TOS_REGION",                "TOS_REGION",                "Region"),
        CredentialField("bucket",                "TOS_BUCKET",                "TOS_BUCKET",                "主 Bucket（音频/任务产物）"),
        CredentialField("asr_bucket",            "TOS_ASR_BUCKET",            "TOS_ASR_BUCKET",            "ASR Bucket"),
        CredentialField("media_bucket",          "TOS_MEDIA_BUCKET",          "TOS_MEDIA_BUCKET",          "素材 Bucket"),
        CredentialField("endpoint",              "TOS_ENDPOINT",              "TOS_ENDPOINT",              "Endpoint"),
        CredentialField("public_endpoint",       "TOS_PUBLIC_ENDPOINT",       "TOS_PUBLIC_ENDPOINT",       "Public Endpoint"),
        CredentialField("private_endpoint",      "TOS_PRIVATE_ENDPOINT",      "TOS_PRIVATE_ENDPOINT",      "Private Endpoint"),
        CredentialField("prefix",                "TOS_PREFIX",                "TOS_PREFIX",                "ASR 音频 Prefix"),
        CredentialField("browser_upload_prefix", "TOS_BROWSER_UPLOAD_PREFIX", "TOS_BROWSER_UPLOAD_PREFIX", "浏览器上传 Prefix"),
        CredentialField("final_artifact_prefix", "TOS_FINAL_ARTIFACT_PREFIX", "TOS_FINAL_ARTIFACT_PREFIX", "最终产物 Prefix"),
    ],
    "tos_backup": [
        CredentialField("access_key",       "TOS_BACKUP_ACCESS_KEY",       "TOS_BACKUP_ACCESS_KEY",       "Access Key（留空则继承主 TOS）", is_secret=True),
        CredentialField("secret_key",       "TOS_BACKUP_SECRET_KEY",       "TOS_BACKUP_SECRET_KEY",       "Secret Key（留空则继承主 TOS）", is_secret=True),
        CredentialField("region",           "TOS_BACKUP_REGION",           "TOS_BACKUP_REGION",           "Region"),
        CredentialField("bucket",           "TOS_BACKUP_BUCKET",           "TOS_BACKUP_BUCKET",           "灾备 Bucket"),
        CredentialField("public_endpoint",  "TOS_BACKUP_PUBLIC_ENDPOINT",  "TOS_BACKUP_PUBLIC_ENDPOINT",  "Public Endpoint"),
        CredentialField("private_endpoint", "TOS_BACKUP_PRIVATE_ENDPOINT", "TOS_BACKUP_PRIVATE_ENDPOINT", "Private Endpoint"),
        CredentialField("prefix",           "TOS_BACKUP_PREFIX",           "TOS_BACKUP_PREFIX",           "文件 Prefix"),
        CredentialField("db_prefix",        "TOS_BACKUP_DB_PREFIX",        "TOS_BACKUP_DB_PREFIX",        "DB 备份 Prefix"),
        CredentialField("env",              "TOS_BACKUP_ENV",              "TOS_BACKUP_ENV",              "环境标记（test / prod）"),
    ],
    "vod_main": [
        CredentialField("access_key",      "VOD_ACCESS_KEY",      "VOD_ACCESS_KEY",      "Access Key", is_secret=True),
        CredentialField("secret_key",      "VOD_SECRET_KEY",      "VOD_SECRET_KEY",      "Secret Key", is_secret=True),
        CredentialField("region",          "VOD_REGION",          "VOD_REGION",          "Region"),
        CredentialField("space_name",      "VOD_SPACE_NAME",      "VOD_SPACE_NAME",      "Space 名"),
        CredentialField("playback_domain", "VOD_PLAYBACK_DOMAIN", "VOD_PLAYBACK_DOMAIN", "播放域名"),
    ],
}


# 与 SQL 种子行的 display_name / group_code 对齐
_DISPLAY_META: dict[str, tuple[str, str]] = {
    "tos_main":   ("火山引擎 TOS · 主对象存储",  "object_storage"),
    "tos_backup": ("火山引擎 TOS · 灾备桶",      "object_storage"),
    "vod_main":   ("火山引擎 VOD · 视频点播",    "object_storage"),
}


# 在 settings 页和后台展示时的分组顺序
GROUP_ORDER: list[tuple[str, str]] = [
    ("object_storage", "对象存储 / 视频点播"),
]


@dataclass(frozen=True)
class InfraCredential:
    code: str
    display_name: str
    group_code: str
    config: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    updated_by: int | None = None


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------

def _coerce_config(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return dict(parsed) if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _row_to_cred(row: dict) -> InfraCredential:
    return InfraCredential(
        code=str(row["code"]),
        display_name=str(row.get("display_name") or row["code"]),
        group_code=str(row.get("group_code") or "object_storage"),
        config=_coerce_config(row.get("config")),
        enabled=bool(int(row.get("enabled") or 0)),
        updated_by=int(row["updated_by"]) if row.get("updated_by") is not None else None,
    )


# ---------------------------------------------------------------------------
# 对外 API：读
# ---------------------------------------------------------------------------

def list_configs() -> list[InfraCredential]:
    rows = query(
        "SELECT code, display_name, group_code, config, enabled, updated_by "
        "FROM infra_credentials ORDER BY group_code, code"
    )
    return [_row_to_cred(r) for r in rows]


def get_config(code: str) -> InfraCredential | None:
    row = query_one(
        "SELECT code, display_name, group_code, config, enabled, updated_by "
        "FROM infra_credentials WHERE code = %s",
        (code,),
    )
    return _row_to_cred(row) if row else None


def known_codes() -> list[str]:
    return list(_CREDENTIAL_SCHEMA.keys())


def schema_for(code: str) -> list[CredentialField]:
    return list(_CREDENTIAL_SCHEMA.get(code, []))


def display_meta(code: str) -> tuple[str, str]:
    return _DISPLAY_META.get(code, (code, "object_storage"))


# ---------------------------------------------------------------------------
# 对外 API：写
# ---------------------------------------------------------------------------

def save_config(
    code: str,
    fields: dict[str, Any],
    updated_by: int | None,
) -> None:
    """部分字段更新；保存后立即触发 :func:`sync_to_runtime`。

    fields 的 key 是 JSON 字段名（``access_key`` / ``secret_key`` / ...）。
    传空串表示显式清空（DB 行里写入空串），但 :func:`sync_to_runtime` 不会用
    空串覆盖 config 模块属性 —— 这样可以让 ``.env`` 兜底值在 DB 留空时继续
    生效，平滑过渡。
    """
    if code not in _CREDENTIAL_SCHEMA:
        raise ValueError(f"unknown infra credential code: {code}")

    existing = get_config(code)
    if existing is None:
        display_name, group_code = display_meta(code)
        existing = InfraCredential(
            code=code,
            display_name=display_name,
            group_code=group_code,
        )

    new_config = dict(existing.config)
    valid_keys = {f.json_key for f in _CREDENTIAL_SCHEMA[code]}
    for key, value in fields.items():
        if key not in valid_keys:
            continue
        if value is None:
            new_config[key] = ""
        else:
            new_config[key] = str(value).strip()

    enabled = bool(fields.get("enabled", existing.enabled))
    display_name, group_code = display_meta(code)

    execute(
        "INSERT INTO infra_credentials "
        "  (code, display_name, group_code, config, enabled, updated_by) "
        "VALUES (%s, %s, %s, %s, %s, %s) "
        "ON DUPLICATE KEY UPDATE "
        "  display_name = VALUES(display_name), "
        "  group_code   = VALUES(group_code), "
        "  config       = VALUES(config), "
        "  enabled      = VALUES(enabled), "
        "  updated_by   = VALUES(updated_by)",
        (
            code, display_name, group_code,
            json.dumps(new_config, ensure_ascii=False),
            int(enabled), updated_by,
        ),
    )
    sync_to_runtime()


# ---------------------------------------------------------------------------
# 启动 + 写时同步入口
# ---------------------------------------------------------------------------

def sync_to_runtime() -> None:
    """从 DB 读所有 enabled=1 行 → 覆盖 ``config.XXX`` + ``os.environ`` →
    失效已经持有旧凭据的 SDK client 缓存。

    适合在两个时机调用：
      * 进程启动（``main.py`` 触发 ``ensure_up_to_date()`` 之后）
      * admin 在 ``/settings?tab=infrastructure`` 保存表单之后
        （:func:`save_config` 已自动调用，外部一般不需要再调）

    DB 不可达 / 表未创建 / JSON 解析失败时记 warning 但不抛错，让进程仍
    能用 ``.env`` 兜底值启动。
    """
    try:
        rows = list_configs()
    except Exception as exc:
        log.warning("infra_credentials.sync_to_runtime: DB read failed: %s", exc)
        return

    by_code = {r.code: r for r in rows if r.enabled}

    try:
        import config as cfg
    except Exception as exc:  # 极端情况：测试或独立脚本
        log.warning("infra_credentials.sync_to_runtime: config import failed: %s", exc)
        return

    for code, fields in _CREDENTIAL_SCHEMA.items():
        cred = by_code.get(code)
        if cred is None:
            continue
        for spec in fields:
            value = cred.config.get(spec.json_key)
            if value is None:
                continue
            text = str(value).strip()
            if not text:
                # 空串：DB 显式留空 → 保留 .env 兜底值不动
                continue
            setattr(cfg, spec.config_attr, text)
            os.environ[spec.env_var] = text

    # tos_backup 的 ak/sk 留空时回落到 tos_main 的值，与 config.py 行 83-84 的
    # ``_env(name, default=TOS_ACCESS_KEY)`` 行为对齐。
    backup = by_code.get("tos_backup")
    main = by_code.get("tos_main")
    if backup and main:
        for json_key, attr in (
            ("access_key", "TOS_BACKUP_ACCESS_KEY"),
            ("secret_key", "TOS_BACKUP_SECRET_KEY"),
        ):
            current = (backup.config.get(json_key) or "").strip()
            fallback = (main.config.get(json_key) or "").strip()
            if not current and fallback:
                setattr(cfg, attr, fallback)
                os.environ[attr] = fallback

    _invalidate_sdk_caches()


def _invalidate_sdk_caches() -> None:
    """清掉所有持有旧 ak/sk 的 SDK client 缓存。

    必须在 ``config.XXX`` 已经被覆盖之后调用 —— 这些 client 是上次按旧 ak/sk
    构造的，下次 ``get_*_client()`` cache miss 时才会用新 config 重建。
    """
    try:
        from appcore import tos_clients

        tos_clients._client_cache.clear()
        tos_clients._private_probe_cache.update({"value": None, "expires_at": 0.0})
        tos_clients._private_probe_client = None
    except Exception:
        pass

    try:
        from appcore import tos_backup_storage

        tos_backup_storage._client_cache.clear()
    except Exception:
        pass

    try:
        from appcore import vod_client

        vod_client._configured = False
    except Exception:
        pass
