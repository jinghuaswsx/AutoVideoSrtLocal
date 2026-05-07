# P1/P2 Acceptance Note - Admin Settings Route

Date: 2026-05-07

Scope:

- P2 route boundary cleanup for `web/routes/admin.py` settings POST.
- Moved retention override deletion into `appcore.settings.delete_setting`.
- Kept the route responsible for form parsing, validation, flash/redirect flow, retention adjustment decisions, and template rendering only.
- Preserved the existing `system_settings` delete SQL and return semantics.

Verification:

- RED was confirmed first against the missing `delete_setting` helper, missing route patch point, and remaining route-level DB import.
- GREEN focused tests:
  `tests/test_settings.py::test_delete_setting_deletes_key`,
  `tests/test_admin_image_translate_routes.py::test_admin_settings_default_change_skips_per_type_adjust_for_default_types`,
  and `tests/test_architecture_boundaries.py::test_admin_settings_delete_setting_lives_in_appcore_settings`:
  `3 passed, 2 warnings`.
- Combined settings/admin/architecture no-db regression:
  `tests/test_settings.py`, `tests/test_admin_image_translate_routes.py`, and `tests/test_architecture_boundaries.py`:
  `208 passed, 2 warnings`.
- `python -m compileall web appcore tests -q` passed.
- `git diff --check` passed.

Local MySQL:

- Not used. All local tests for this change use monkeypatched no-db paths.
