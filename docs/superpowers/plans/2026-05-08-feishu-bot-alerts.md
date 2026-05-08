# Feishu Bot Alerts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Send Feishu app bot notifications when `scheduled_task_runs` records a failed run, and provide a CLI test notification path.

**Architecture:** Add a small `appcore.feishu_alerts` module that owns config loading, token retrieval, IM message sending, formatting, and safe best-effort dispatch. `appcore.scheduled_tasks.finish_run()` calls it only on `status="failed"`. Settings UI persists bot credentials in `system_settings` with the existing secret masking pattern.

**Tech Stack:** Python 3.14, Flask/Jinja, `requests`, pytest, existing `system_settings` and `scheduled_task_runs`.

---

## File Map

- Create `appcore/feishu_alerts.py`: configuration, Feishu API calls, message formatting, safe failure dispatch.
- Modify `appcore/scheduled_tasks.py`: load the updated run row and dispatch Feishu alert after failed runs are persisted.
- Modify `web/routes/settings.py`: add `feishu_alerts` tab handling, settings view data, and form persistence.
- Modify `web/templates/settings.html`: add tab link and Feishu alert configuration form without secret echo.
- Create `tools/send_feishu_test_alert.py`: CLI for test notifications.
- Create `tests/test_feishu_alerts.py`: unit tests for config, API calls, formatting, and safe dispatch.
- Modify `tests/test_appcore_scheduled_tasks.py`: prove failed `finish_run()` triggers Feishu dispatch and dispatch errors do not block DB updates.
- Modify `tests/test_settings_routes_new.py`: prove settings tab renders and saves without leaking secrets.
- Modify `docs/superpowers/specs/2026-05-08-feishu-bot-alerts-design.md`: update implementation status after tests pass.

## Task 1: Feishu Alert Core

**Files:**
- Create: `appcore/feishu_alerts.py`
- Test: `tests/test_feishu_alerts.py`

- [ ] **Step 1: Write failing tests for config, send flow, and failure formatting**

```python
def test_send_text_message_fetches_token_and_posts_chat_message(monkeypatch):
    from appcore import feishu_alerts

    settings = {
        "feishu_alerts.enabled": "1",
        "feishu_alerts.app_id": "cli_test",
        "feishu_alerts.app_secret": "secret_test",
        "feishu_alerts.chat_id": "oc_test",
    }
    monkeypatch.setattr(feishu_alerts.settings_store, "get_setting", lambda key: settings.get(key))
    calls = []

    class FakeResponse:
        def __init__(self, payload):
            self.status_code = 200
            self._payload = payload
            self.text = "ok"
        def json(self):
            return self._payload

    def fake_post(url, *, json=None, headers=None, params=None, timeout=None):
        calls.append({"url": url, "json": json, "headers": headers, "params": params, "timeout": timeout})
        if url.endswith("/tenant_access_token/internal"):
            return FakeResponse({"code": 0, "tenant_access_token": "tenant-token"})
        return FakeResponse({"code": 0, "data": {"message_id": "om_test"}})

    monkeypatch.setattr(feishu_alerts.requests, "post", fake_post)

    result = feishu_alerts.send_text_message("hello")

    assert result == {"ok": True, "message_id": "om_test"}
    assert calls[0]["json"] == {"app_id": "cli_test", "app_secret": "secret_test"}
    assert calls[1]["params"] == {"receive_id_type": "chat_id"}
    assert calls[1]["headers"]["Authorization"] == "Bearer tenant-token"
    assert calls[1]["json"]["receive_id"] == "oc_test"
    assert calls[1]["json"]["msg_type"] == "text"
```

- [ ] **Step 2: Run the new test and verify RED**

Run: `pytest tests/test_feishu_alerts.py::test_send_text_message_fetches_token_and_posts_chat_message -q`

Expected: FAIL because `appcore.feishu_alerts` does not exist.

- [ ] **Step 3: Implement `appcore/feishu_alerts.py`**

Create the module with:

