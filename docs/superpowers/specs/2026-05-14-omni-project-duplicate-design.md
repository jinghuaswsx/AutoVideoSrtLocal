# Omni Project Duplicate Design

Date: 2026-05-14
Status: approved

## Anchors

- `AGENTS.md`: document-driven code, isolated worktree, and verification order.
- `web/templates/CLAUDE.md`: mutating front-end requests must send `X-CSRFToken`.
- `web/static/CLAUDE.md`: list/card controls must stay within the Ocean Blue admin style.
- `docs/superpowers/specs/2026-05-07-omni-translate-merge-design.md`: Omni create flow stores the selected `plugin_config` snapshot on the task.
- `docs/superpowers/specs/2026-04-28-tos-backup-storage-design.md`: project source videos are local-primary protected assets, with TOS used as the recovery copy.

## Goal

Add a project copy action to the top-right management menu on `/omni-translate` project cards and list rows, next to the existing delete action.

## Behavior

- The menu shows a normal action named `复制项目` above `删除`.
- Clicking `复制项目` sends `POST /api/omni-translate/<task_id>/duplicate` with the CSRF header.
- The duplicated task belongs to the current user.
- Admins may duplicate any project they can view; normal users may only duplicate their own projects.
- The new task copies the original task's source language, target language, `plugin_config`, subtitle/voice defaults, and source metadata where applicable.
- The new task does not copy generated artifacts, progress, errors, review state, or output files.
- The original source video is copied into a new upload file path for the new task, so deleting either project does not remove the other project's source video.
- If the original local source file is missing, the route first tries the TOS backup recovery path. If the file still cannot be materialized, the route returns `409`.
- After creation, the Omni pipeline starts automatically for the new task and the response returns the new `task_id` plus a detail-page `redirect_url`.

## Implementation

- Add a small route helper in `web/routes/omni_translate.py` for copying the source video into `UPLOAD_DIR/<new_task_id><ext>`.
- Add `POST /api/omni-translate/<task_id>/duplicate` in `web/routes/omni_translate.py`.
- Add `duplicateTask(event, taskId)` in `web/templates/omni_translate_list.html`, using the same response parsing helper as create.
- Add menu buttons in both card and list views.
- Cover the API route in `tests/test_omni_translate_routes.py`.
- Cover the template menu and CSRF/fetch behavior in `tests/test_web_routes_omni_create_modal.py`.

## Verification

Run focused route/template tests:

```bash
pytest tests/test_omni_translate_routes.py tests/test_web_routes_omni_create_modal.py -q
python3 -m compileall web/routes/omni_translate.py
```

No `CHANGELOG` file exists in this repository, so no changelog update is required.
