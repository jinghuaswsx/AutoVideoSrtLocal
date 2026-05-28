from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import requests

from appcore import settings as settings_store

log = logging.getLogger(__name__)

TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
MESSAGE_URL = "https://open.feishu.cn/open-apis/im/v1/messages"

# Docs-anchor: docs/superpowers/specs/2026-05-09-roi-hourly-sync-lock-recovery.md
SETTING_ENABLED = "feishu_alerts.enabled"
SETTING_APP_ID = "feishu_alerts.app_id"
SETTING_APP_SECRET = "feishu_alerts.app_secret"
SETTING_CHAT_ID = "feishu_alerts.chat_id"
SETTING_FAILURE_REPEAT_EVERY = "feishu_alerts.failure_repeat_every"
SETTING_FAILURE_REPEAT_HOURS = "feishu_alerts.failure_repeat_hours"
SETTING_FAILURE_LAST_ALERT_PREFIX = "feishu_alerts.failure_last_alert."
DEFAULT_FAILURE_REPEAT_EVERY = 5
DEFAULT_FAILURE_REPEAT_HOURS = 12
DEFAULT_FAILURE_MIN_CONSECUTIVE = 20

REQUEST_TIMEOUT = 8
ERROR_LIMIT = 900
SUMMARY_LIMIT = 500


class FeishuAlertError(RuntimeError):
    pass


class FeishuAlertConfigError(FeishuAlertError):
    pass


class FeishuAlertSendError(FeishuAlertError):
    pass


@dataclass(frozen=True)
class FeishuAlertConfig:
    enabled: bool
    app_id: str
    app_secret: str
    chat_id: str


def _setting(key: str) -> str:
    return (settings_store.get_setting(key) or "").strip()


def _clip(value: Any, limit: int) -> str:
    text = "" if value is None else str(value)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."


