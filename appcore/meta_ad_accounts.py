"""Meta 广告账户配置（system_settings.meta_ad_accounts）。

详细设计见 docs/superpowers/specs/2026-05-07-meta-ads-multi-account-design.md。
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

from appcore import settings as system_settings

log = logging.getLogger(__name__)

SETTING_KEY = "meta_ad_accounts"


@dataclass(frozen=True)
class MetaAdAccount:
    code: str
    account_id: str
    business_id: str
    csv_prefix: str
    enabled: bool
    label: str = ""
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "label": self.label or self.code,
            "account_id": self.account_id,
            "business_id": self.business_id,
            "csv_prefix": self.csv_prefix,
            "enabled": self.enabled,
            "note": self.note,
        }


def _coerce_account(raw: dict) -> MetaAdAccount | None:
    if not isinstance(raw, dict):
        return None
    code = str(raw.get("code") or "").strip()
    account_id = str(raw.get("account_id") or "").strip().removeprefix("act_")
    business_id = str(raw.get("business_id") or "").strip()
    csv_prefix = str(raw.get("csv_prefix") or code).strip()
    if not code or not account_id or not business_id or not csv_prefix:
        log.warning("meta_ad_accounts: skipping invalid entry %r", raw)
        return None
    return MetaAdAccount(
        code=code,
        account_id=account_id,
        business_id=business_id,
        csv_prefix=csv_prefix,
        enabled=bool(raw.get("enabled", True)),
        label=str(raw.get("label") or "").strip(),
        note=str(raw.get("note") or "").strip(),
    )


def _env_default_account() -> MetaAdAccount | None:
    """没有 setting 时回退到旧版单账户行为（newjoyloo），与 tools.roi_hourly_sync 模块默认对齐。"""
    account_id = (
        os.environ.get("META_AD_EXPORT_ACCOUNT_ID")
        or "2110407576446225"
    ).strip().removeprefix("act_")
    business_id = (
        os.environ.get("META_AD_EXPORT_BUSINESS_ID")
        or "476723373113063"
    ).strip()
    if not account_id or not business_id:
        return None
    return MetaAdAccount(
        code="newjoyloo",
        account_id=account_id,
        business_id=business_id,
        csv_prefix="newjoyloo",
        enabled=True,
        label="Newjoyloo",
    )


def get_all_accounts() -> list[MetaAdAccount]:
    raw = system_settings.get_setting(SETTING_KEY)
    if not raw:
        env_account = _env_default_account()
        return [env_account] if env_account else []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError) as exc:
        log.warning("meta_ad_accounts: setting JSON invalid (%s); falling back to env", exc)
        env_account = _env_default_account()
        return [env_account] if env_account else []
    if not isinstance(data, list):
        log.warning("meta_ad_accounts: setting must be a JSON list, got %r", type(data).__name__)
        return []
    accounts: list[MetaAdAccount] = []
    seen_codes: set[str] = set()
    for item in data:
        account = _coerce_account(item)
        if account is None:
            continue
        if account.code in seen_codes:
            log.warning("meta_ad_accounts: duplicate code %r dropped", account.code)
            continue
        seen_codes.add(account.code)
        accounts.append(account)
    return accounts


def get_enabled_accounts() -> list[MetaAdAccount]:
    return [a for a in get_all_accounts() if a.enabled]


def set_accounts(accounts: list[dict]) -> None:
    """覆盖式写入。值会先经过 _coerce_account 验证。"""
    coerced = []
    for item in accounts:
        account = _coerce_account(item)
        if account is None:
            raise ValueError(f"invalid meta ad account entry: {item!r}")
        coerced.append(account.to_dict())
    system_settings.set_setting(SETTING_KEY, json.dumps(coerced, ensure_ascii=False))
