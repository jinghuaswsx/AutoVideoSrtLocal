from __future__ import annotations

import json
import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import requests

from appcore import scheduled_tasks
from appcore.db import query
from appcore.llm_provider_configs import ProviderConfigError, require_provider_config
from config import APIMART_BASE_URL_DEFAULT


log = logging.getLogger(__name__)

TASK_CODE = "apimart_balance_watchdog"
PROVIDER_CODE = "apimart_image"
USAGE_LOG_PROVIDER = "apimart"

USD_TO_CNY = Decimal("7.2")
LOW_BALANCE_USD = Decimal("20")
MIN_GAP_USD = Decimal("1.00")
MIN_GAP_RATIO = Decimal("0.20")
REQUEST_TIMEOUT_SECONDS = 15


class ApimartBalanceWatchdogError(RuntimeError):
    """Raised when the APIMART balance watchdog cannot build a trustworthy snapshot."""


def _to_decimal(value: Any, *, field: str) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ApimartBalanceWatchdogError(
            f"invalid APIMART balance field {field}: {value!r}"
        ) from exc


def _api_error_message(payload: dict[str, Any]) -> str:
    error = payload.get("error")
    if isinstance(error, dict):
        message = str(error.get("message") or error.get("code") or "").strip()
        if message:
            return message
    for key in ("message", "msg", "detail"):
        message = str(payload.get(key) or "").strip()
        if message:
            return message
    return "APIMART balance response did not contain success=true"


