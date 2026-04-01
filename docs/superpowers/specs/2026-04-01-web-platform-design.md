# AutoVideoSrt Web Platform Design

Date: 2026-04-01  
Status: Approved

## Overview

Transform AutoVideoSrt from a single-user local tool into a multi-user web platform with authentication, persistent project records, TOS file storage, per-user API key configuration, scheduled cleanup, and LLM usage tracking.

Deployment target: existing server at 14.103.220.208, port 8888, MySQL database `auto_video`.

---

## Database Schema

### `users`
```sql
CREATE TABLE users (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    username    VARCHAR(64) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    role        ENUM('admin', 'user') NOT NULL DEFAULT 'user',
    is_active   TINYINT(1) NOT NULL DEFAULT 1,
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

### `api_keys`
```sql
CREATE TABLE api_keys (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    user_id     INT NOT NULL,
    service     VARCHAR(32) NOT NULL,  -- doubao_asr | elevenlabs | openrouter
    key_value   VARCHAR(512) NOT NULL,
    extra_config JSON,                 -- app_id, cluster, voice_id, etc.
    updated_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_user_service (user_id, service),
    FOREIGN KEY (user_id) REFERENCES users(id)
);
```

### `projects`
```sql
CREATE TABLE projects (
    id              VARCHAR(32) PRIMARY KEY,   -- task_id (hex)
    user_id         INT NOT NULL,
    original_filename VARCHAR(255),
    thumbnail_path  VARCHAR(512),              -- local path to first-frame JPEG
    status          VARCHAR(32) NOT NULL DEFAULT 'uploaded',
    task_dir        VARCHAR(512),
    state_json      LONGTEXT,                  -- full task_state dict as JSON
    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at      DATETIME NOT NULL,         -- created_at + 24h
    deleted_at      DATETIME,
    FOREIGN KEY (user_id) REFERENCES users(id)
);
```

### `usage_logs`
```sql
CREATE TABLE usage_logs (
    id                    BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id               INT NOT NULL,
    project_id            VARCHAR(32),
    service               VARCHAR(32) NOT NULL,   -- doubao_asr | elevenlabs | openrouter
    model_name            VARCHAR(128),
    called_at             DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    success               TINYINT(1) NOT NULL DEFAULT 1,
    input_tokens          INT,
    output_tokens         INT,
    audio_duration_seconds FLOAT,                 -- ASR parsed duration / ElevenLabs generated duration
    extra_data            JSON,
    FOREIGN KEY (user_id) REFERENCES users(id)
);
```

---

## Architecture

```
browser
  │ HTTP / SocketIO
  ▼
web/ (Flask)
  ├── auth routes     /login /logout
  ├── project routes  / /projects/<id> /api/tasks/*
  ├── settings routes /settings
  ├── admin routes    /admin/users /admin/usage
  └── download routes /api/download/<task_id>/<artifact>

appcore/
  ├── db.py           MySQL connection pool (pymysql + DBUtils)
  ├── task_state.py   read/write memory + DB (state_json), unchanged interface
  ├── users.py        user CRUD
  ├── api_keys.py     API key read/write per user
  ├── usage_log.py    write usage records; called from pipeline steps
  └── cleanup.py      scan expired projects, delete files + TOS objects

pipeline/             unchanged — pure logic, no web deps
scheduler.py          APScheduler instance, hourly cleanup job
main.py               start Flask + scheduler
```

---

## Authentication & Authorization

- **Library:** Flask-Login for session management, bcrypt for password hashing
- **Decorators:** `@login_required` (Flask-Login), `@admin_required` (custom)
- **Session:** server-side session via Flask's signed cookie
- Admin-only routes: `/admin/*`
- All other routes except `/login`: `@login_required`

---

## Page Structure

| URL | Access | Description |
|-----|--------|-------------|
| `/login` | public | Login form |
| `/` | user | Project list — card grid with thumbnail, filename, status, created time |
| `/projects/<task_id>` | user (owner only) | Read-only detail: all step artifacts, download links |
| `/settings` | user | API Key config: doubao_asr, elevenlabs, openrouter |
| `/admin/users` | admin | Create/disable users, set roles |
| `/admin/usage` | admin | Usage stats by user/day/service |

---

## Project State Persistence

`appcore/task_state.py` currently uses an in-process dict. Changes:

1. `create()` — insert row into `projects`, write full state to `state_json`
2. `update()` / `set_step()` / `set_artifact()` etc. — update `state_json` + indexed columns (`status`, `thumbnail_path`) in DB
3. `get()` — read from memory if present, fall back to DB (supports page reload / server restart)
4. All existing callers unchanged — same function signatures

---

## API Key Resolution

When pipeline steps run, they resolve API keys in this order:
1. Current user's key from `api_keys` table
2. Fall back to system `.env` key

`appcore/api_keys.py` exposes `get_key(user_id, service)` used by pipeline wrappers.

Pipeline runner receives `user_id` at `start()` time and passes it through to steps that need API keys.

---

## TOS Upload

After pipeline completes (`EVT_PIPELINE_DONE`):
- Upload `soft_video` (normal + hook_cta), `hard_video`, `srt` files
- Object key pattern: `{user_id}/{task_id}/{filename}`
- Bucket: `auto-video-srt`, reuse credentials from `pipeline/storage.py` env vars
- Store TOS object keys in `state_json` under `tos_uploads`

Download flow:
- `/api/download/<task_id>/<artifact>?variant=normal`
- If project not expired: generate presigned URL (1h), redirect
- If project expired: return 410 Gone

---

## Thumbnail Generation

On project creation, extract first frame of uploaded video using ffmpeg:
```
ffmpeg -i video.mp4 -vframes 1 -f image2 thumbnail.jpg
```
Store path in `projects.thumbnail_path`. Used in project list cards.

---

## Scheduled Cleanup (APScheduler)

- Runs every hour
- Query: `SELECT id, task_dir FROM projects WHERE expires_at < NOW() AND deleted_at IS NULL`
- For each: delete `task_dir` recursively, delete TOS objects under `{user_id}/{task_id}/`
- Update: `SET deleted_at = NOW(), status = 'expired'`
- Log result

---

## LLM Usage Logging

Each pipeline step that calls an external service writes to `usage_logs` after the call:

| Step | Service | Fields populated |
|------|---------|-----------------|
| ASR (doubao) | `doubao_asr` | `audio_duration_seconds` (input audio length) |
| Translate/TTS script | `openrouter` | `model_name`, `input_tokens`, `output_tokens` |
| TTS (ElevenLabs) | `elevenlabs` | `audio_duration_seconds` (generated audio length) |

`appcore/usage_log.py` exposes `record(user_id, project_id, service, **kwargs)` — fire-and-forget, never raises.

Admin usage dashboard shows:
- Per-user per-day table: calls, tokens, audio seconds
- Filter by service and date range

---

## Deployment

- Server: 14.103.220.208, port 8888
- Run as: `gunicorn -w 1 -k eventlet main:app --bind 0.0.0.0:8888`
  (single worker required for SocketIO in-process state; can move to Redis later)
- MySQL: `auto_video` database, user `root`, password `wylf1109`
- `.env` on server holds system API keys + DB credentials
- Systemd service for auto-restart

---

## Dependencies to Add

```
Flask-Login>=0.6.3
bcrypt>=4.1.0
pymysql>=1.1.0
DBUtils>=3.0.3
APScheduler>=3.10.0
```
