"""把残留在 .env 的供应商 key 一次性回填到 llm_provider_configs。

背景：
    2026-04-25 的迁移 (db/migrations/2026_04_25_llm_provider_configs.sql)
    把所有供应商凭据搬进 DB 表 llm_provider_configs，但只从旧 api_keys 表迁。
    历史上由 .env / config.py 直接读的几条（apimart_image / subtitle_removal /
    openapi_materials / 早期未入库的 openrouter / gemini 等）没有自动回填，
    上线后会让运行中的任务报 "缺少供应商配置 …"。

行为：
    - 对每个 provider_code，DB 已有非空 api_key → 跳过（不覆盖 admin 已填值）。
    - DB 为空时按候选 env 变量列表依次找第一个非空值，写进 DB。
    - 候选都没有 → 标记 "skipped_no_env"，运维需要去 /settings 手填。

幂等：可重复运行；只填空值，不动已配置的行。

用法：
    python scripts/backfill_provider_configs_from_env.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from appcore.llm_provider_configs import (  # noqa: E402
    get_provider_config,
    save_provider_config,
)


# provider_code → 按优先级排序的候选 env 变量名。
# 排在前面的命中后即停止；与 5b47dae 重构前 config.py 的 fallback 链对齐。
PROVIDER_ENV_CANDIDATES: dict[str, list[str]] = {
    "openrouter_text":       ["OPENROUTER_API_KEY"],
    "openrouter_image":      ["OPENROUTER_API_KEY"],
    "gemini_aistudio_text":  ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
    "gemini_aistudio_image": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
    "gemini_cloud_text":     ["GEMINI_CLOUD_API_KEY"],
    "gemini_cloud_image":    ["GEMINI_CLOUD_API_KEY"],
    "doubao_llm":            ["DOUBAO_LLM_API_KEY", "VOLC_API_KEY"],
    "doubao_asr":            ["VOLC_API_KEY"],
    "seedance_video":        ["SEEDANCE_API_KEY", "DOUBAO_LLM_API_KEY", "VOLC_API_KEY"],
    "elevenlabs_tts":        ["ELEVENLABS_API_KEY"],
    "apimart_image":         ["APIMART_IMAGE_API_KEY"],
    "subtitle_removal":      ["SUBTITLE_REMOVAL_PROVIDER_TOKEN"],
    "openapi_materials":     ["OPENAPI_MEDIA_API_KEY"],
    # doubao_seedream 在历史 .env 里没有变量来源，只能 admin 手填。
}


def _first_nonempty_env(env_names: list[str], env: dict[str, str]) -> tuple[str, str]:
    """返回 (env_var_used, value)；都为空时返回 ("", "")."""
    for name in env_names:
        value = (env.get(name) or "").strip()
        if value:
            return name, value
    return "", ""


def backfill(env: dict[str, str] | None = None) -> dict[str, list]:
    """执行回填，返回分类汇总。env 默认取 os.environ；测试可注入。"""
    src = env if env is not None else os.environ
    backfilled: list[tuple[str, str]] = []   # [(provider_code, env_var_used)]
    skipped_db_filled: list[str] = []
    skipped_no_env: list[str] = []

    for provider_code, env_names in PROVIDER_ENV_CANDIDATES.items():
        existing = get_provider_config(provider_code)
        if existing and (existing.api_key or "").strip():
            skipped_db_filled.append(provider_code)
            continue
        env_var, value = _first_nonempty_env(env_names, src)
        if not value:
            skipped_no_env.append(provider_code)
            continue
        save_provider_config(provider_code, {"api_key": value}, updated_by=None)
        backfilled.append((provider_code, env_var))

    return {
        "backfilled": backfilled,
        "skipped_db_filled": skipped_db_filled,
        "skipped_no_env": skipped_no_env,
    }


def main() -> int:
    print("Backfilling llm_provider_configs from .env ...")
    result = backfill()

    if result["backfilled"]:
        print(f"\n[OK] Backfilled {len(result['backfilled'])} provider(s):")
        for code, env_var in result["backfilled"]:
            print(f"  + {code:24s}  <-  ${env_var}")
    else:
        print("\n[OK] Nothing to backfill (all configured rows already have an api_key).")

    if result["skipped_db_filled"]:
        print(f"\n[SKIP] DB already filled ({len(result['skipped_db_filled'])}):")
        for code in result["skipped_db_filled"]:
            print(f"    {code}")

    if result["skipped_no_env"]:
        print(f"\n[TODO] Empty in DB and no env candidate ({len(result['skipped_no_env'])}):")
        for code in result["skipped_no_env"]:
            print(f"    {code}    -> please fill in /settings -> 服务商接入")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