```python
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import requests

from appcore import settings as settings_store

log = logging.getLogger(__name__)

TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
MESSAGE_URL = "https://open.feishu.cn/open-apis/im/v1/messages"
SETTING_ENABLED = "feishu_alerts.enabled"
SETTING_APP_ID = "feishu_alerts.app_id"
SETTING_APP_SECRET = "feishu_alerts.app_secret"
SETTING_CHAT_ID = "feishu_alerts.chat_id"
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


def load_config() -> FeishuAlertConfig:
    return FeishuAlertConfig(
        enabled=_setting(SETTING_ENABLED) == "1",
        app_id=_setting(SETTING_APP_ID),
        app_secret=_setting(SETTING_APP_SECRET),
        chat_id=_setting(SETTING_CHAT_ID),
    )
```

Add `config_view()`, `fetch_tenant_access_token()`, `send_text_message()`, `format_scheduled_task_failure()`, `send_scheduled_task_failure()`, and `send_test_alert()` in the same module.

- [ ] **Step 4: Run core tests and make them pass**

Run: `pytest tests/test_feishu_alerts.py -q`

Expected: PASS.

## Task 2: Scheduled Task Failure Hook

**Files:**
- Modify: `appcore/scheduled_tasks.py`
- Test: `tests/test_appcore_scheduled_tasks.py`

- [ ] **Step 1: Write failing tests for failed run dispatch**

Add tests that monkeypatch `execute`, `query`, and `appcore.feishu_alerts.send_scheduled_task_failure`:

```python
def test_finish_run_dispatches_feishu_alert_for_failed_run(monkeypatch):
    from appcore import scheduled_tasks
    from appcore import feishu_alerts

    sent = []
    monkeypatch.setattr(scheduled_tasks, "execute", lambda *a, **k: 1)
    monkeypatch.setattr(
        scheduled_tasks,
        "query",
        lambda sql, params=(): [{
            "id": params[0],
            "task_code": "shopifyid",
            "task_name": "Shopify ID 获取",
            "status": "failed",
            "started_at": "2026-05-08 10:00:00",
            "finished_at": "2026-05-08 10:00:02",
            "duration_seconds": 2,
            "summary_json": '{"updated": 0}',
            "error_message": "boom",
            "output_file": None,
        }],
    )
    monkeypatch.setattr(feishu_alerts, "send_scheduled_task_failure", lambda row: sent.append(row))

    scheduled_tasks.finish_run(42, status="failed", error_message="boom")

    assert sent and sent[0]["id"] == 42
    assert sent[0]["summary"] == {"updated": 0}
```

- [ ] **Step 2: Run test and verify RED**

Run: `pytest tests/test_appcore_scheduled_tasks.py::test_finish_run_dispatches_feishu_alert_for_failed_run -q`

Expected: FAIL because `finish_run()` does not dispatch Feishu alerts.

- [ ] **Step 3: Implement run lookup and safe dispatch**

Add `_scheduled_task_run_by_id(run_id)` near row normalization helpers and call `_dispatch_failure_alert(run_id)` after the update in `finish_run()`.

- [ ] **Step 4: Run scheduled task tests**

Run: `pytest tests/test_appcore_scheduled_tasks.py -q`

Expected: PASS.

## Task 3: Settings UI

**Files:**
- Modify: `web/routes/settings.py`
- Modify: `web/templates/settings.html`
- Test: `tests/test_settings_routes_new.py`

- [ ] **Step 1: Write failing route/template tests**

Add tests that assert:

- `/settings?tab=feishu_alerts` renders “飞书告警”.
- configured `app_secret` is not present in HTML.
- POST saves `enabled`, `app_id`, `chat_id`, and preserves blank secret unless `clear=feishu_alerts.app_secret`.

- [ ] **Step 2: Run route tests and verify RED**

Run: `pytest tests/test_settings_routes_new.py::test_settings_feishu_alerts_tab_masks_secret -q`

Expected: FAIL because the tab does not exist.

- [ ] **Step 3: Implement route handling**

In `web/routes/settings.py`:

