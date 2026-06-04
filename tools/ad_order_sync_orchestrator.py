from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from appcore import scheduled_tasks
from tools import dianxiaomi_order_import
from tools import meta_daily_final_sync

TIMEZONE = "Asia/Shanghai"
META_CUTOVER_HOUR_BJ = 16
TASK_PREVIOUS_BUSINESS_DAY = "ad_order_previous_business_day_sync"
TASK_PREVIOUS_WEEK = "ad_order_previous_week_sync"
DEFAULT_SITE_CODES = ["newjoy", "omurio"]


def bj_now() -> datetime:
    return datetime.now(ZoneInfo(TIMEZONE)).replace(tzinfo=None, microsecond=0)


def json_default(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()
    return str(value)


def covered_bj_dates(target_date: date) -> list[date]:
    return [target_date, target_date + timedelta(days=1)]


def target_dates_for_mode(mode: str, *, now: datetime | None = None) -> list[date]:
    value = now or bj_now()
    if mode == "previous-business-day":
        return [meta_daily_final_sync.completed_meta_business_date(value)]
    if mode == "previous-week":
        this_monday = value.date() - timedelta(days=value.date().weekday())
        previous_monday = this_monday - timedelta(days=7)
        return [previous_monday + timedelta(days=offset) for offset in range(7)]
    raise ValueError(f"unsupported sync mode: {mode}")
