"""LLM prompt 配置 DAO + resolver。

运行时通过 resolve_prompt_config() 取配置；DB 为空时 fallback 到
pipeline/languages/prompt_defaults.py 里的 DEFAULTS，并 seed 写回 DB。

管理员后台通过 upsert() / list_all() 编辑。
"""
from __future__ import annotations

from typing import Optional

from appcore.db import query, query_one, execute


VALID_SLOTS = {"base_translation", "base_tts_script", "base_rewrite", "ecommerce_plugin"}


def _get_default(slot: str, lang: Optional[str]) -> Optional[dict]:
    """从代码里的出厂默认取一条；空则 None。"""
    from pipeline.languages.prompt_defaults import DEFAULTS
    return DEFAULTS.get((slot, lang))


def resolve_prompt_config(slot: str, lang: Optional[str]) -> dict:
    """返回 {provider, model, content}。DB 命中即返回；否则从 DEFAULTS 取并 seed 写回。

    `lang` 对 slot=='ecommerce_plugin' 传 None（表示共享），SQL 用 IS NULL 精确匹配。
    """
    if slot not in VALID_SLOTS:
        raise ValueError(f"invalid slot: {slot}")

    if lang is None:
        row = query_one(
            "SELECT model_provider, model_name, content FROM llm_prompt_configs "
            "WHERE slot = %s AND lang IS NULL AND enabled = 1 LIMIT 1",
            (slot,),
        )
    else:
        row = query_one(
            "SELECT model_provider, model_name, content FROM llm_prompt_configs "
            "WHERE slot = %s AND lang = %s AND enabled = 1 LIMIT 1",
            (slot, lang),
        )

    if row:
        return {
            "provider": row["model_provider"],
            "model": row["model_name"],
            "content": row["content"],
        }

    default = _get_default(slot, lang)
    if not default:
        raise LookupError(f"no prompt config and no default for slot={slot} lang={lang}")
    upsert(slot, lang,
           provider=default["provider"], model=default["model"],
           content=default["content"], updated_by=None)
    return default


def upsert(slot: str, lang: Optional[str], *,
           provider: str, model: str, content: str,
           updated_by: Optional[int]) -> None:
    if slot not in VALID_SLOTS:
        raise ValueError(f"invalid slot: {slot}")
    execute(
        "INSERT INTO llm_prompt_configs "
        "(slot, lang, model_provider, model_name, content, updated_by) "
        "VALUES (%s, %s, %s, %s, %s, %s) "
        "ON DUPLICATE KEY UPDATE "
        "  model_provider = VALUES(model_provider), "
        "  model_name = VALUES(model_name), "
        "  content = VALUES(content), "
        "  updated_by = VALUES(updated_by)",
        (slot, lang, provider, model, content, updated_by),
    )


def list_all() -> list[dict]:
    return query(
        "SELECT id, slot, lang, model_provider, model_name, content, "
        "       enabled, updated_at, updated_by "
        "FROM llm_prompt_configs ORDER BY slot, lang"
    )


def get_one(slot: str, lang: Optional[str]) -> Optional[dict]:
    if lang is None:
        return query_one(
            "SELECT * FROM llm_prompt_configs WHERE slot = %s AND lang IS NULL",
            (slot,),
        )
    return query_one(
        "SELECT * FROM llm_prompt_configs WHERE slot = %s AND lang = %s",
        (slot, lang),
    )


def delete(slot: str, lang: Optional[str]) -> None:
    """删掉一条 override，下次 resolve 时会重新 seed 默认值。"""
    if lang is None:
        execute(
            "DELETE FROM llm_prompt_configs WHERE slot = %s AND lang IS NULL",
            (slot,),
        )
    else:
        execute(
            "DELETE FROM llm_prompt_configs WHERE slot = %s AND lang = %s",
            (slot, lang),
        )