def parse_balance_payload(payload: dict[str, Any], *, label: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ApimartBalanceWatchdogError(f"invalid APIMART {label} balance response")
    if payload.get("success") is not True:
        raise ApimartBalanceWatchdogError(_api_error_message(payload))
    return {
        "label": label,
        "remaining_usd": _to_decimal(payload.get("remain_balance"), field="remain_balance"),
        "used_usd": _to_decimal(payload.get("used_balance"), field="used_balance"),
        "unlimited_quota": bool(payload.get("unlimited_quota")),
    }


def _json_get(url: str, api_key: str) -> dict[str, Any]:
    try:
        response = requests.get(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        raise ApimartBalanceWatchdogError(f"APIMART balance request failed: {exc}") from exc
    except ValueError as exc:
        raise ApimartBalanceWatchdogError("APIMART balance response was not JSON") from exc
    if not isinstance(payload, dict):
        raise ApimartBalanceWatchdogError("APIMART balance response was not an object")
    return payload


def balance_snapshot(
    *,
    api_key_balance: dict[str, Any],
    account_balance: dict[str, Any],
    base_url: str,
    api_key_tail: str,
    fetched_at: datetime | None = None,
) -> dict[str, Any]:
    return {
        "fetched_at": fetched_at or datetime.now(),
        "base_url": base_url,
        "api_key_tail": api_key_tail,
        "apimart": {
            "api_key": api_key_balance,
            "account": account_balance,
        },
    }


def fetch_balance_snapshot() -> dict[str, Any]:
    try:
        cfg = require_provider_config(PROVIDER_CODE)
        api_key = cfg.require_api_key()
        base_url = cfg.require_base_url(default=APIMART_BASE_URL_DEFAULT).rstrip("/")
    except ProviderConfigError as exc:
        raise ApimartBalanceWatchdogError(str(exc)) from exc

    api_key_balance = parse_balance_payload(
        _json_get(f"{base_url}/v1/balance", api_key),
        label="api_key",
    )
    account_balance = parse_balance_payload(
        _json_get(f"{base_url}/v1/user/balance", api_key),
        label="account",
    )
    return balance_snapshot(
        api_key_balance=api_key_balance,
        account_balance=account_balance,
        base_url=base_url,
        api_key_tail=api_key[-6:],
    )


def local_usage_summary(
    *,
    cost_cny: Decimal | int | str | None = Decimal("0"),
    call_count: int = 0,
    unpriced_calls: int = 0,
) -> dict[str, Any]:
    cost_cny_decimal = _to_decimal(cost_cny, field="cost_cny")
    cost_usd = Decimal("0")
    if cost_cny_decimal:
        cost_usd = cost_cny_decimal / USD_TO_CNY
    return {
        "cost_cny": cost_cny_decimal,
        "cost_usd": cost_usd,
        "call_count": int(call_count or 0),
        "unpriced_calls": int(unpriced_calls or 0),
        "usd_to_cny": USD_TO_CNY,
    }


def local_apimart_usage_usd(
    start: datetime | None,
    end: datetime | None,
) -> dict[str, Any]:
    if start is None or end is None:
        return local_usage_summary()
    rows = query(
        """
        SELECT COALESCE(SUM(cost_cny), 0) AS cost_cny,
               COUNT(*) AS call_count,
               COALESCE(SUM(CASE WHEN cost_cny IS NULL THEN 1 ELSE 0 END), 0) AS unpriced_calls
        FROM usage_logs
        WHERE provider = %s
          AND success = 1
          AND called_at >= %s
          AND called_at < %s
        """,
        (USAGE_LOG_PROVIDER, start, end),
    )
    row = rows[0] if rows else {}
    return local_usage_summary(
        cost_cny=row.get("cost_cny") or Decimal("0"),
        call_count=int(row.get("call_count") or 0),
        unpriced_calls=int(row.get("unpriced_calls") or 0),
    )


def _decode_summary(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        decoded = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def latest_success_snapshot() -> dict[str, Any] | None:
    rows = query(
        """
        SELECT id, finished_at, summary_json
        FROM scheduled_task_runs
        WHERE task_code = %s
          AND status = 'success'
          AND summary_json IS NOT NULL
        ORDER BY finished_at DESC, id DESC
        LIMIT 1
        """,
        (TASK_CODE,),
    )
    if not rows:
        return None
    row = rows[0]
    summary = _decode_summary(row.get("summary_json"))
    api_key_summary = (
        summary.get("apimart", {}).get("api_key", {})
        if isinstance(summary.get("apimart"), dict)
        else {}
    )
    used_usd = api_key_summary.get("used_usd")
    if used_usd is None:
        return None
    return {
        "run_id": row.get("id"),
        "finished_at": row.get("finished_at"),
        "api_key_used_usd": _to_decimal(used_usd, field="api_key.used_usd"),
    }


def _remote_delta(current_used: Decimal, previous_used: Decimal | None) -> Decimal:
    if previous_used is None:
        return Decimal("0")
    return max(current_used - previous_used, Decimal("0"))


def _gap_ratio(gap_usd: Decimal, remote_delta_usd: Decimal) -> Decimal:
    if remote_delta_usd <= 0:
        return Decimal("0")
    return gap_usd / max(remote_delta_usd, Decimal("0.01"))


def evaluate_snapshot(
    *,
    current: dict[str, Any],
    previous: dict[str, Any] | None,
    local_usage: dict[str, Any],
) -> dict[str, Any]:
    api_key_balance = current["apimart"]["api_key"]
    remaining_usd = _to_decimal(api_key_balance.get("remaining_usd"), field="remaining_usd")
    current_used = _to_decimal(api_key_balance.get("used_usd"), field="used_usd")
    previous_used = (
        _to_decimal(previous.get("api_key_used_usd"), field="previous.api_key_used_usd")
        if previous
        else None
    )
    remote_delta_usd = _remote_delta(current_used, previous_used)
    local_usage_usd = _to_decimal(local_usage.get("cost_usd"), field="local_usage.cost_usd")
    gap_usd = max(remote_delta_usd - local_usage_usd, Decimal("0"))
    gap_ratio = _gap_ratio(gap_usd, remote_delta_usd)

    base = {
        "remote_delta_usd": remote_delta_usd,
        "local_usage_usd": local_usage_usd,
        "gap_usd": gap_usd,
        "gap_ratio": gap_ratio,
        "low_balance_threshold_usd": LOW_BALANCE_USD,
        "gap_threshold_usd": MIN_GAP_USD,
        "gap_ratio_threshold": MIN_GAP_RATIO,
    }

    if remaining_usd < LOW_BALANCE_USD and not api_key_balance.get("unlimited_quota"):
        return {
            **base,
            "alert": True,
            "reason": "low_balance",
            "message": (
                f"APIMART remaining balance is {remaining_usd} USD, "
                f"below {LOW_BALANCE_USD} USD."
            ),
        }

    if previous is None:
        return {
            **base,
            "alert": False,
            "reason": "baseline",
            "message": "APIMART balance baseline recorded.",
        }

    if gap_usd >= MIN_GAP_USD and gap_ratio >= MIN_GAP_RATIO:
        return {
            **base,
            "alert": True,
            "reason": "usage_gap",
            "message": (
                "Detected unexplained APIMART usage: "
                f"remote delta {remote_delta_usd} USD, local billing {local_usage_usd} USD, "
                f"gap {gap_usd} USD."
            ),
        }

    return {
        **base,
        "alert": False,
        "reason": "normal",
        "message": "APIMART balance movement matches local billing.",
    }


def _summary(
    *,
    status: str,
    current: dict[str, Any] | None = None,
    previous: dict[str, Any] | None = None,
    local_usage: dict[str, Any] | None = None,
    evaluation: dict[str, Any] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    evaluation = evaluation or {}
    return {
        "status": status,
        "reason": evaluation.get("reason") or ("balance_query_failed" if error else "unknown"),
        "message": evaluation.get("message") or error,
        "fetched_at": current.get("fetched_at") if current else None,
        "base_url": current.get("base_url") if current else None,
        "api_key_tail": current.get("api_key_tail") if current else None,
        "apimart": current.get("apimart") if current else None,
        "previous": previous,
        "local_usage": local_usage or local_usage_summary(),
        "remote_delta_usd": evaluation.get("remote_delta_usd", Decimal("0")),
        "local_usage_usd": evaluation.get("local_usage_usd", Decimal("0")),
        "gap_usd": evaluation.get("gap_usd", Decimal("0")),
        "gap_ratio": evaluation.get("gap_ratio", Decimal("0")),
        "thresholds": {
            "low_balance_usd": LOW_BALANCE_USD,
            "min_gap_usd": MIN_GAP_USD,
            "min_gap_ratio": MIN_GAP_RATIO,
            "usd_to_cny": USD_TO_CNY,
        },
    }


def run_scheduled_check(*, scheduled_for: datetime | None = None) -> dict[str, Any]:
    run_id = scheduled_tasks.start_run(TASK_CODE, scheduled_for=scheduled_for)
    try:
        current = fetch_balance_snapshot()
        previous = latest_success_snapshot()
        end = current.get("fetched_at")
        start = previous.get("finished_at") if previous else None
        local_usage = local_apimart_usage_usd(start, end) if start and end else local_usage_summary()
        evaluation = evaluate_snapshot(
            current=current,
            previous=previous,
            local_usage=local_usage,
        )
        status = "failed" if evaluation["alert"] else "success"
        summary = _summary(
            status=status,
            current=current,
            previous=previous,
            local_usage=local_usage,
            evaluation=evaluation,
        )
        scheduled_tasks.finish_run(
            run_id,
            status=status,
            summary=summary,
            error_message=evaluation["message"] if status == "failed" else None,
        )
        return summary
    except Exception as exc:
        message = f"APIMART balance watchdog failed: {exc}"
        log.warning(message, exc_info=True)
        summary = _summary(status="failed", error=message)
        scheduled_tasks.finish_run(
            run_id,
            status="failed",
            summary=summary,
            error_message=message,
        )
        return summary


def register(scheduler: Any):
    return scheduled_tasks.add_controlled_job(
        scheduler,
        TASK_CODE,
        run_scheduled_check,
        "interval",
        hours=1,
        id=TASK_CODE,
        replace_existing=True,
        max_instances=1,
    )
