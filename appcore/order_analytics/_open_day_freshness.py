"""Inline freshness for open-day order_profit_lines.

For an open BJ business day (target == current_meta_business_date), the
20-min cron tick that refreshes ``order_profit_lines`` is too coarse for
the user's "三个模块当天数据要一致" expectation: the realtime dashboard
reads ``dianxiaomi_order_lines`` directly while the order-profit and
product-profit dashboards read the cron-snapshotted ``order_profit_lines``,
so any order paid between two cron ticks shows up only on the realtime
dashboard until the next tick.

This helper closes the gap: when an open-day query is about to run, we
trigger ``order_profit_backfill.backfill(today, today)`` inline so the
profit dashboards see the same orders the realtime dashboard sees. The
cost is ~0.5s per dashboard request that hits an open day (closed-day
queries are unaffected). A 30-second in-process TTL prevents parallel
dashboard hits from re-running the backfill too aggressively — the data
moves slowly compared to dashboard refresh rates.

Spec: docs/superpowers/specs/2026-05-09-realtime-dashboard-ad-spend-source-of-truth.md
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import date

from appcore.order_analytics._helpers import current_meta_business_date

log = logging.getLogger(__name__)

# 30s TTL: dashboard refresh cadence is typically >> 30s, but the cron
# tick that "owns" steady-state freshness fires every 20 min, so 30s
# guarantees at least 40 inline refreshes per cron cycle worst-case
# (still cheap because backfill is single-day). Tunable via env var if
# real traffic needs a different shape.
_TTL_SECONDS = 30.0
_lock = threading.Lock()
_last_run_at_per_date: dict[date, float] = {}


def ensure_open_day_profit_lines_fresh(date_from: date, date_to: date) -> None:
    """If the (date_from, date_to) range covers the current open BJ
    business day, run ``order_profit_backfill.backfill(today, today)``
    inline so ``order_profit_lines`` reflects all orders that have
    landed in ``dianxiaomi_order_lines``. Closed-day-only ranges are
    unaffected and return immediately.

    Idempotent + rate-limited via 30s in-process cache so concurrent
    dashboard hits don't stampede the DB.
    """
    if not date_from or not date_to:
        return
    today = current_meta_business_date()
    if date_from > today or date_to < today:
        return  # range entirely in the past — nothing to refresh
    now = time.monotonic()
    with _lock:
        last = _last_run_at_per_date.get(today, 0.0)
        if now - last < _TTL_SECONDS:
            return
        _last_run_at_per_date[today] = now
    # Lazy import — backfill pulls in heavyweight dependencies
    # (profit_calculation, shopify_payments_import) we don't want to
    # touch on closed-day code paths.
    try:
        from tools.order_profit_backfill import backfill

        backfill(today, today, dry_run=False)
    except Exception as exc:  # noqa: BLE001 - never break the dashboard
        log.warning("open-day profit backfill skipped: %s", exc)
