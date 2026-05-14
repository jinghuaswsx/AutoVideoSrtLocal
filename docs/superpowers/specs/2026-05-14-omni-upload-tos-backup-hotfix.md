# Omni Upload TOS Backup Hotfix

Date: 2026-05-14
Status: hotfix

## Anchors

- `AGENTS.md` hard rule: document-driven code, isolated worktree, and verification order.
- `docs/superpowers/specs/2026-04-28-tos-backup-storage-design.md`: `local_primary` writes local files first and keeps TOS as the disaster-recovery copy.
- `docs/superpowers/specs/2026-05-07-omni-translate-merge-design.md`: Omni create flow stores the selected preset snapshot and then starts the task.

## Incident

Clicking "上传并创建" on `/omni-translate` returned a browser alert:

```text
Unexpected token '<', "<!doctype"... is not valid JSON
```

The production traceback showed `POST /api/omni-translate/start` failed inside
`web.upload_util.save_uploaded_video()` after the local file was written and
the TOS backup layer attempted to upload the local file. TOS returned
`403 AccessDenied` for bucket `autovideosrtlocal`.

The front end then parsed the Flask 500 HTML page with `res.json()`, masking
the real server-side error.

## Root Cause

`tos_backup` has an explicit backup bucket (`autovideosrtlocal`) but its AK/SK
fields are allowed to be empty. Empty backup credentials are meant to inherit
`tos_main`. The runtime sync code instead inherited the currently active TOS
channel. When the active channel was `tos_wj`, the backup client used WJ
credentials against the CJH backup bucket and received `403 AccessDenied`.

## Fix

- `local_primary` upload writes must not fail user-facing task creation when
  the post-commit TOS backup sync fails. They now keep the local file and log a
  warning.
- `tos_backup` empty AK/SK fallback now prefers `tos_main` even when the active
  TOS channel is `tos_wj`.
- The Omni create-page JavaScript now handles non-JSON error responses without
  surfacing JSON parser errors.

## Verification

```bash
pytest tests/test_infra_credentials.py -q
pytest tests/test_upload_util_tos_backup.py tests/test_web_routes_omni_create_modal.py tests/test_omni_translate_create_with_plugin_config.py -q
python3 -m compileall web/upload_util.py appcore/infra_credentials.py
```
