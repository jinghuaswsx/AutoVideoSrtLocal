# P1/P2 Acceptance Note - TTS Speedup Evaluation Route

Date: 2026-05-07

Scope:

- P2-14 route boundary cleanup for `web/routes/tts_speedup_eval.py`.
- Moved admin list query filtering and pagination into `appcore.tts_speedup_eval.list_evaluations`.
- Moved admin summary metrics and top-flag aggregation into `appcore.tts_speedup_eval.summarize_evaluations`.
- Kept the route responsible for login/admin gating, template or fallback response rendering, retry dispatch, and CSV response formatting only.

Verification:

- RED was confirmed first against the missing appcore query functions and remaining route-level DB import.
- GREEN selected tests: `5 passed, 2 warnings`.
- Local no-db focused regression:
  `tests/test_admin_tts_speedup_eval_routes.py tests/test_tts_speedup_eval_service.py`
  selected no-db tests from `tests/test_tts_speedup_eval.py`
  and `tests/test_architecture_boundaries.py`: `9 passed, 2 warnings`.
- `python -m compileall web appcore tests -q` passed.
- `git diff --check` passed.

Local MySQL:

- Not used. This verification intentionally avoided Windows local MySQL per project rule.
