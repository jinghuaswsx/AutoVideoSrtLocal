"""llm_use_case_bindings 表的 DAO + resolver。

resolver 行为（对齐 llm_prompt_configs.resolve_prompt_config）：
  - DB 命中 enabled=1 → 返 DB
  - DB 命中 enabled=0 → 走默认，但不 seed（保留管理员 disable 意图）
  - DB 无记录 → 走默认，并 seed 写回 DB，下次命中 DB 路径

bindings 表是全局级（无 user_id），由管理员在 /settings 第二 Tab 编辑。
"""
from __future__ import annotations

import json
from typing import Any

from appcore.db import execute, query, query_one
from appcore.llm_use_cases import USE_CASES, get_use_case


def _parse_extra(raw: Any) -> dict:
    if raw is None:
        return {}
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return {}
    return dict(raw) if isinstance(raw, dict) else {}


def resolve(use_case_code: str) -> dict:
    """返回 {provider, model, extra, source}。

    source ∈ {"db", "default"}。无记录时会 seed 默认值。
    """
    default = get_use_case(use_case_code)  # 校验 + 拿默认
    row = query_one(
        "SELECT provider_code, model_id, extra_config, enabled "
        "FROM llm_use_case_bindings WHERE use_case_code = %s",
        (use_case_code,),
    )
    if row and int(row.get("enabled") or 0) == 1:
        return {
            "provider": row["provider_code"],
            "model": row["model_id"],
            "extra": _parse_extra(row.get("extra_config")),
            "source": "db",
        }

    # 无记录时 seed；有但 disabled 不 seed
    if row is None:
        execute(
            "INSERT INTO llm_use_case_bindings "
            "(use_case_code, provider_code, model_id, extra_config, enabled, updated_by) "
            "VALUES (%s, %s, %s, %s, %s, %s) "
            "ON DUPLICATE KEY UPDATE "
            "  provider_code = VALUES(provider_code), "
            "  model_id = VALUES(model_id)",
            (use_case_code, default["default_provider"], default["default_model"],
             None, 1, None),
        )

    return {
        "provider": default["default_provider"],
        "model": default["default_model"],
        "extra": {},
        "source": "default",
    }


def upsert(use_case_code: str, *, provider: str, model: str,
           extra: dict | None = None, enabled: bool = True,
           updated_by: int | None) -> None:
    get_use_case(use_case_code)  # 校验存在
    execute(
        "INSERT INTO llm_use_case_bindings "
        "(use_case_code, provider_code, model_id, extra_config, enabled, updated_by) "
        "VALUES (%s, %s, %s, %s, %s, %s) "
        "ON DUPLICATE KEY UPDATE "
        "  provider_code = VALUES(provider_code), "
        "  model_id = VALUES(model_id), "
        "  extra_config = VALUES(extra_config), "
        "  enabled = VALUES(enabled), "
        "  updated_by = VALUES(updated_by)",
        (use_case_code, provider, model,
         json.dumps(extra) if extra else None,
         1 if enabled else 0, updated_by),
    )


def delete(use_case_code: str) -> None:
    """删除覆盖，下次 resolve 回到默认并重新 seed。"""
    execute(
        "DELETE FROM llm_use_case_bindings WHERE use_case_code = %s",
        (use_case_code,),
    )


def list_all() -> list[dict]:
    """返回所有 use_case 的合并列表（USE_CASES 默认 ∪ DB 覆盖）。

    DB 记录 enabled=0 视为无覆盖（走默认，is_custom=False）。
    """
    rows = query(
        "SELECT use_case_code, provider_code, model_id, extra_config, "
        "       enabled, updated_by, updated_at "
        "FROM llm_use_case_bindings"
    )
    by_code = {r["use_case_code"]: r for r in rows}
    out: list[dict] = []
    for code, uc in USE_CASES.items():
        row = by_code.get(code)
        if row and int(row.get("enabled") or 0) == 1:
            out.append({
                "code": code,
                "module": uc["module"],
                "label": uc["label"],
                "description": uc["description"],
                "provider": row["provider_code"],
                "model": row["model_id"],
                "extra": _parse_extra(row.get("extra_config")),
                "enabled": True,
                "is_custom": True,
                "updated_at": row.get("updated_at"),
                "updated_by": row.get("updated_by"),
            })
        else:
            out.append({
                "code": code,
                "module": uc["module"],
                "label": uc["label"],
                "description": uc["description"],
                "provider": uc["default_provider"],
                "model": uc["default_model"],
                "extra": {},
                "enabled": True,
                "is_custom": False,
                "updated_at": None,
                "updated_by": None,
            })
    return out