- Add `feishu_alerts` to `allowed_tabs`.
- Add POST branch `elif tab == "feishu_alerts": _handle_feishu_alerts_post()`.
- Add `feishu_alerts_view=feishu_alerts.config_view()` to `render_template`.
- Add `_handle_feishu_alerts_post()` using `settings_store.set_setting()`.

- [ ] **Step 4: Implement template form**

In `web/templates/settings.html`:

- Add a settings tab link labelled `飞书告警`.
- Add a form rendered when `active_tab == "feishu_alerts"`.
- Use password input for `feishu_alerts_app_secret` with empty value and masked status.

- [ ] **Step 5: Run settings tests**

Run: `pytest tests/test_settings_routes_new.py -q`

Expected: PASS.

## Task 4: Test Notification CLI

**Files:**
- Create: `tools/send_feishu_test_alert.py`
- Test: `tests/test_feishu_alerts.py`

- [ ] **Step 1: Write failing CLI test**

Test `main(["--message", "hello"])` with monkeypatched `feishu_alerts.send_test_alert` and assert JSON output with `ok: true`.

- [ ] **Step 2: Run test and verify RED**

Run: `pytest tests/test_feishu_alerts.py::test_send_feishu_test_alert_cli_outputs_json -q`

Expected: FAIL because the CLI module does not exist.

- [ ] **Step 3: Implement CLI**

Create `tools/send_feishu_test_alert.py` with `argparse`, JSON stdout, and non-zero exit on `FeishuAlertError`.

- [ ] **Step 4: Run CLI tests**

Run: `pytest tests/test_feishu_alerts.py -q`

Expected: PASS.

## Task 5: Verification And Real Test Notification

**Files:**
- Modify: `docs/superpowers/specs/2026-05-08-feishu-bot-alerts-design.md`

- [ ] **Step 1: Run focused regression**

Run:

```bash
pytest tests/test_feishu_alerts.py tests/test_appcore_scheduled_tasks.py tests/test_settings_routes_new.py tests/test_scheduled_tasks_ui.py -q
python3 -m compileall appcore/feishu_alerts.py appcore/scheduled_tasks.py web/routes/settings.py tools/send_feishu_test_alert.py
git diff --check
```

Expected: all pass.

- [ ] **Step 2: Configure test values without printing secrets**

Use a stdin-based one-off Python command or environment already present on the server. Do not echo App Secret in terminal output, shell history, docs, or commit messages.

- [ ] **Step 3: Send real test notification**

Run: `python3 -m tools.send_feishu_test_alert --message "AutoVideoSrt 飞书告警测试：scheduled_task_runs 失败通知已接入。"`

Expected: command exits 0 and prints `{"ok": true, "message_id": "..."}`.

- [ ] **Step 4: Update spec implementation status**

Append a short “实现记录” section to `docs/superpowers/specs/2026-05-08-feishu-bot-alerts-design.md` listing tests and real notification result without any credentials.

- [ ] **Step 5: Commit**

Commit with:

```bash
git add appcore/feishu_alerts.py appcore/scheduled_tasks.py web/routes/settings.py web/templates/settings.html tools/send_feishu_test_alert.py tests/test_feishu_alerts.py tests/test_appcore_scheduled_tasks.py tests/test_settings_routes_new.py docs/superpowers/specs/2026-05-08-feishu-bot-alerts-design.md docs/superpowers/plans/2026-05-08-feishu-bot-alerts.md
git commit -m "feat(alerts): send feishu notifications for scheduled failures" -m "Docs-anchor: docs/superpowers/specs/2026-05-08-feishu-bot-alerts-design.md#飞书应用机器人告警设计"
```

## Self-Review

- Spec coverage: the plan covers Feishu app bot config, token retrieval, IM send, failed scheduled task trigger, safe failure handling, settings UI masking, and CLI test notification.
- Placeholder scan: no unfinished placeholder markers are present.
- Scope check: long-connection event receiving, multi-group routing, log scanning, and alert aggregation remain out of scope as specified.
