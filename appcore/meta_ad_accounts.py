"""Meta 广告账户配置（system_settings.meta_ad_accounts）。

详细设计见 docs/superpowers/specs/2026-05-07-meta-ads-multi-account-design.md。
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from appcore import settings as system_settings

log = logging.getLogger(__name__)

SETTING_KEY = "meta_ad_accounts"
AVAILABLE_STORE_CODES = ("newjoy", "omurio")
DEFAULT_NEWJOYLOO_ACCOUNT_ID = "1861285821213497"
LEGACY_NEWJOYLOO_ACCOUNT_ID = "2110407576446225"
OMURIO_ACCOUNT_ID = "1253003326160754"
DEFAULT_NEWJOYLOO_BUSINESS_ID = "476723373113063"

# 旧户 2110407576446225 的 column preset（含购物转化价值 / ROAS - 购物 等完整列）。
# 该 preset 仅在旧户账号下可见，新账户没有这条 preset 时 Meta 会回退到一组裸列。
# 详细背景见 docs/superpowers/specs/2026-05-09-ads-purchase-value-order-fallback-design.md
LEGACY_COLUMN_PRESET = "1658418688523178"
NEWJOYLOO_COLUMN_PRESET = "1680560372975676"
OMURIO_COLUMN_PRESET = "1645951873103193"

# 这些是当前 Meta UI 中可见的列模板展示名与真实 URL 参数。同步链路只使用
# column_preset 参数；UI 展示名只用于广告账户管理页给运营识别。
# Docs-anchor:
# docs/superpowers/specs/2026-05-09-ads-purchase-value-order-fallback-design.md
COLUMN_PRESET_CHOICES = (
    {
        "label": "111",
        "value": NEWJOYLOO_COLUMN_PRESET,
        "recommended_account_codes": ["newjoyloo"],
        "note": "newjoyloo / newjoyloo_bak",
    },
    {
        "label": "1111",
        "value": OMURIO_COLUMN_PRESET,
        "recommended_account_codes": ["Omurio"],
        "note": "Omurio",
    },
    {
        "label": "1111",
        "value": LEGACY_COLUMN_PRESET,
        "recommended_account_codes": ["newjoyloo_old"],
        "note": "newjoyloo_old",
    },
)

# sync_mode 枚举：csv_export 走 Ads Manager CSV 导出（现有），
# xhr_api 走页面内 Marketing API 抓取（in-page fetch channel，2026-05-09 新增）。
# 详见 docs/superpowers/specs/2026-05-09-meta-ads-xhr-token-channel.md
AVAILABLE_SYNC_MODES = ("csv_export", "xhr_api")
DEFAULT_SYNC_MODE = "csv_export"

# 账户时区：xhr_api 通道按账户时区构造 Meta /insights time_range（calendar date）。
# 默认值贴近 Meta US 账户的现网配置（newjoyloo / newjoyloo_bak / Omurio 都按 LA 时区）。
# 详见 docs/superpowers/specs/2026-05-09-meta-ads-account-timezone-and-async-fix.md
DEFAULT_ACCOUNT_TIMEZONE = "America/Los_Angeles"

# BJ 业务日 cutover 小时；与 tools/roi_hourly_sync.META_CUTOVER_HOUR_BJ 同源
# （此处复制是为了避免 appcore 反向依赖 tools/）。
META_CUTOVER_HOUR_BJ = 16
_BJ_TIMEZONE = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class MetaAdAccount:
    code: str
    account_id: str
    business_id: str
    csv_prefix: str
    store_codes: tuple[str, ...]
    enabled: bool
    label: str = ""
    note: str = ""
    column_preset: str = LEGACY_COLUMN_PRESET
    sync_mode: str = DEFAULT_SYNC_MODE
    timezone: str = DEFAULT_ACCOUNT_TIMEZONE

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "label": self.label or self.code,
            "account_id": self.account_id,
            "business_id": self.business_id,
            "csv_prefix": self.csv_prefix,
            "store_codes": list(self.store_codes),
            "enabled": self.enabled,
            "note": self.note,
            "column_preset": self.column_preset,
            "column_preset_label": column_preset_label(self.column_preset),
            "sync_mode": self.sync_mode,
            "timezone": self.timezone,
        }


def column_preset_choices() -> list[dict[str, object]]:
    return [dict(choice) for choice in COLUMN_PRESET_CHOICES]


def column_preset_label(column_preset: str | None) -> str:
    value = str(column_preset or "").strip()
    if not value:
        return ""
    for choice in COLUMN_PRESET_CHOICES:
        if choice["value"] == value:
            return str(choice["label"])
    return "自定义"


def default_column_preset_for_account(code: str, account_id: str) -> str:
    normalized_code = str(code or "").strip().lower()
    normalized_account_id = str(account_id or "").strip().removeprefix("act_")
    if normalized_account_id == DEFAULT_NEWJOYLOO_ACCOUNT_ID or normalized_code in {
        "newjoyloo",
        "newjoyloo_bak",
    }:
        return NEWJOYLOO_COLUMN_PRESET
    if normalized_account_id == OMURIO_ACCOUNT_ID or normalized_code == "omurio":
        return OMURIO_COLUMN_PRESET
    if normalized_account_id == LEGACY_NEWJOYLOO_ACCOUNT_ID or normalized_code == "newjoyloo_old":
        return LEGACY_COLUMN_PRESET
    return LEGACY_COLUMN_PRESET


def _coerce_column_preset(raw: object, *, code: str, account_id: str) -> str:
    value = str(raw or "").strip()
    default = default_column_preset_for_account(code, account_id)
    if not value:
        return default

    normalized_value = value.strip()
    normalized_upper = normalized_value.upper()
    normalized_account_id = str(account_id or "").strip().removeprefix("act_")

    # These are UI labels or built-in naked-performance presets, not usable
    # per-account URL preset IDs for CSV sync.
    if normalized_upper == "PERFORMANCE" or normalized_value in {"111", "1111"}:
        return default

    # The legacy preset only works in the old account. If it was persisted into
    # a new account config, treat it as a stale fallback and use that account's
    # current default instead.
    if normalized_value == LEGACY_COLUMN_PRESET and normalized_account_id != LEGACY_NEWJOYLOO_ACCOUNT_ID:
        return default
    return normalized_value


def _normalize_store_codes(raw: object, *, code: str = "") -> tuple[str, ...]:
    values: list[str]
    if isinstance(raw, str):
        values = [part.strip() for part in raw.split(",")]
    elif isinstance(raw, (list, tuple, set)):
        values = [str(item or "").strip() for item in raw]
    else:
        values = []

    normalized: list[str] = []
    for value in values:
        item = value.strip().lower()
        if not item or item in normalized:
            continue
        normalized.append(item)

    if normalized:
        return tuple(normalized)

    lowered_code = code.strip().lower()
    if "newjoy" in lowered_code:
        return ("newjoy",)
    if "omurio" in lowered_code:
        return ("omurio",)
    return ()


def _coerce_sync_mode(raw: object) -> str:
    value = str(raw or "").strip().lower()
    if not value:
        return DEFAULT_SYNC_MODE
    if value not in AVAILABLE_SYNC_MODES:
        raise ValueError(
            f"sync_mode must be one of {AVAILABLE_SYNC_MODES}, got {value!r}"
        )
    return value


def _coerce_timezone(raw: object) -> str:
    """Validate the timezone string against the IANA database.

    Raises ``ValueError`` for explicit non-empty inputs that ``zoneinfo``
    cannot resolve so that ``set_accounts`` can refuse them at write time.
    Accepts blank / missing values and substitutes the default — that's
    what every legacy row will look like before any UI roll-out.
    """
    value = str(raw or "").strip()
    if not value:
        return DEFAULT_ACCOUNT_TIMEZONE
    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(
            f"timezone must be a valid IANA name (e.g. America/Los_Angeles), got {value!r}"
        ) from exc
    return value


def _coerce_account(raw: dict) -> MetaAdAccount | None:
    if not isinstance(raw, dict):
        return None
    code = str(raw.get("code") or "").strip()
    account_id = str(raw.get("account_id") or "").strip().removeprefix("act_")
    business_id = str(raw.get("business_id") or "").strip()
    csv_prefix = str(raw.get("csv_prefix") or code).strip()
    store_codes = _normalize_store_codes(raw.get("store_codes"), code=code)
    try:
        sync_mode = _coerce_sync_mode(raw.get("sync_mode"))
    except ValueError as exc:
        log.warning("meta_ad_accounts: invalid sync_mode for %r: %s", code, exc)
        return None
    try:
        timezone_name = _coerce_timezone(raw.get("timezone"))
    except ValueError as exc:
        log.warning(
            "meta_ad_accounts: invalid timezone for %r: %s; falling back to %s",
            code,
            exc,
            DEFAULT_ACCOUNT_TIMEZONE,
        )
        timezone_name = DEFAULT_ACCOUNT_TIMEZONE
    if not code or not account_id or not business_id or not csv_prefix or not store_codes:
        log.warning("meta_ad_accounts: skipping invalid entry %r", raw)
        return None
    column_preset = _coerce_column_preset(
        raw.get("column_preset"),
        code=code,
        account_id=account_id,
    )
    return MetaAdAccount(
        code=code,
        account_id=account_id,
        business_id=business_id,
        csv_prefix=csv_prefix,
        store_codes=store_codes,
        enabled=bool(raw.get("enabled", True)),
        label=str(raw.get("label") or "").strip(),
        note=str(raw.get("note") or "").strip(),
        column_preset=column_preset,
        sync_mode=sync_mode,
        timezone=timezone_name,
    )


def _env_default_account() -> MetaAdAccount | None:
    """没有 setting 时回退到当前 newjoyloo 单账户行为，与 tools.roi_hourly_sync 模块默认对齐。"""
    account_id = (
        os.environ.get("META_AD_EXPORT_ACCOUNT_ID")
        or DEFAULT_NEWJOYLOO_ACCOUNT_ID
    ).strip().removeprefix("act_")
    business_id = (
        os.environ.get("META_AD_EXPORT_BUSINESS_ID")
        or DEFAULT_NEWJOYLOO_BUSINESS_ID
    ).strip()
    if not account_id or not business_id:
        return None
    return MetaAdAccount(
        code="newjoyloo",
        account_id=account_id,
        business_id=business_id,
        csv_prefix="newjoyloo",
        store_codes=("newjoy",),
        enabled=True,
        label="Newjoyloo",
        column_preset=default_column_preset_for_account("newjoyloo", account_id),
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


def site_account_map(*, enabled_only: bool = True) -> dict[str, tuple[str, ...]]:
    accounts = get_enabled_accounts() if enabled_only else get_all_accounts()
    grouped: dict[str, list[str]] = {}
    for account in accounts:
        for store_code in account.store_codes:
            grouped.setdefault(store_code, [])
            if account.account_id not in grouped[store_code]:
                grouped[store_code].append(account.account_id)
    return {store_code: tuple(account_ids) for store_code, account_ids in grouped.items()}


def set_accounts(accounts: list[dict]) -> None:
    """覆盖式写入。值会先经过 _coerce_account 验证。"""
    coerced = []
    seen_codes: set[str] = set()
    for item in accounts:
        if isinstance(item, dict) and "sync_mode" in item:
            try:
                _coerce_sync_mode(item.get("sync_mode"))
            except ValueError as exc:
                raise ValueError(
                    f"invalid sync_mode for account {item.get('code')!r}: {exc}"
                ) from exc
        if isinstance(item, dict) and "timezone" in item and str(item.get("timezone") or "").strip():
            try:
                _coerce_timezone(item.get("timezone"))
            except ValueError as exc:
                raise ValueError(
                    f"invalid timezone for account {item.get('code')!r}: {exc}"
                ) from exc
        account = _coerce_account(item)
        if account is None:
            raise ValueError(f"invalid meta ad account entry: {item!r}")
        if account.code in seen_codes:
            raise ValueError(f"duplicate meta ad account code: {account.code}")
        seen_codes.add(account.code)
        coerced.append(account.to_dict())
    system_settings.set_setting(SETTING_KEY, json.dumps(coerced, ensure_ascii=False))


def account_xhr_time_range(
    account: "MetaAdAccount", business_date: date
) -> dict[str, str]:
    """Map a BJ business day to a Meta /insights ``time_range`` in account TZ.

    The BJ business window is ``[BJ 16:00 D, BJ 16:00 D+1)``. Meta's
    ``/insights`` endpoint interprets ``time_range.since`` / ``time_range.until``
    as calendar dates **in the account's configured timezone**, so passing
    the BJ business date as-is silently misaligns by 7-8 hours when the
    account is on Pacific time. This helper returns the calendar-date
    range in the account's timezone that fully covers the BJ window.

    Output rules — ``until`` is *inclusive* (matches Meta's API spec):

    - ``since`` = the account-TZ calendar date of the window start.
    - ``until`` = the account-TZ calendar date of the window end, unless
      the end falls exactly on midnight (i.e. the previous day's
      23:59:59 was the last second covered), in which case we step back
      one day to avoid covering an extra full day with zero overlap.

    Examples (assuming account timezone arg & BJ business date 2026-05-09):

    - ``America/Los_Angeles`` PDT (UTC-7): start = PDT 01:00 5/9, end =
      PDT 01:00 5/10 → ``{since=5/9, until=5/10}``.
    - ``America/Los_Angeles`` PST (UTC-8): start = PST 00:00 5/9, end =
      PST 00:00 5/10 (midnight boundary) → ``{since=5/9, until=5/9}``.
    - ``Asia/Shanghai`` (UTC+8): start = BJ 16:00 5/9, end = BJ 16:00
      5/10 → ``{since=5/9, until=5/10}``.

    Realtime + daily_final XHR paths share this helper to guarantee the
    same conversion logic.
    """
    tz_name = getattr(account, "timezone", None) or DEFAULT_ACCOUNT_TIMEZONE
    try:
        acct_tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        log.warning(
            "account_xhr_time_range: invalid timezone %r for account %r; "
            "falling back to %s",
            tz_name,
            getattr(account, "code", "?"),
            DEFAULT_ACCOUNT_TIMEZONE,
        )
        acct_tz = ZoneInfo(DEFAULT_ACCOUNT_TIMEZONE)

    window_start_bj = datetime(
        business_date.year,
        business_date.month,
        business_date.day,
        META_CUTOVER_HOUR_BJ,
        0,
        0,
        tzinfo=_BJ_TIMEZONE,
    )
    window_end_bj = window_start_bj + timedelta(days=1)

    start_local = window_start_bj.astimezone(acct_tz)
    end_local = window_end_bj.astimezone(acct_tz)

    since_date = start_local.date()
    if end_local.hour == 0 and end_local.minute == 0 and end_local.second == 0:
        until_date = end_local.date() - timedelta(days=1)
    else:
        until_date = end_local.date()

    return {"since": since_date.isoformat(), "until": until_date.isoformat()}


def account_xhr_report_date(account: "MetaAdAccount", business_date: date) -> date:
    """Return the account-local report date that belongs to ``business_date``.

    ``account_xhr_time_range`` can intentionally straddle multiple account
    calendar days. Meta returns one row per account calendar day when
    ``time_increment=1`` is used, so importers must only write the first
    account-local report day into the current BJ business date.

    Docs-anchor:
    docs/superpowers/specs/2026-05-10-meta-xhr-report-date-filter-design.md
    """
    return date.fromisoformat(account_xhr_time_range(account, business_date)["since"])


def _parse_xhr_row_report_date(value: object) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def filter_xhr_insight_rows_to_report_date(
    rows: list[dict], report_date: date
) -> list[dict]:
    """Keep only XHR insight rows for ``report_date``.

    Production requests include ``date_start`` and ``date_stop``. Rows missing
    both fields are kept for backward compatibility with older tests and any
    unexpected Meta payload that omits date fields.
    """
    filtered: list[dict] = []
    for row in rows:
        row_dates = [
            parsed
            for parsed in (
                _parse_xhr_row_report_date(row.get("date_start")),
                _parse_xhr_row_report_date(row.get("date_stop")),
            )
            if parsed is not None
        ]
        if row_dates and any(item != report_date for item in row_dates):
            continue
        filtered.append(row)
    return filtered
