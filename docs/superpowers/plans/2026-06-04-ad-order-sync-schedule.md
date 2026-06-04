# 广告与订单同步调度优化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the approved Meta-business-day ad/order sync schedule while leaving the existing 20-minute ROI realtime sync cadence unchanged.

**Architecture:** Keep `tools/roi_hourly_sync.py` as the 20-minute realtime path. Add a small orchestration CLI that composes existing Dianxiaomi order import and Meta daily-final sync for 12:00 previous-business-day and Monday previous-week backfills. Add a shared Dianxiaomi order-import lock so the new backfill jobs do not race the ROI child order import against the same DXM03 browser.

**Tech Stack:** Python 3.12, pytest, systemd unit/timer files, existing `scheduled_tasks`, `dianxiaomi_order_import`, `meta_daily_final_sync`, and `browser_automation_lock` helpers.

---

## File Structure

- Create: `tools/ad_order_sync_orchestrator.py`
  - CLI and testable functions for `previous-business-day` and `previous-week`.
  - Calls existing sync modules; does not parse Meta or Dianxiaomi data itself.
- Create: `appcore/dxm_order_import_lock.py`
  - Shared lock helper around `appcore.browser_automation_lock`.
  - Provides fixed timeout defaults from the spec: ROI 60s, backfill 600s.
- Create: `tests/test_ad_order_sync_orchestrator.py`
  - Date selection, covered Beijing natural dates, orchestration order, failure isolation.
- Create: `tests/test_dxm_order_import_lock.py`
  - Lock path defaults, env override, timeout summary shape.
- Modify: `tools/roi_hourly_sync.py`
  - Wrap `_run_dxm_recent_import` in the new DXM order-import lock.
  - On lock timeout, skip only the DXM child import and continue Meta realtime + snapshot.
- Modify: `deploy/server_browser/autovideosrt-meta-daily-final-sync.timer`
  - Move from `16:30` to `16:10`.
- Modify: `deploy/server_browser/autovideosrt-meta-daily-final-check.service`
  - Change from `--mode check` to `--mode run`.
  - Rename description to confirmation rerun.
- Modify: `deploy/server_browser/autovideosrt-meta-daily-final-check.timer`
  - Move from `17:00` to `19:00`.
- Create: `deploy/server_browser/autovideosrt-ad-order-previous-business-day-sync.service`
- Create: `deploy/server_browser/autovideosrt-ad-order-previous-business-day-sync.timer`
- Create: `deploy/server_browser/autovideosrt-ad-order-previous-week-sync.service`
- Create: `deploy/server_browser/autovideosrt-ad-order-previous-week-sync.timer`
- Modify: `appcore/scheduled_tasks.py`
  - Update stale ROI child-task wording.
  - Update Meta daily-final schedule wording.
  - Register `ad_order_previous_business_day_sync` and `ad_order_previous_week_sync`.
- Modify: `tests/test_server_browser_runtime.py`
  - Static systemd assertions.
- Modify: `tests/test_appcore_scheduled_tasks.py`
  - Scheduled task registry assertions.
- Modify: `tests/test_roi_hourly_sync_controls.py`
  - DXM lock timeout behavior in ROI sync.

---

### Task 1: Orchestrator Date Math

**Files:**
- Create: `tests/test_ad_order_sync_orchestrator.py`
- Create: `tools/ad_order_sync_orchestrator.py`

- [ ] **Step 1: Write failing tests for Meta-business-day targets**

Create `tests/test_ad_order_sync_orchestrator.py` with:

```python
from datetime import date, datetime


def test_previous_business_day_uses_meta_completed_business_day(monkeypatch):
    from tools import ad_order_sync_orchestrator as orch

    monkeypatch.setattr(
        orch.meta_daily_final_sync,
        "completed_meta_business_date",
        lambda now=None: date(2026, 6, 2),
    )

    assert orch.target_dates_for_mode(
        "previous-business-day",
        now=datetime(2026, 6, 4, 12, 0, 0),
    ) == [date(2026, 6, 2)]


def test_previous_week_returns_previous_iso_week_dates():
    from tools import ad_order_sync_orchestrator as orch

    assert orch.target_dates_for_mode(
        "previous-week",
        now=datetime(2026, 6, 8, 20, 30, 0),
    ) == [
        date(2026, 6, 1),
        date(2026, 6, 2),
        date(2026, 6, 3),
        date(2026, 6, 4),
        date(2026, 6, 5),
        date(2026, 6, 6),
        date(2026, 6, 7),
    ]


def test_covered_bj_dates_for_meta_business_day_spans_two_natural_days():
    from tools import ad_order_sync_orchestrator as orch

    assert orch.covered_bj_dates(date(2026, 6, 2)) == [
        date(2026, 6, 2),
        date(2026, 6, 3),
    ]
```

- [ ] **Step 2: Run tests and verify they fail because the module is missing**

Run:

```bash
pytest tests/test_ad_order_sync_orchestrator.py -q
```

Expected: FAIL with `ModuleNotFoundError` or import error for `tools.ad_order_sync_orchestrator`.

- [ ] **Step 3: Add minimal date helpers**

Create `tools/ad_order_sync_orchestrator.py` with:

```python
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
```

- [ ] **Step 4: Run tests and verify date helpers pass**

Run:

```bash
pytest tests/test_ad_order_sync_orchestrator.py -q
```

Expected: PASS, 3 tests.

- [ ] **Step 5: Commit Task 1**

```bash
git add tools/ad_order_sync_orchestrator.py tests/test_ad_order_sync_orchestrator.py
git commit -m "test(sync): cover ad order orchestrator date math" -m "Docs-anchor: docs/superpowers/specs/2026-06-04-ad-order-sync-schedule-design.md"
```

---

### Task 2: Single-Day Orchestration

**Files:**
- Modify: `tests/test_ad_order_sync_orchestrator.py`
- Modify: `tools/ad_order_sync_orchestrator.py`