def _mask_secret(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    if len(text) <= 4:
        return "已配置（已隐藏）"
    return f"已配置（末四位 {text[-4:]}）"


def load_config() -> FeishuAlertConfig:
    return FeishuAlertConfig(
        enabled=_setting(SETTING_ENABLED) == "1",
        app_id=_setting(SETTING_APP_ID),
        app_secret=_setting(SETTING_APP_SECRET),
        chat_id=_setting(SETTING_CHAT_ID),
    )


def config_view() -> dict[str, Any]:
    config = load_config()
    return {
        "enabled": config.enabled,
        "app_id": config.app_id,
        "app_secret_present": bool(config.app_secret),
        "app_secret_mask": _mask_secret(config.app_secret),
        "chat_id": config.chat_id,
    }


def _validate_config(config: FeishuAlertConfig, *, require_enabled: bool) -> None:
    if require_enabled and not config.enabled:
        raise FeishuAlertConfigError("feishu alerts disabled")
    missing = []
    if not config.app_id:
        missing.append("app_id")
    if not config.app_secret:
        missing.append("app_secret")
    if not config.chat_id:
        missing.append("chat_id")
    if missing:
        raise FeishuAlertConfigError(f"feishu alert config missing: {', '.join(missing)}")


def _post_json(
    url: str,
    *,
    payload: dict[str, Any],
    headers: dict[str, str] | None = None,
    params: dict[str, str] | None = None,
) -> dict[str, Any]:
    try:
        response = requests.post(
            url,
            json=payload,
            headers=headers,
            params=params,
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise FeishuAlertSendError(f"request feishu failed: {_clip(exc, 240)}") from exc

    try:
        data = response.json()
    except ValueError as exc:
        raise FeishuAlertSendError(
            f"feishu response is not json: status={response.status_code} body={_clip(response.text, 240)}"
        ) from exc

    code = data.get("code")
    if response.status_code >= 400 or code not in (0, None):
        msg = data.get("msg") or data.get("message") or response.text
        raise FeishuAlertSendError(
            f"feishu api failed: status={response.status_code} code={code} msg={_clip(msg, 240)}"
        )
    return data


def fetch_tenant_access_token(config: FeishuAlertConfig | None = None) -> str:
    config = config or load_config()
    _validate_config(config, require_enabled=False)
    data = _post_json(
        TOKEN_URL,
        payload={"app_id": config.app_id, "app_secret": config.app_secret},
    )
    token = str(data.get("tenant_access_token") or "").strip()
    if not token:
        raise FeishuAlertSendError("feishu tenant_access_token missing")
    return token


def send_text_message(
    text: str,
    *,
    config: FeishuAlertConfig | None = None,
) -> dict[str, Any]:
    config = config or load_config()
    if not config.enabled:
        return {"ok": False, "skipped": True, "reason": "disabled"}
    _validate_config(config, require_enabled=True)
    token = fetch_tenant_access_token(config)
    data = _post_json(
        MESSAGE_URL,
        payload={
            "receive_id": config.chat_id,
            "msg_type": "text",
            "content": json.dumps({"text": text}, ensure_ascii=False),
        },
        headers={"Authorization": f"Bearer {token}"},
        params={"receive_id_type": "chat_id"},
    )
    message_id = str((data.get("data") or {}).get("message_id") or "")
    return {"ok": True, "message_id": message_id}


def _format_time(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    return str(value or "-")


def format_scheduled_task_failure(row: dict[str, Any]) -> str:
    task_code = str(row.get("task_code") or "-")
    task_name = str(row.get("task_name") or task_code)
    duration = row.get("duration_seconds")
    duration_text = "-" if duration in (None, "") else f"{duration}s"
    lines = [
        "【AutoVideoSrt 告警】定时任务失败",
        f"任务：{task_name} ({task_code})",
    ]
    consecutive = row.get("consecutive_failures")
    if isinstance(consecutive, int) and consecutive >= 2:
        lines.append(f"连续失败：{consecutive} 次")
    lines.extend([
        f"运行ID：{row.get('id') or '-'}",
        f"开始：{_format_time(row.get('started_at'))}",
        f"结束：{_format_time(row.get('finished_at'))}",
        f"耗时：{duration_text}",
        f"错误：{_clip(row.get('error_message') or '未记录错误信息', ERROR_LIMIT)}",
        f"查看：/scheduled-tasks?view=logs&task={task_code}",
    ])
    summary = row.get("summary") or {}
    if summary:
        summary_text = json.dumps(summary, ensure_ascii=False, default=str)
        if len(summary_text) <= SUMMARY_LIMIT:
            lines.insert(-1, f"摘要：{summary_text}")
    return "\n".join(lines)


def send_scheduled_task_failure(row: dict[str, Any]) -> dict[str, Any]:
    config = load_config()
    if not config.enabled:
        return {"ok": False, "skipped": True, "reason": "disabled"}
    try:
        return send_text_message(format_scheduled_task_failure(row), config=config)
    except FeishuAlertError as exc:
        log.warning("feishu alert send failed: %s", _clip(exc, 300))
        return {"ok": False, "error": _clip(exc, 300)}
    except Exception as exc:  # noqa: BLE001 - alert dispatch must not break run logging
        log.warning("feishu alert send failed unexpectedly", exc_info=True)
        return {"ok": False, "error": _clip(exc, 300)}


def _failure_repeat_every() -> int:
    raw = _setting(SETTING_FAILURE_REPEAT_EVERY)
    if not raw:
        return DEFAULT_FAILURE_REPEAT_EVERY
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_FAILURE_REPEAT_EVERY
    return value if value >= 1 else DEFAULT_FAILURE_REPEAT_EVERY


def _failure_repeat_hours() -> int:
    raw = _setting(SETTING_FAILURE_REPEAT_HOURS)
    if not raw:
        return DEFAULT_FAILURE_REPEAT_HOURS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_FAILURE_REPEAT_HOURS
    return value if value >= 1 else DEFAULT_FAILURE_REPEAT_HOURS


def _failure_alert_record_key(task_code: str) -> str:
    return f"{SETTING_FAILURE_LAST_ALERT_PREFIX}{str(task_code or '').strip()}"


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=None)


def failure_alert_record(task_code: str) -> dict[str, Any] | None:
    raw = _setting(_failure_alert_record_key(task_code))
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return {"alerted_at": raw}
    return payload if isinstance(payload, dict) else None


def _current_run_time(rows: list[dict[str, Any]], current_run_id: int | None) -> datetime | None:
    if current_run_id is None:
        return None
    for row in rows:
        if int(row.get("id") or 0) == int(current_run_id):
            return _parse_time(
                row.get("started_at") or row.get("finished_at") or row.get("created_at")
            )
    return None


def failure_alert_cooldown_allows(
    task_code: str,
    *,
    current_run_id: int | None = None,
    reference_at: Any = None,
) -> bool:
    record = failure_alert_record(task_code)
    if not record:
        return True
    last_alerted_at = _parse_time(record.get("alerted_at"))
    if last_alerted_at is None:
        return True
    current_at = _parse_time(reference_at) or datetime.now()
    return current_at - last_alerted_at >= timedelta(hours=_failure_repeat_hours())


def record_failure_alert_sent(
    task_code: str,
    *,
    run_id: int | None = None,
    alerted_at: Any = None,
) -> None:
    timestamp = _parse_time(alerted_at) or datetime.now()
    payload = {
        "run_id": int(run_id) if run_id is not None else None,
        "alerted_at": timestamp.isoformat(timespec="seconds"),
    }
    settings_store.set_setting(
        _failure_alert_record_key(task_code),
        json.dumps(payload, ensure_ascii=False),
    )


def _query_recent_run_statuses(task_code: str) -> list[dict[str, Any]]:
    """Indirection so tests can monkeypatch a single hook without touching db."""
    from appcore.db import query as _query

    return _query(
        "SELECT id, status, started_at, finished_at, created_at FROM scheduled_task_runs "
        "WHERE task_code=%s AND status IN ('success','failed') "
        "ORDER BY id DESC LIMIT 100",
        (task_code,),
    ) or []


def consecutive_failure_count(task_code: str, *, current_run_id: int | None) -> int:
    """How many consecutive `failed` runs for ``task_code`` ending at the
    given run, counting backwards until the first non-failed status.

    Always >= 1 for a failed terminal run; 0 if ``task_code`` has no failed
    runs at all (defensive — caller should only ask this for a freshly
    failed run).
    """
    rows = _query_recent_run_statuses(task_code)
    streak = 0
    for row in rows:
        if current_run_id is not None and int(row.get("id") or 0) > int(current_run_id):
            continue
        status = str(row.get("status") or "").strip()
        if status == "failed":
            streak += 1
            continue
        break
    return streak


def should_dispatch_failure(
    task_code: str,
    *,
    current_run_id: int | None,
    immediate: bool = False,
) -> tuple[bool, int]:
    rows = _query_recent_run_statuses(task_code)
    streak = 0
    for row in rows:
        if current_run_id is not None and int(row.get("id") or 0) > int(current_run_id):
            continue
        status = str(row.get("status") or "").strip()
        if status == "failed":
            streak += 1
            continue
        break
    if streak <= 0:
        return False, streak
    current_at = _current_run_time(rows, current_run_id)
    if immediate and streak == 1:
        return (
            failure_alert_cooldown_allows(
                task_code,
                current_run_id=current_run_id,
                reference_at=current_at,
            ),
            streak,
        )
    if streak < DEFAULT_FAILURE_MIN_CONSECUTIVE:
        return False, streak
    repeat = max(_failure_repeat_every(), DEFAULT_FAILURE_MIN_CONSECUTIVE)
    should_send = streak == DEFAULT_FAILURE_MIN_CONSECUTIVE or streak % repeat == 0
    if not should_send:
        return False, streak
    return (
        failure_alert_cooldown_allows(
            task_code,
            current_run_id=current_run_id,
            reference_at=current_at,
        ),
        streak,
    )


def format_scheduled_task_recovery(row: dict[str, Any], *, prior_failures: int) -> str:
    task_code = str(row.get("task_code") or "-")
    task_name = str(row.get("task_name") or task_code)
    duration = row.get("duration_seconds")
    duration_text = "-" if duration in (None, "") else f"{duration}s"
    return "\n".join(
        [
            "【AutoVideoSrt 恢复】定时任务转回成功",
            f"任务：{task_name} ({task_code})",
            f"运行ID：{row.get('id') or '-'}",
            f"开始：{_format_time(row.get('started_at'))}",
            f"结束：{_format_time(row.get('finished_at'))}",
            f"耗时：{duration_text}",
            f"此前连续失败次数：{prior_failures}",
            f"查看：/scheduled-tasks?view=logs&task={task_code}",
        ]
    )


def send_scheduled_task_recovery(
    row: dict[str, Any], *, prior_failures: int
) -> dict[str, Any]:
    if prior_failures <= 0:
        return {"ok": False, "skipped": True, "reason": "no_prior_failure"}
    config = load_config()
    if not config.enabled:
        return {"ok": False, "skipped": True, "reason": "disabled"}
    try:
        return send_text_message(
            format_scheduled_task_recovery(row, prior_failures=prior_failures),
            config=config,
        )
    except FeishuAlertError as exc:
        log.warning("feishu recovery alert send failed: %s", _clip(exc, 300))
        return {"ok": False, "error": _clip(exc, 300)}
    except Exception as exc:  # noqa: BLE001 - alert dispatch must not break run logging
        log.warning("feishu recovery alert send failed unexpectedly", exc_info=True)
        return {"ok": False, "error": _clip(exc, 300)}


def prior_consecutive_failures_before_run(
    task_code: str, *, current_run_id: int | None
) -> int:
    rows = _query_recent_run_statuses(task_code)
    streak = 0
    for row in rows:
        if current_run_id is not None and int(row.get("id") or 0) >= int(current_run_id):
            continue
        status = str(row.get("status") or "").strip()
        if status == "failed":
            streak += 1
            continue
        break
    return streak


def send_test_alert(message: str | None = None) -> dict[str, Any]:
    text = message or "AutoVideoSrt 飞书告警测试：scheduled_task_runs 失败通知已接入。"
    return send_text_message(text)
