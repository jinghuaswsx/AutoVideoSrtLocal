"""api_keys 表只剩用户级配置（剪映目录、翻译偏好等非供应商凭据）。

2026-04-25 LLM 供应商配置数据库化后：
  - 所有供应商凭据迁到 llm_provider_configs；本模块不再读 .env、不再 fallback。
  - 历史 service 名（"openrouter"、"doubao_llm"、"doubao_asr"、"gemini" 等）
    通过 _LEGACY_SERVICE_MAP 路由到新 provider_code，供老调用点平滑过渡。
  - jianying / translate_pref 这类用户级配置仍然存在 api_keys 表里，admin
    设置页负责 set_key 写入；非用户级 service 写 api_keys 现在只允许 admin
    保存（与之前一致）。
"""
from __future__ import annotations

import json

from appcore.db import execute, query, query_one

DEFAULT_JIANYING_PROJECT_ROOT = r"C:\Users\admin\AppData\Local\JianyingPro\User Data\Projects\com.lveditor.draft"
ADMIN_CONFIG_USERNAME = "admin"
USER_SCOPED_SERVICES = {"jianying"}

# 非用户级、但仍由 api_keys 表承载的"管理员级偏好"配置。这些不是模型供应商
# 凭据，所以保留在 api_keys；通过 admin /settings 页保存。
_NON_PROVIDER_ADMIN_SERVICES = {"jianying", "translate_pref"}

# Legacy service 名 → llm_provider_configs.provider_code（默认走 *_text）
# 旧调用 `resolve_key(user_id, "openrouter", "OPENROUTER_API_KEY")` 经此映射后
# 直接命中 DB 行；不再有 env fallback。
_LEGACY_SERVICE_MAP: dict[str, str] = {
    "openrouter": "openrouter_text",
    "doubao_llm": "doubao_llm",
    "doubao_asr": "doubao_asr",
    "volc": "doubao_asr",
    "gemini": "gemini_aistudio_text",
    "gemini_video_analysis": "gemini_aistudio_text",
    "gemini_cloud": "gemini_cloud_text",
    "elevenlabs": "elevenlabs_tts",
    "seedance": "seedance_video",
    "apimart": "apimart_image",
    "doubao_seedream": "doubao_seedream",
    "subtitle_removal": "subtitle_removal",
    "openapi_materials": "openapi_materials",
}


def _admin_config_user_id() -> int | None:
    row = query_one(
        "SELECT id FROM users WHERE username = %s AND is_active = 1",
        (ADMIN_CONFIG_USERNAME,),
    )
    if not row:
        return None
    try:
        return int(row["id"])
    except (KeyError, TypeError, ValueError):
        return None


def _is_admin_config_user(user_id: int | None) -> bool:
    if user_id is None:
        return False
    row = query_one(
        "SELECT username FROM users WHERE id = %s AND is_active = 1",
        (user_id,),
    )
    return bool(row and row.get("username") == ADMIN_CONFIG_USERNAME)


def can_manage_api_config_user(user) -> bool:
    return bool(
        getattr(user, "is_authenticated", False)
        and getattr(user, "username", None) == ADMIN_CONFIG_USERNAME
    )


def _config_read_user_id() -> int | None:
    return _admin_config_user_id()


# ---------------------------------------------------------------------------
# 用户/管理员级 api_keys 表写入（仅限 USER_SCOPED_SERVICES + admin 偏好）
# ---------------------------------------------------------------------------

def set_key(user_id: int, service: str, key_value: str, extra: dict | None = None) -> None:
    """向 api_keys 表写入。供应商凭据走 llm_provider_configs.save_provider_config，
    本函数只负责 jianying / translate_pref 这类"非供应商"行的写入。
    """
    if service in USER_SCOPED_SERVICES:
        pass  # user-scoped 任何用户都可保存
    elif service in _NON_PROVIDER_ADMIN_SERVICES:
        if not _is_admin_config_user(user_id):
            raise PermissionError("API 配置只能由 admin 用户修改")
    else:
        if service in _LEGACY_SERVICE_MAP:
            raise PermissionError(
                f"service={service} 已迁移到 llm_provider_configs，"
                "请改用 appcore.llm_provider_configs.save_provider_config"
            )
        if not _is_admin_config_user(user_id):
            raise PermissionError("API 配置只能由 admin 用户修改")
    extra_json = json.dumps(extra) if extra else None
    execute(
        """INSERT INTO api_keys (user_id, service, key_value, extra_config)
           VALUES (%s, %s, %s, %s)
           ON DUPLICATE KEY UPDATE key_value = VALUES(key_value), extra_config = VALUES(extra_config)""",
        (user_id, service, key_value, extra_json),
    )


def get_key(user_id: int, service: str) -> str | None:
    """直接查 api_keys 表（不 fallback 到 llm_provider_configs）。

    供应商类 service 应优先走 resolve_key（自动路由到 llm_provider_configs），
    本函数只在确实需要"老 api_keys 表里有没有这条"时使用，例如 jianying
    用户级配置 / 历史数据迁移脚本。
    """
    config_user_id = user_id if service in USER_SCOPED_SERVICES else _config_read_user_id()
    if config_user_id is None:
        return None
    row = query_one(
        "SELECT key_value FROM api_keys WHERE user_id = %s AND service = %s",
        (config_user_id, service),
    )
    return row["key_value"] if row else None