- [ ] **Step 1: Add failing tests for one-day orchestration**

Append to `tests/test_ad_order_sync_orchestrator.py`:

```python
def test_run_one_business_day_imports_orders_then_meta_daily(monkeypatch):
    from tools import ad_order_sync_orchestrator as orch

    calls = []

    def fake_import(**kwargs):
        calls.append(("order", kwargs))
        return {"batch_id": 10, "summary": {"fetched_orders": 5}}

    def fake_final(target_date, *, mode, include_adsets):
        calls.append(("meta", {"target_date": target_date, "mode": mode, "include_adsets": include_adsets}))
        return {
            "status": "success",
            "run_id": 20,
            "profit_backfill": {"status": "success", "profit_run_id": 30},
        }

    monkeypatch.setattr(orch.dianxiaomi_order_import, "run_import_from_server_browser", fake_import)
    monkeypatch.setattr(orch.meta_daily_final_sync, "run_final_sync", fake_final)

    result = orch.run_one_business_day(date(2026, 6, 2), max_scan_pages=220)

    assert result["status"] == "success"
    assert [item[0] for item in calls] == ["order", "meta"]
    assert calls[0][1]["start_date_text"] == "2026-06-02"
    assert calls[0][1]["end_date_text"] == "2026-06-03"
    assert calls[0][1]["site_codes"] == ["newjoy", "omurio"]
    assert calls[0][1]["dxm_env"] == "DXM03-RJC"
    assert calls[0][1]["date_filter_mode"] == "recent-scan"
    assert calls[1][1] == {
        "target_date": date(2026, 6, 2),
        "mode": "run",
        "include_adsets": True,
    }
    assert result["order_import"]["batch_id"] == 10
    assert result["meta_daily_final"]["run_id"] == 20
    assert result["profit_backfill"]["profit_run_id"] == 30


def test_run_one_business_day_continues_meta_when_order_import_fails(monkeypatch):
    from tools import ad_order_sync_orchestrator as orch

    meta_calls = []

    def fake_import(**kwargs):
        raise RuntimeError("dxm unavailable")

    def fake_final(target_date, *, mode, include_adsets):
        meta_calls.append(target_date)
        return {"status": "success", "run_id": 21, "profit_backfill": {"status": "success"}}

    monkeypatch.setattr(orch.dianxiaomi_order_import, "run_import_from_server_browser", fake_import)
    monkeypatch.setattr(orch.meta_daily_final_sync, "run_final_sync", fake_final)

    result = orch.run_one_business_day(date(2026, 6, 2), max_scan_pages=220)

    assert result["status"] == "failed"
    assert result["order_import"]["status"] == "failed"
    assert "dxm unavailable" in result["order_import"]["error"]
    assert meta_calls == [date(2026, 6, 2)]
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
pytest tests/test_ad_order_sync_orchestrator.py -q
```

Expected: FAIL with `AttributeError` stating that `tools.ad_order_sync_orchestrator` has no attribute `run_one_business_day`.

- [ ] **Step 3: Implement `run_one_business_day`**

Append to `tools/ad_order_sync_orchestrator.py`:

```python
def _status_from_day_parts(order_report: dict[str, Any], meta_report: dict[str, Any]) -> str:
    if order_report.get("status") == "failed":
        return "failed"
    if meta_report.get("status") != "success":
        return "failed"
    profit = meta_report.get("profit_backfill") or {}
    if profit.get("status") not in ("success", None):
        return "failed"
    return "success"


def run_one_business_day(
    target_date: date,
    *,
    max_scan_pages: int,
    site_codes: list[str] | None = None,
    dxm_env: str = "DXM03-RJC",
) -> dict[str, Any]:
    bj_dates = covered_bj_dates(target_date)
    order_report: dict[str, Any]
    try:
        order_report = dianxiaomi_order_import.run_import_from_server_browser(
            start_date_text=bj_dates[0].isoformat(),
            end_date_text=bj_dates[-1].isoformat(),
            site_codes=site_codes or DEFAULT_SITE_CODES,
            states=[""],
            dxm_env=dxm_env,
            dry_run=False,
            skip_login_prompt=True,
            date_filter_mode="recent-scan",
            max_scan_pages=max_scan_pages,
        )
        order_report.setdefault("status", "success")
    except Exception as exc:
        order_report = {"status": "failed", "error": str(exc)}

    try:
        meta_report = meta_daily_final_sync.run_final_sync(
            target_date,
            mode="run",
            include_adsets=True,
        )
    except Exception as exc:
        meta_report = {"status": "failed", "error": str(exc)}

    return {
        "target_date": target_date.isoformat(),
        "covered_bj_dates": [item.isoformat() for item in bj_dates],
        "status": _status_from_day_parts(order_report, meta_report),
        "order_import": order_report,
        "meta_daily_final": meta_report,
        "profit_backfill": meta_report.get("profit_backfill") or {},
    }
```

- [ ] **Step 4: Run tests and verify pass**

Run:

```bash
pytest tests/test_ad_order_sync_orchestrator.py -q
```

Expected: PASS, 5 tests.

- [ ] **Step 5: Commit Task 2**

```bash
git add tools/ad_order_sync_orchestrator.py tests/test_ad_order_sync_orchestrator.py
git commit -m "feat(sync): orchestrate one ad order business day" -m "Docs-anchor: docs/superpowers/specs/2026-06-04-ad-order-sync-schedule-design.md"
```

---

### Task 3: Orchestrator Run Logging and CLI

**Files:**
- Modify: `tests/test_ad_order_sync_orchestrator.py`
- Modify: `tools/ad_order_sync_orchestrator.py`

- [ ] **Step 1: Add failing tests for run-level logging and failure isolation**

Append to `tests/test_ad_order_sync_orchestrator.py`:

```python
def test_run_orchestrator_records_scheduled_task_run(monkeypatch):
    from tools import ad_order_sync_orchestrator as orch

    finished = []
    monkeypatch.setattr(orch.scheduled_tasks, "start_run", lambda task_code: 99)
    monkeypatch.setattr(
        orch.scheduled_tasks,
        "finish_run",
        lambda run_id, status, summary, error_message=None, output_file=None: finished.append(
            {
                "run_id": run_id,
                "status": status,
                "summary": summary,
                "error_message": error_message,
                "output_file": output_file,
            }
        ),
    )
    monkeypatch.setattr(
        orch,
        "target_dates_for_mode",
        lambda mode, now=None: [date(2026, 6, 2)],
    )
    monkeypatch.setattr(
        orch,
        "run_one_business_day",
        lambda target_date, max_scan_pages, site_codes=None, dxm_env="DXM03-RJC": {
            "target_date": target_date.isoformat(),
            "status": "success",
            "order_import": {"status": "success"},
            "meta_daily_final": {"status": "success"},
            "profit_backfill": {"status": "success"},
        },
    )

    result = orch.run_orchestrator(
        mode="previous-business-day",
        now=datetime(2026, 6, 4, 12, 0, 0),
        max_scan_pages=220,
    )

    assert result["status"] == "success"
    assert result["run_id"] == 99
    assert finished[0]["run_id"] == 99
    assert finished[0]["status"] == "success"
    assert finished[0]["summary"]["target_dates"] == ["2026-06-02"]


def test_run_orchestrator_previous_week_continues_after_failed_day(monkeypatch):
    from tools import ad_order_sync_orchestrator as orch

    monkeypatch.setattr(orch.scheduled_tasks, "start_run", lambda task_code: 100)
    monkeypatch.setattr(orch.scheduled_tasks, "finish_run", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        orch,
        "target_dates_for_mode",
        lambda mode, now=None: [date(2026, 6, 1), date(2026, 6, 2), date(2026, 6, 3)],
    )
    seen = []

    def fake_day(target_date, max_scan_pages, site_codes=None, dxm_env="DXM03-RJC"):
        seen.append(target_date)
        return {"target_date": target_date.isoformat(), "status": "failed" if target_date.day == 2 else "success"}

    monkeypatch.setattr(orch, "run_one_business_day", fake_day)

    result = orch.run_orchestrator(
        mode="previous-week",
        now=datetime(2026, 6, 8, 20, 30, 0),
        max_scan_pages=500,
    )

    assert seen == [date(2026, 6, 1), date(2026, 6, 2), date(2026, 6, 3)]
    assert result["status"] == "failed"
    assert [day["status"] for day in result["days"]] == ["success", "failed", "success"]


def test_cli_forwards_mode_and_max_scan_pages(monkeypatch, capsys):
    from tools import ad_order_sync_orchestrator as orch

    calls = []
    monkeypatch.setattr(
        orch,
        "run_orchestrator",
        lambda **kwargs: calls.append(kwargs) or {"status": "success", "mode": kwargs["mode"]},
    )

    rc = orch.main(["--mode", "previous-week", "--max-scan-pages", "500"])

    assert rc == 0
    assert calls[0]["mode"] == "previous-week"
    assert calls[0]["max_scan_pages"] == 500
    assert '"status": "success"' in capsys.readouterr().out
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
pytest tests/test_ad_order_sync_orchestrator.py -q
```

Expected: FAIL with missing `run_orchestrator` and `main`.

- [ ] **Step 3: Implement run-level orchestration and CLI**

Append to `tools/ad_order_sync_orchestrator.py`:

```python
def task_code_for_mode(mode: str) -> str:
    if mode == "previous-business-day":
        return TASK_PREVIOUS_BUSINESS_DAY
    if mode == "previous-week":
        return TASK_PREVIOUS_WEEK
    raise ValueError(f"unsupported sync mode: {mode}")


def run_orchestrator(
    *,
    mode: str,
    now: datetime | None = None,
    max_scan_pages: int,
    site_codes: list[str] | None = None,
    dxm_env: str = "DXM03-RJC",
) -> dict[str, Any]:
    started = time.time()
    targets = target_dates_for_mode(mode, now=now)
    task_code = task_code_for_mode(mode)
    run_id = scheduled_tasks.start_run(task_code)
    summary: dict[str, Any] = {
        "mode": mode,
        "target_dates": [item.isoformat() for item in targets],
        "timezone": TIMEZONE,
        "meta_cutover_hour_bj": META_CUTOVER_HOUR_BJ,
        "max_scan_pages": max_scan_pages,
        "days": [],
    }
    status = "success"
    error_message = None
    try:
        for target in targets:
            day_report = run_one_business_day(
                target,
                max_scan_pages=max_scan_pages,
                site_codes=site_codes,
                dxm_env=dxm_env,
            )
            summary["days"].append(day_report)
            if day_report.get("status") != "success":
                status = "failed"
        if status == "failed":
            failed_dates = [
                day.get("target_date")
                for day in summary["days"]
                if day.get("status") != "success"
            ]
            error_message = "ad/order sync failed for target_dates=" + ",".join(failed_dates)
        return {**summary, "status": status, "run_id": run_id}
    except Exception as exc:
        status = "failed"
        error_message = str(exc)
        summary["error"] = error_message
        raise
    finally:
        summary["duration_seconds"] = round(time.time() - started, 2)
        scheduled_tasks.finish_run(
            run_id,
            status=status,
            summary=summary,
            error_message=error_message,
        )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sync ad daily-final data and Dianxiaomi orders by Meta business day.")
    parser.add_argument("--mode", choices=("previous-business-day", "previous-week"), required=True)
    parser.add_argument("--max-scan-pages", type=int, default=None)
    parser.add_argument("--sites", default=",".join(DEFAULT_SITE_CODES))
    parser.add_argument("--dxm-env", default="DXM03-RJC")
    return parser


def _csv_list(value: str) -> list[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    max_scan_pages = args.max_scan_pages
    if max_scan_pages is None:
        max_scan_pages = 500 if args.mode == "previous-week" else 220
    result = run_orchestrator(
        mode=args.mode,
        max_scan_pages=max(1, int(max_scan_pages)),
        site_codes=_csv_list(args.sites),
        dxm_env=args.dxm_env,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=json_default))
    return 0 if result.get("status") == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests and verify pass**

Run:

```bash
pytest tests/test_ad_order_sync_orchestrator.py -q
```

Expected: PASS, 8 tests.

- [ ] **Step 5: Commit Task 3**

```bash
git add tools/ad_order_sync_orchestrator.py tests/test_ad_order_sync_orchestrator.py
git commit -m "feat(sync): add ad order sync orchestrator cli" -m "Docs-anchor: docs/superpowers/specs/2026-06-04-ad-order-sync-schedule-design.md"
```

---

### Task 4: Dianxiaomi Order Import Lock Helper

**Files:**
- Create: `tests/test_dxm_order_import_lock.py`
- Create: `appcore/dxm_order_import_lock.py`

- [ ] **Step 1: Write failing tests for lock defaults and timeout summary**

Create `tests/test_dxm_order_import_lock.py` with:

```python
import pytest


