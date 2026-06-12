"""广告预警每日飞书推送。

每天业务日切换后（BJ 17:00）把高亏损广告 Top 榜推送到飞书群，
附 24 小时公开分享链接。已标记处理/忽略的条目不进入榜单。
Docs anchor: docs/superpowers/specs/2026-06-12-ad-alert-action-workflow-design.md
"""
from __future__ import annotations

import logging
import os
from datetime import date
from typing import Any
from urllib.parse import urlencode

from appcore import ad_alerts, feishu_alerts, scheduled_tasks
import config

log = logging.getLogger(__name__)

TASK_CODE = "ad_alert_daily_feishu_report"
REPORT_LIMIT = 10
SHARE_EXPIRES_HOURS = 24


def build_report_text(
    business_date: date,
    items: list[ad_alerts.HighLossAdItem],
    share_url: str | None,
) -> str:
    """构建推送文本：榜单行 + 分享链接。"""
    lines = [f"【广告预警】{business_date.strftime('%m-%d')} 高亏损广告 Top {len(items)}"]
    for index, item in enumerate(items, start=1):
        metric = item.metrics.get("last_7d")
        spend = metric.spend_usd if metric else 0.0
        roas = metric.roas if metric else None
        roas_text = f"{roas:.2f}" if roas is not None else "无转化"
        parts = [
            f"{index}. {item.country or '-'} {item.name}",
            f"7天花费 ${spend:.0f}",
            f"7天ROAS {roas_text}",
        ]
        if item.consecutive_loss_days > 0:
            parts.append(f"连续亏损 {item.consecutive_loss_days} 天")
        lines.append(" ｜ ".join(parts))
    if share_url:
        lines.append(f"查看明细：{share_url}")
    else:
        lines.append("查看明细：/ad-alerts")
    return "\n".join(lines)


def _share_secret_key() -> str:
    return (os.environ.get("FLASK_SECRET_KEY") or "").strip()


def _share_base_url() -> str:
    return (getattr(config, "AD_ALERT_PUBLIC_SHARE_BASE_URL", "") or "").strip().rstrip("/")


def _build_share_url() -> str | None:
    """生成 24h 公开分享链接；无 SECRET_KEY 时返回 None。"""
    secret = _share_secret_key()
    if not secret:
        return None
    payload = ad_alerts.build_high_loss_share_payload(
        search=None,
        limit=REPORT_LIMIT,
        expires_in_hours=SHARE_EXPIRES_HOURS,
    )
    token = ad_alerts.sign_share_token(payload, secret)
    query = urlencode({"token": token, "expires": payload["expires_at"]})
    return f"{_share_base_url()}/ad-alerts/share/high-loss?{query}"


def tick_once() -> dict[str, Any]:
    run_id = None
    try:
        run_id = scheduled_tasks.start_run(TASK_CODE)
    except Exception:
        log.debug("ad alert daily report run start failed", exc_info=True)

    def _finish(status: str, summary: dict[str, Any], error: str | None = None) -> None:
        if not run_id:
            return
        try:
            scheduled_tasks.finish_run(
                run_id, status=status, summary=summary, error_message=error
            )
        except Exception:
            log.debug("ad alert daily report finish_run failed", exc_info=True)

    try:
        feishu_config = feishu_alerts.load_config()
        if not feishu_config.enabled:
            summary = {"skipped": "feishu_disabled"}
            _finish("success", summary)
            return summary

        business_date, items = ad_alerts.get_high_loss_ads(limit=REPORT_LIMIT)
        if not items:
            summary = {"skipped": "no_high_loss_ads"}
            _finish("success", summary)
            return summary

        share_url = None
        try:
            share_url = _build_share_url()
        except Exception:
            log.warning("ad alert daily report share link build failed", exc_info=True)

        text = build_report_text(business_date, items, share_url)
        result = feishu_alerts.send_text_message(text, config=feishu_config)
        summary = {
            "sent": bool(result.get("ok")),
            "ad_count": len(items),
            "business_date": business_date.isoformat(),
            "message_id": result.get("message_id"),
        }
        _finish("success" if result.get("ok") else "failed", summary,
                None if result.get("ok") else str(result.get("error") or "send failed")[:500])
        return summary
    except Exception as exc:
        _finish("failed", {}, str(exc)[:500])
        raise


def register(scheduler) -> None:
    scheduled_tasks.add_controlled_job(
        scheduler,
        TASK_CODE,
        tick_once,
        "cron",
        hour=17,
        minute=0,
        id=TASK_CODE,
        replace_existing=True,
        max_instances=1,
    )
