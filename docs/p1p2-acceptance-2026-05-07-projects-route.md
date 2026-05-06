# P1/P2 Acceptance Note - Projects Route

Date: 2026-05-07

Scope:

- P2-14 route boundary cleanup for `web/routes/projects.py`.
- Moved translation project list reads into `appcore.project_state.list_translation_projects`.
- Moved AV-sync project list reads and optional language filtering into `appcore.project_state.list_av_sync_projects`.
- Moved project detail row reads into `appcore.project_state.get_project_detail_row`.
- Moved legacy TOS download status reads into `appcore.project_state.get_project_download_status_row`.
- Kept the route responsible for login gating, recovery calls, request/query parsing, state JSON parsing, redirects, abort mapping, and template rendering only.

Verification:

- RED was confirmed first against the missing appcore DAO functions, missing route `project_store` patch points, and remaining route-level DB import.
- GREEN selected tests: `8 passed, 2 warnings`.
- Local no-db focused regression:
  `tests/test_project_state.py`, selected projects route tests from `tests/test_web_routes.py`,
  and `tests/test_architecture_boundaries.py::test_projects_route_db_access_lives_in_appcore_project_state`:
  `25 passed, 2 warnings`.
- `python -m compileall web appcore tests -q` passed.
- `git diff --check` passed.

Local MySQL:

- Not used. One legacy route test was tightened to stub recovery before detail rendering, preventing accidental Windows local MySQL access.