def test_default_lock_path_uses_output_locally(monkeypatch, tmp_path):
    from appcore import dxm_order_import_lock as lock

    monkeypatch.delenv("DXM_ORDER_IMPORT_LOCK_PATH", raising=False)
    monkeypatch.setattr(lock.Path, "cwd", lambda: tmp_path)
    monkeypatch.setattr(lock, "DEFAULT_LINUX_LOCK_PATH", tmp_path / "linux" / "automation.lock")
    monkeypatch.setattr(lock.os.path, "exists", lambda path: False)

    assert lock.default_dxm_order_import_lock_path() == tmp_path / "output" / "browser_automation" / "dxm_order_import.lock"


def test_env_lock_path_wins(monkeypatch, tmp_path):
    from appcore import dxm_order_import_lock as lock

    custom = tmp_path / "custom.lock"
    monkeypatch.setenv("DXM_ORDER_IMPORT_LOCK_PATH", str(custom))

    assert lock.default_dxm_order_import_lock_path() == custom


def test_timeout_summary_reads_holder_json(tmp_path):
    from appcore import dxm_order_import_lock as lock

    path = tmp_path / "automation.lock"
    path.write_text('{"pid":123,"command":"python tools/roi_hourly_sync.py"}\n', encoding="utf-8")

    summary = lock.lock_timeout_summary(path, timeout_seconds=60, error_message="busy")

    assert summary["lock_path"] == str(path)
    assert summary["timeout_seconds"] == 60
    assert summary["holder_pid"] == 123
    assert summary["holder_command"] == "python tools/roi_hourly_sync.py"
    assert summary["error"] == "busy"
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
pytest tests/test_dxm_order_import_lock.py -q
```

Expected: FAIL with import error for `appcore.dxm_order_import_lock`.

- [ ] **Step 3: Implement lock helper**

Create `appcore/dxm_order_import_lock.py` with:

```python
"""Dianxiaomi order-import browser lock.

Docs-anchor:
docs/superpowers/specs/2026-06-04-ad-order-sync-schedule-design.md#店小秘订单导入细节
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from appcore.browser_automation_lock import browser_automation_lock

DEFAULT_LINUX_LOCK_PATH = Path("/data/autovideosrt/browser/runtime-dxm-order-import/automation.lock")
DEFAULT_LOCAL_LOCK_PATH = Path("output") / "browser_automation" / "dxm_order_import.lock"
ROI_LOCK_TIMEOUT_SECONDS = 60
BACKFILL_LOCK_TIMEOUT_SECONDS = 600
LOCK_RETRY_SECONDS = 5


def default_dxm_order_import_lock_path() -> Path:
    configured = os.environ.get("DXM_ORDER_IMPORT_LOCK_PATH")
    if configured:
        return Path(configured)
    if os.name != "nt" and (DEFAULT_LINUX_LOCK_PATH.parent.exists() or os.path.exists("/data/autovideosrt")):
        return DEFAULT_LINUX_LOCK_PATH
    return Path.cwd() / DEFAULT_LOCAL_LOCK_PATH


def read_lock_holder(path: str | os.PathLike[str]) -> dict[str, Any]:
    try:
        text = Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return {}
    if not text:
        return {}
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def lock_timeout_summary(
    path: str | os.PathLike[str],
    *,
    timeout_seconds: int,
    error_message: str,
) -> dict[str, Any]:
    holder = read_lock_holder(path)
    return {
        "status": "skipped_lock_timeout",
        "lock_path": str(path),
        "timeout_seconds": int(timeout_seconds),
        "holder_pid": holder.get("pid"),
        "holder_command": holder.get("command"),
        "error": error_message,
    }


@contextmanager
def dxm_order_import_lock(
    *,
    task_code: str,
    timeout_seconds: int,
    command: str,
    lock_path: str | os.PathLike[str] | None = None,
) -> Iterator[Path]:
    path = Path(lock_path) if lock_path is not None else default_dxm_order_import_lock_path()
    with browser_automation_lock(
        task_code=task_code,
        timeout_seconds=timeout_seconds,
        retry_seconds=LOCK_RETRY_SECONDS,
        command=command,
        lock_path=path,
    ) as acquired_path:
        yield acquired_path
```

- [ ] **Step 4: Run tests and verify pass**

Run:

```bash
pytest tests/test_dxm_order_import_lock.py -q
```

Expected: PASS, 3 tests.

- [ ] **Step 5: Commit Task 4**

```bash
git add appcore/dxm_order_import_lock.py tests/test_dxm_order_import_lock.py
git commit -m "feat(sync): add dianxiaomi order import lock" -m "Docs-anchor: docs/superpowers/specs/2026-06-04-ad-order-sync-schedule-design.md"
```

---

### Task 5: Wire Lock into ROI and Orchestrator

**Files:**
- Modify: `tests/test_roi_hourly_sync_controls.py`
- Modify: `tests/test_ad_order_sync_orchestrator.py`
- Modify: `tools/roi_hourly_sync.py`
- Modify: `tools/ad_order_sync_orchestrator.py`

- [ ] **Step 1: Add failing ROI lock-timeout test**

Append to `tests/test_roi_hourly_sync_controls.py`:

```python
def test_run_sync_skips_only_dxm_when_order_import_lock_times_out(monkeypatch):
    from appcore.browser_automation_lock import BrowserAutomationLockTimeout
    from appcore import scheduled_tasks
    from tools import roi_hourly_sync

    monkeypatch.setattr(scheduled_tasks, "is_task_enabled", lambda task_code: True)
    monkeypatch.setattr(roi_hourly_sync, "_start_run", lambda *args, **kwargs: 7)
    finishes = []
    monkeypatch.setattr(roi_hourly_sync, "_finish_run", lambda *args, **kwargs: finishes.append(args))
    monkeypatch.setattr(roi_hourly_sync, "_insert_daily_snapshot", lambda *args, **kwargs: 11)
    monkeypatch.setattr(
        roi_hourly_sync,
        "_sync_meta_realtime_daily",
        lambda *args, **kwargs: {"status": "success", "rows_imported": 3},
    )

    class TimeoutLock:
        def __enter__(self):
            raise BrowserAutomationLockTimeout("browser automation lock timeout after 60s: /tmp/dxm.lock")

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(roi_hourly_sync.dxm_order_import_lock, "dxm_order_import_lock", lambda **kwargs: TimeoutLock())
    monkeypatch.setattr(
        roi_hourly_sync.dxm_order_import_lock,
        "default_dxm_order_import_lock_path",
        lambda: "/tmp/dxm.lock",
    )

    result = roi_hourly_sync.run_sync(now=datetime(2026, 6, 4, 12, 0, 0))

    assert result["status"] == "success"
    assert result["dxm_report"]["status"] == "skipped_lock_timeout"
    assert result["meta_realtime_report"]["status"] == "success"
    assert result["snapshot_id"] == 11
```

- [ ] **Step 2: Add failing orchestrator lock use test**

Append to `tests/test_ad_order_sync_orchestrator.py`:

```python
def test_run_one_business_day_uses_backfill_lock(monkeypatch):
    from tools import ad_order_sync_orchestrator as orch

    lock_calls = []

    class FakeLock:
        def __enter__(self):
            lock_calls.append("enter")
            return "/tmp/dxm.lock"

        def __exit__(self, exc_type, exc, tb):
            lock_calls.append("exit")
            return False

    monkeypatch.setattr(orch.dxm_order_import_lock, "dxm_order_import_lock", lambda **kwargs: FakeLock())
    monkeypatch.setattr(
        orch.dianxiaomi_order_import,
        "run_import_from_server_browser",
        lambda **kwargs: {"batch_id": 1, "summary": {}},
    )
    monkeypatch.setattr(
        orch.meta_daily_final_sync,
        "run_final_sync",
        lambda target_date, *, mode, include_adsets: {"status": "success", "profit_backfill": {"status": "success"}},
    )

    result = orch.run_one_business_day(date(2026, 6, 2), max_scan_pages=220)

    assert result["status"] == "success"
    assert lock_calls == ["enter", "exit"]
```

- [ ] **Step 3: Run tests and verify failure**

Run:

```bash
pytest tests/test_roi_hourly_sync_controls.py tests/test_ad_order_sync_orchestrator.py -q
```

Expected: FAIL because lock imports/wrapping are not implemented.

- [ ] **Step 4: Wire ROI lock**

In `tools/roi_hourly_sync.py`, add imports near existing `appcore` imports:

```python
from appcore import dxm_order_import_lock
from appcore.browser_automation_lock import BrowserAutomationLockTimeout
```

In `run_sync`, replace the current DXM block:

```python
elif not skip_dxm_fetch:
    dxm_report = _run_dxm_recent_import(business_window_start, snapshot_at, max_scan_pages=max_scan_pages)
    summary["dxm_import_batch_id"] = dxm_report.get("batch_id")
    summary["dxm_report"] = dxm_report
```

with:

```python
elif not skip_dxm_fetch:
    lock_path = dxm_order_import_lock.default_dxm_order_import_lock_path()
    try:
        with dxm_order_import_lock.dxm_order_import_lock(
            task_code="roi_hourly_sync",
            timeout_seconds=dxm_order_import_lock.ROI_LOCK_TIMEOUT_SECONDS,
            command="tools/roi_hourly_sync.py::_run_dxm_recent_import",
            lock_path=lock_path,
        ):
            dxm_report = _run_dxm_recent_import(
                business_window_start,
                snapshot_at,
                max_scan_pages=max_scan_pages,
            )
        summary["dxm_import_batch_id"] = dxm_report.get("batch_id")
        summary["dxm_report"] = dxm_report
    except BrowserAutomationLockTimeout as exc:
        summary["dxm_report"] = dxm_order_import_lock.lock_timeout_summary(
            lock_path,
            timeout_seconds=dxm_order_import_lock.ROI_LOCK_TIMEOUT_SECONDS,
            error_message=str(exc),
        )
```

- [ ] **Step 5: Wire orchestrator lock**

In `tools/ad_order_sync_orchestrator.py`, add imports:

```python
from appcore import dxm_order_import_lock
from appcore.browser_automation_lock import BrowserAutomationLockTimeout
```

Inside `run_one_business_day`, wrap the `dianxiaomi_order_import.run_import_from_server_browser` call:

```python
lock_path = dxm_order_import_lock.default_dxm_order_import_lock_path()
try:
    with dxm_order_import_lock.dxm_order_import_lock(
        task_code=TASK_PREVIOUS_BUSINESS_DAY,
        timeout_seconds=dxm_order_import_lock.BACKFILL_LOCK_TIMEOUT_SECONDS,
        command="tools/ad_order_sync_orchestrator.py::run_one_business_day",
        lock_path=lock_path,
    ):
        order_report = dianxiaomi_order_import.run_import_from_server_browser(
            start_date_text=bj_dates[0].isoformat(),
            end_date_text=bj_dates[-1].isoformat(),
            site_codes=site_codes or DEFAULT_SITE_CODES,
            states=[""],
            dxm_env=dxm_env,
            dry_run=False,
            skip_login_prompt=True,
            date_filter_mode="recent-scan",
            max_scan_pages=max_scan_pages,
        )
    order_report.setdefault("status", "success")
except BrowserAutomationLockTimeout as exc:
    order_report = dxm_order_import_lock.lock_timeout_summary(
        lock_path,
        timeout_seconds=dxm_order_import_lock.BACKFILL_LOCK_TIMEOUT_SECONDS,
        error_message=str(exc),
    )
    order_report["status"] = "failed"
except Exception as exc:
    order_report = {"status": "failed", "error": str(exc)}
```

- [ ] **Step 6: Run tests and verify pass**

Run:

```bash
pytest tests/test_roi_hourly_sync_controls.py tests/test_ad_order_sync_orchestrator.py tests/test_dxm_order_import_lock.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit Task 5**

```bash
git add tools/roi_hourly_sync.py tools/ad_order_sync_orchestrator.py tests/test_roi_hourly_sync_controls.py tests/test_ad_order_sync_orchestrator.py
git commit -m "feat(sync): serialize dianxiaomi order imports" -m "Docs-anchor: docs/superpowers/specs/2026-06-04-ad-order-sync-schedule-design.md"
```

---

### Task 6: Systemd Units and Timers

**Files:**
- Modify: `tests/test_server_browser_runtime.py`
- Modify: `deploy/server_browser/autovideosrt-meta-daily-final-sync.timer`
- Modify: `deploy/server_browser/autovideosrt-meta-daily-final-check.service`
- Modify: `deploy/server_browser/autovideosrt-meta-daily-final-check.timer`
- Create: `deploy/server_browser/autovideosrt-ad-order-previous-business-day-sync.service`
- Create: `deploy/server_browser/autovideosrt-ad-order-previous-business-day-sync.timer`
- Create: `deploy/server_browser/autovideosrt-ad-order-previous-week-sync.service`
- Create: `deploy/server_browser/autovideosrt-ad-order-previous-week-sync.timer`

- [ ] **Step 1: Update failing static systemd tests**

In `tests/test_server_browser_runtime.py`, update `test_meta_daily_final_units_use_dxm01_meta_without_shared_lock_and_staggered_timers` assertions:

```python
    assert "--mode run" in sync_service
    assert "--mode run" in check_service
    assert "--mode check" not in check_service
    assert "--include-adsets" in sync_service
    assert "--include-adsets" in check_service
    assert "OnCalendar=*-*-* 16:10:00" in sync_timer
    assert "OnCalendar=*-*-* 19:00:00" in check_timer
```

Append a new test:

```python
def test_ad_order_backfill_units_are_registered_with_expected_schedules():
    daily_service = _read("deploy/server_browser/autovideosrt-ad-order-previous-business-day-sync.service")
    daily_timer = _read("deploy/server_browser/autovideosrt-ad-order-previous-business-day-sync.timer")
    weekly_service = _read("deploy/server_browser/autovideosrt-ad-order-previous-week-sync.service")
    weekly_timer = _read("deploy/server_browser/autovideosrt-ad-order-previous-week-sync.timer")

    assert "tools/ad_order_sync_orchestrator.py --mode previous-business-day" in daily_service
    assert "tools/ad_order_sync_orchestrator.py --mode previous-week --max-scan-pages 500" in weekly_service
    assert "autovideosrt-dxm03-rjc-vnc.service" in daily_service
    assert "autovideosrt-dxm01-meta-vnc.service" in daily_service
    assert "autovideosrt-dxm03-rjc-vnc.service" in weekly_service
    assert "autovideosrt-dxm01-meta-vnc.service" in weekly_service
    assert "OnCalendar=*-*-* 12:00:00" in daily_timer
    assert "OnCalendar=Mon *-*-* 20:30:00" in weekly_timer
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
pytest tests/test_server_browser_runtime.py -q
```

Expected: FAIL on old timer values and missing new unit files.

- [ ] **Step 3: Update Meta daily-final timers**

In `deploy/server_browser/autovideosrt-meta-daily-final-sync.timer`:

```ini
[Unit]
Description=Run AutoVideoSrt Meta final daily ad data sync at 16:10 Beijing time

[Timer]
OnCalendar=*-*-* 16:10:00
Persistent=true
Unit=autovideosrt-meta-daily-final-sync.service

[Install]
WantedBy=timers.target
```

In `deploy/server_browser/autovideosrt-meta-daily-final-check.service`, change description and ExecStart:

```ini
[Unit]
Description=AutoVideoSrt Meta final daily ad data confirmation rerun
After=network-online.target autovideosrt-dxm01-meta-vnc.service
Wants=network-online.target autovideosrt-dxm01-meta-vnc.service

[Service]
Type=oneshot
WorkingDirectory=/opt/autovideosrt
EnvironmentFile=-/opt/autovideosrt/.env
Environment=META_AD_EXPORT_CDP_URL=http://127.0.0.1:9222
Environment=BROWSER_AUTOMATION_APP_ROOT=/opt/autovideosrt
Environment=BROWSER_AUTOMATION_PYTHON_BIN=/opt/autovideosrt/venv/bin/python
Environment=PYTHONUNBUFFERED=1
ExecStartPre=/usr/bin/install -d -o root -g root -m 02775 /opt/autovideosrt/output/meta_daily_final_exports
ExecStart=/opt/autovideosrt/venv/bin/python /opt/autovideosrt/tools/meta_daily_final_sync.py --mode run --include-adsets
User=root
Group=root
StandardOutput=journal
StandardError=journal
TimeoutStartSec=5400
```

In `deploy/server_browser/autovideosrt-meta-daily-final-check.timer`:

```ini
[Unit]
Description=Confirm AutoVideoSrt Meta final daily ad data sync at 19:00 Beijing time

[Timer]
OnCalendar=*-*-* 19:00:00
Persistent=true
Unit=autovideosrt-meta-daily-final-check.service

[Install]
WantedBy=timers.target
```

- [ ] **Step 4: Add new daily and weekly backfill units**

Create `deploy/server_browser/autovideosrt-ad-order-previous-business-day-sync.service`:

```ini
[Unit]
Description=AutoVideoSrt ad/order previous Meta business day sync
After=network-online.target autovideosrt-dxm01-meta-vnc.service autovideosrt-dxm03-rjc-vnc.service
Wants=network-online.target autovideosrt-dxm01-meta-vnc.service autovideosrt-dxm03-rjc-vnc.service

[Service]
Type=oneshot
WorkingDirectory=/opt/autovideosrt
EnvironmentFile=-/opt/autovideosrt/.env
Environment=PYTHONUNBUFFERED=1
Environment=META_AD_EXPORT_CDP_URL=http://127.0.0.1:9222
Environment=DXM_ORDER_BROWSER_CDP_URL=http://127.0.0.1:9225
ExecStartPre=/usr/bin/install -d -o root -g root -m 02775 /opt/autovideosrt/output/meta_daily_final_exports
ExecStart=/opt/autovideosrt/venv/bin/python /opt/autovideosrt/tools/ad_order_sync_orchestrator.py --mode previous-business-day
User=root
Group=root
StandardOutput=journal
StandardError=journal
TimeoutStartSec=7200
```

Create `deploy/server_browser/autovideosrt-ad-order-previous-business-day-sync.timer`:

```ini
[Unit]
Description=Run AutoVideoSrt ad/order previous Meta business day sync at 12:00 Beijing time

[Timer]
OnCalendar=*-*-* 12:00:00
Persistent=true
Unit=autovideosrt-ad-order-previous-business-day-sync.service

[Install]
WantedBy=timers.target
```

Create `deploy/server_browser/autovideosrt-ad-order-previous-week-sync.service`:

```ini
[Unit]
Description=AutoVideoSrt ad/order previous ISO week sync
After=network-online.target autovideosrt-dxm01-meta-vnc.service autovideosrt-dxm03-rjc-vnc.service
Wants=network-online.target autovideosrt-dxm01-meta-vnc.service autovideosrt-dxm03-rjc-vnc.service

[Service]
Type=oneshot
WorkingDirectory=/opt/autovideosrt
EnvironmentFile=-/opt/autovideosrt/.env
Environment=PYTHONUNBUFFERED=1
Environment=META_AD_EXPORT_CDP_URL=http://127.0.0.1:9222
Environment=DXM_ORDER_BROWSER_CDP_URL=http://127.0.0.1:9225
ExecStartPre=/usr/bin/install -d -o root -g root -m 02775 /opt/autovideosrt/output/meta_daily_final_exports
ExecStart=/opt/autovideosrt/venv/bin/python /opt/autovideosrt/tools/ad_order_sync_orchestrator.py --mode previous-week --max-scan-pages 500
User=root
Group=root
StandardOutput=journal
StandardError=journal
TimeoutStartSec=43200
```

Create `deploy/server_browser/autovideosrt-ad-order-previous-week-sync.timer`:

```ini
[Unit]
Description=Run AutoVideoSrt ad/order previous ISO week sync on Monday evening

[Timer]
OnCalendar=Mon *-*-* 20:30:00
Persistent=true
Unit=autovideosrt-ad-order-previous-week-sync.service

[Install]
WantedBy=timers.target
```

- [ ] **Step 5: Run static systemd tests**

Run:

```bash
pytest tests/test_server_browser_runtime.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit Task 6**

```bash
git add deploy/server_browser tests/test_server_browser_runtime.py
git commit -m "feat(sync): update ad order systemd schedules" -m "Docs-anchor: docs/superpowers/specs/2026-06-04-ad-order-sync-schedule-design.md"
```

---

### Task 7: Scheduled Task Registry

**Files:**
- Modify: `tests/test_appcore_scheduled_tasks.py`
- Modify: `appcore/scheduled_tasks.py`

- [ ] **Step 1: Update failing registry tests**

In `tests/test_appcore_scheduled_tasks.py::test_task_definitions_include_server_and_app_timers`, replace the relevant assertions with:

```python
    assert definitions["roi_hourly_sync"]["schedule"] == "每 20 分钟（每小时 :00/:20/:40）"
    assert definitions["dianxiaomi_order_import"]["schedule"] == "每 20 分钟（随 ROI :00/:20/:40 触发）；12:00/weekly 补拉编排也会调用"
    assert definitions["meta_realtime_import"]["schedule"] == "每 20 分钟（随 ROI :00/:20/:40 触发）"
    assert definitions["meta_daily_final"]["schedule"] == "每天 16:10 同步；19:00 二次同步确认"
    assert "16:10" in definitions["meta_daily_final"]["description"]
    assert "19:00" in definitions["meta_daily_final"]["description"]
    assert "--include-adsets" in definitions["meta_daily_final"]["runner"]
    assert definitions["ad_order_previous_business_day_sync"]["schedule"] == "每天 12:00（Meta 业务日口径）"
    assert definitions["ad_order_previous_business_day_sync"]["source_ref"] == "autovideosrt-ad-order-previous-business-day-sync.timer"
    assert definitions["ad_order_previous_business_day_sync"]["log_table"] == "scheduled_task_runs"
    assert definitions["ad_order_previous_week_sync"]["schedule"] == "每周一 20:30（上一 ISO 周 7 个 Meta 业务日）"
    assert definitions["ad_order_previous_week_sync"]["source_ref"] == "autovideosrt-ad-order-previous-week-sync.timer"
    assert definitions["ad_order_previous_week_sync"]["log_table"] == "scheduled_task_runs"
```

- [ ] **Step 2: Run registry test and verify failure**

Run:

```bash
pytest tests/test_appcore_scheduled_tasks.py::test_task_definitions_include_server_and_app_timers -q
```

Expected: FAIL because definitions still show old schedules and missing new task codes.

- [ ] **Step 3: Update `TASK_DEFINITIONS`**

In `appcore/scheduled_tasks.py`, update the existing `roi_hourly_sync` definition fields:

```python
"description": "每 20 分钟同步店小秘近期订单、Meta 日内广告数据，并刷新真实 ROAS 日内快照。Docs-anchor: docs/superpowers/specs/2026-06-04-ad-order-sync-schedule-design.md",
"schedule": "每 20 分钟（每小时 :00/:20/:40）",
```

Update the existing `dianxiaomi_order_import` definition fields:

```python
"description": "ROI 实时同步中的店小秘订单导入子任务；12:00/weekly 补拉编排也复用同一导入入口，且通过 DXM 订单导入锁避免并发操作 DXM03 浏览器。",
"schedule": "每 20 分钟（随 ROI :00/:20/:40 触发）；12:00/weekly 补拉编排也会调用",
```

Update `meta_realtime_import`:

```python
"schedule": "每 20 分钟（随 ROI :00/:20/:40 触发）",
```

Update `meta_daily_final`:

```python
"description": (
    "每天北京时间 16:10 抓取刚收盘的 Meta 广告整日数据（Campaign / Ad Set / Ad），"
    "19:00 对同一目标日做二次同步确认；spec: "
    "docs/superpowers/specs/2026-06-04-ad-order-sync-schedule-design.md；"
    "Ad Set steady sync: docs/superpowers/specs/2026-05-28-meta-daily-final-adset-steady-sync-design.md。"
),
"schedule": "每天 16:10 同步；19:00 二次同步确认",
"source_ref": "autovideosrt-meta-daily-final-sync.timer / autovideosrt-meta-daily-final-check.timer",
"runner": "tools/meta_daily_final_sync.py --mode run --include-adsets",
```

Add definitions near `meta_daily_final`:

```python
"ad_order_previous_business_day_sync": {
    "code": "ad_order_previous_business_day_sync",
    "name": "广告订单上一业务日补拉",
    "description": (
        "每天 12:00 按 Meta 业务日口径补拉上一完整业务日：店小秘订单导入、Meta 日终广告同步、"
        "订单利润重算。Docs-anchor: docs/superpowers/specs/2026-06-04-ad-order-sync-schedule-design.md"
    ),
    "schedule": "每天 12:00（Meta 业务日口径）",
    "source_type": "systemd",
    "source_label": "Linux systemd timer",
    "source_ref": "autovideosrt-ad-order-previous-business-day-sync.timer",
    "runner": "tools/ad_order_sync_orchestrator.py --mode previous-business-day",
    "deployment": "线上待启用",
    "log_table": "scheduled_task_runs",
},
"ad_order_previous_week_sync": {
    "code": "ad_order_previous_week_sync",
    "name": "广告订单上一周补拉",
    "description": (
        "每周一 20:30 补拉上一 ISO 周 7 个 Meta 业务日：店小秘订单导入、Meta 日终广告同步、"
        "订单利润重算。Docs-anchor: docs/superpowers/specs/2026-06-04-ad-order-sync-schedule-design.md"
    ),
    "schedule": "每周一 20:30（上一 ISO 周 7 个 Meta 业务日）",
    "source_type": "systemd",
    "source_label": "Linux systemd timer",
    "source_ref": "autovideosrt-ad-order-previous-week-sync.timer",
    "runner": "tools/ad_order_sync_orchestrator.py --mode previous-week --max-scan-pages 500",
    "deployment": "线上待启用",
    "log_table": "scheduled_task_runs",
},
```

- [ ] **Step 4: Run registry tests**

Run:

```bash
pytest tests/test_appcore_scheduled_tasks.py::test_task_definitions_include_server_and_app_timers -q
```

Expected: PASS.

- [ ] **Step 5: Commit Task 7**

```bash
git add appcore/scheduled_tasks.py tests/test_appcore_scheduled_tasks.py
git commit -m "feat(sync): register ad order sync schedules" -m "Docs-anchor: docs/superpowers/specs/2026-06-04-ad-order-sync-schedule-design.md"
```

---

### Task 8: Final Verification

**Files:**
- No new files unless verification exposes failures.

- [ ] **Step 1: Run focused test suite**

Run:

```bash
pytest tests/test_ad_order_sync_orchestrator.py tests/test_dxm_order_import_lock.py tests/test_roi_hourly_sync_controls.py tests/test_server_browser_runtime.py tests/test_appcore_scheduled_tasks.py -q
```

Expected: PASS.

- [ ] **Step 2: Run related regression tests**

Run:

```bash
pytest tests/test_dianxiaomi_order_import.py tests/test_meta_server_sync_tools.py tests/test_meta_daily_final_sync_guard.py -q
```

Expected: PASS.

- [ ] **Step 3: Syntax-check changed Python files**

Run:

```bash
python -m py_compile tools/ad_order_sync_orchestrator.py tools/roi_hourly_sync.py appcore/dxm_order_import_lock.py appcore/scheduled_tasks.py
```

Expected: exit 0.

- [ ] **Step 4: Inspect final diff**

Run:

```bash
git diff --stat HEAD~7..HEAD
git status --short --branch
```

Expected:

- Diff includes the intended files only.
- Working tree is clean except for any intentionally uncommitted user files.

- [ ] **Step 5: Do not deploy without explicit instruction**

Do not run `systemctl restart`, `systemctl enable`, SSH deploy commands, or production/test service reloads unless the user explicitly says “发测试” or “上线”.