# ---------------------------------------------------------------------------
# 供应商凭据：经 _LEGACY_SERVICE_MAP 路由到 llm_provider_configs
# env_var 入参保留是为了不破坏老签名，但**不会再被读取**。
# ---------------------------------------------------------------------------

def _provider_code_for_service(service: str) -> str | None:
    return _LEGACY_SERVICE_MAP.get(service)


def resolve_key(user_id: int | None, service: str, env_var: str | None = None) -> str | None:
    """返回供应商 api_key（或用户级 api_keys 表的 key_value）。

    路由顺序：
      1) USER_SCOPED_SERVICES（jianying 等）→ 读 api_keys 表对应 user_id
      2) 已映射到 llm_provider_configs 的供应商 service → 读 DB 行
      3) admin 级 _NON_PROVIDER_ADMIN_SERVICES（translate_pref 等）→ 读 admin 行
      4) 其他未知 service → None（不再回落 env）

    env_var 参数保留只为兼容老调用签名，不再被读取。
    """
    if service in USER_SCOPED_SERVICES:
        return get_key(user_id or 0, service) if user_id is not None else None

    provider_code = _provider_code_for_service(service)
    if provider_code is not None:
        from appcore.llm_provider_configs import get_provider_config
        cfg = get_provider_config(provider_code)
        return cfg.api_key if cfg else None

    if service in _NON_PROVIDER_ADMIN_SERVICES:
        return get_key(user_id or 0, service) if user_id is not None else None

    return None


def resolve_extra(user_id: int | None, service: str) -> dict:
    """返回 service 对应的 extra 字典。

    供应商 service：合并 llm_provider_configs.extra_config 与 base_url/model_id 列；
    USER_SCOPED_SERVICES：读 api_keys.extra_config JSON。
    """
    if service in USER_SCOPED_SERVICES:
        return _read_user_scoped_extra(user_id, service)

    provider_code = _provider_code_for_service(service)
    if provider_code is not None:
        from appcore.llm_provider_configs import get_provider_config
        cfg = get_provider_config(provider_code)
        if cfg is None:
            return {}
        extra = dict(cfg.extra_config) if cfg.extra_config else {}
        if cfg.base_url and "base_url" not in extra:
            extra["base_url"] = cfg.base_url
        if cfg.model_id and "model_id" not in extra:
            extra["model_id"] = cfg.model_id
        return extra

    if service in _NON_PROVIDER_ADMIN_SERVICES:
        return _read_user_scoped_extra(user_id, service)

    return {}


def _read_user_scoped_extra(user_id: int | None, service: str) -> dict:
    config_user_id = user_id if service in USER_SCOPED_SERVICES else _config_read_user_id()
    if config_user_id is None:
        return {}
    row = query_one(
        "SELECT extra_config FROM api_keys WHERE user_id = %s AND service = %s",
        (config_user_id, service),
    )
    if not row or not row.get("extra_config"):
        return {}
    extra = row["extra_config"]
    if isinstance(extra, str):
        try:
            return json.loads(extra)
        except Exception:
            return {}
    return extra or {}


# ---------------------------------------------------------------------------
# get_all：admin 设置页用。供应商行直接来自 llm_provider_configs，
# 老 api_keys 表只贡献 USER_SCOPED + admin 级偏好（jianying / translate_pref）。
# 返回结构与老接口兼容：{service: {"key_value": ..., "extra": {...}}}
# ---------------------------------------------------------------------------

def get_all(user_id: int) -> dict[str, dict]:
    config_user_id = _config_read_user_id()
    result: dict[str, dict] = {}
    if config_user_id is not None:
        rows = query(
            "SELECT service, key_value, extra_config FROM api_keys WHERE user_id = %s",
            (config_user_id,),
        )
        for row in rows:
            extra = row["extra_config"]
            if isinstance(extra, str):
                try:
                    extra = json.loads(extra)
                except Exception:
                    extra = {}
            result[row["service"]] = {
                "key_value": row["key_value"],
                "extra": extra or {},
            }

    # 把供应商行从 llm_provider_configs 反向暴露成老 service 名，方便老模板访问
    try:
        from appcore.llm_provider_configs import list_provider_configs
    except Exception:
        return result
    legacy_for_provider: dict[str, str] = {}
    for legacy, provider in _LEGACY_SERVICE_MAP.items():
        legacy_for_provider.setdefault(provider, legacy)
    for cfg in list_provider_configs():
        legacy = legacy_for_provider.get(cfg.provider_code)
        if not legacy:
            continue
        # 若 admin 同时在 api_keys 里手动写过同名 service，已存在的字典优先保留
        result.setdefault(legacy, {
            "key_value": cfg.api_key or "",
            "extra": _legacy_extra_from_config(cfg),
        })
    return result


def _legacy_extra_from_config(cfg) -> dict:
    extra = dict(cfg.extra_config) if cfg.extra_config else {}
    if cfg.base_url:
        extra.setdefault("base_url", cfg.base_url)
    if cfg.model_id:
        extra.setdefault("model_id", cfg.model_id)
    return extra


# ---------------------------------------------------------------------------
# Jianying 配置（用户级，不变）
# ---------------------------------------------------------------------------

def resolve_jianying_project_root(user_id: int | None) -> str:
    extra = resolve_extra(user_id, "jianying")
    project_root = (extra.get("project_root") or "").strip() if isinstance(extra, dict) else ""
    return project_root or DEFAULT_JIANYING_PROJECT_ROOT
