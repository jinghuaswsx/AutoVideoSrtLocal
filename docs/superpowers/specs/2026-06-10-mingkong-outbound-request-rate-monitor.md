# Mingkong Outbound Request Rate Monitor

Date: 2026-06-10

## Anchors

- `AGENTS.md`: document-driven code, isolated worktree development, targeted pytest, and all APScheduler/background tasks must be registered in `appcore/scheduled_tasks.py`.
- `docs/superpowers/specs/2026-05-08-feishu-bot-alerts-design.md`: scheduled task failures are the existing Feishu and Web admin alert path.
- `docs/superpowers/specs/2026-05-18-mingkong-daily-material-snapshot-top100-design.md`: Mingkong material fetches use `/api/marketing/medias` and local archives should reduce live Mingkong dependency.
- `docs/superpowers/specs/2026-06-09-mingkong-product-library-foundation-design.md`: Mingkong product/SKU workflows should prefer the local product library and only fall back to live DXM02/Mingkong requests for targeted misses.

## Background

On 2026-06-10 operations reported that Mingkong was down and asked for strict control over our Mingkong request frequency. The immediate audit showed the largest outbound Mingkong burst was the scheduled material snapshot, while later failures were low-count live requests timing out against `os.wedev.vip`.

We need a durable local monitor so future incidents do not rely on manual journal parsing.

## Scope

Track server-side HTTP requests from this system to the configured Mingkong/wedev base URL, defaulting to `https://os.wedev.vip`.

In scope:

1. Record every server-side Mingkong outbound request at the HTTP boundary.
2. Store only operational metadata: timestamp, source, method, host/path without query string, status code, duration, response bytes, and error summary.
3. Do not store Cookie, Authorization, request body, response body, or product payloads.
4. Add an APScheduler task that runs every 10 minutes.
5. The task inspects the last 10 minutes and groups requests by natural Beijing minute.
6. If any one-minute bucket is greater than 60 requests, finish the scheduled task run as `failed`.
7. This task must dispatch failure alerts immediately instead of waiting for the global 20-failure noise gate.
8. Successful checks are recorded to `scheduled_task_runs` for audit.

Out of scope:

- Do not block, throttle, or retry Mingkong requests in this change.
- Do not count browser playback requests to local `/medias/api/mk-video` unless the server actually fetches a missing file from Mingkong.
- Do not scan Nginx/systemd logs as the primary source of truth.

## Scheduled Task

Register in `appcore/scheduled_tasks.py`:

- Code: `mingkong_request_rate_monitor`
- Name: `明空外呼频率监控`
- Schedule: every 10 minutes
- Source type: `apscheduler`
- Source ref: `mingkong_request_rate_monitor`
- Runner: `appcore.mingkong_request_monitor.run_scheduled_check`
- Log table: `scheduled_task_runs`
- Alert behavior: `failure_alert_immediate=true`

## Data Model

The service ensures this table before writes and checks:

```sql
CREATE TABLE IF NOT EXISTS mingkong_outbound_request_logs (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  called_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  source VARCHAR(160) NOT NULL,
  method VARCHAR(12) NOT NULL,
  host VARCHAR(255) NOT NULL,
  path VARCHAR(768) NOT NULL,
  status_code INT NULL,
  duration_ms INT UNSIGNED NULL,
  response_bytes BIGINT UNSIGNED NULL,
  error_type VARCHAR(120) NULL,
  error_message VARCHAR(512) NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_mk_outbound_called_at (called_at),
  KEY idx_mk_outbound_host_called (host, called_at),
  KEY idx_mk_outbound_source_called (source, called_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

## Alert Semantics

Threshold:

- `> 60` requests in any one natural minute.

Failure summary includes:

- `threshold_per_minute`
- `window_minutes`
- `max_requests_per_minute`
- `breached_minutes`
- top minute buckets by request count

The alert message itself is the standard scheduled-task failure message. The error text must state the offending minute and request count.

## Verification

Focused tests:

```bash
python3 scripts/pytest_related.py --base origin/master --run
pytest tests/test_mingkong_request_monitor.py tests/test_mingkong_materials_scheduler.py tests/test_appcore_scheduled_tasks.py -q
```

Compile check:

```bash
python3 -m compileall appcore/mingkong_request_monitor.py appcore/mingkong_request_monitor_scheduler.py appcore/scheduled_tasks.py appcore/scheduler.py
```

Full pytest is not required unless scheduler/test infrastructure changes expand beyond this monitor.
