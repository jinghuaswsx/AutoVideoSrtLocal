# P1/P2 Acceptance Note - Translation Quality Route

Date: 2026-05-07

Scope:

- P2-14 route boundary cleanup for `web/routes/translation_quality.py`.
- Moved `projects` lookup for quality-assessment access checks into `appcore.quality_assessment.get_project_for_assessment`.
- Moved `translation_quality_assessments` list query into `appcore.quality_assessment.list_assessment_rows`.
- Kept the route responsible for login/admin/user ownership checks, task-state lookup, and HTTP response wrapping only.

Verification:

- RED was confirmed first against the missing appcore functions and remaining route-level DB import.
- GREEN selected tests: `6 passed, 2 warnings`.
- Local no-db focused regression:
  `tests/test_translation_quality_routes.py tests/test_translation_quality_response_service.py`
  selected no-db tests from `tests/test_quality_assessment_service.py`
  and `tests/test_architecture_boundaries.py`: `196 passed, 2 warnings`.

Local MySQL:

- Not used. This verification intentionally avoided Windows local MySQL per project rule.
