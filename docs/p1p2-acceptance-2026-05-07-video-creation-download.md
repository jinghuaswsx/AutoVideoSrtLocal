# P1/P2 Acceptance Note - Video Creation Result Download

Date: 2026-05-07

Scope:

- P2 route boundary cleanup for generated video result download in `web/routes/video_creation.py`.
- Moved the direct `requests.get` download and local file write into `appcore.video_creation_downloads.download_generated_video_result`.
- Kept the existing route workflow unchanged: generation call, billing log, state update, project status update, and Socket.IO completion event remain in the same order.

Verification:

- RED confirmed first:
  - `tests/test_architecture_boundaries.py::test_video_creation_result_download_lives_outside_route_module` failed while the route still imported `requests as req`.
  - `tests/test_video_creation_downloads.py` failed before `appcore.video_creation_downloads` existed.
- GREEN:
  - `python -m pytest tests\test_video_creation_routes.py tests\test_video_creation_downloads.py -q`:
    `3 passed, 2 warnings`.
  - `python -m pytest tests\test_architecture_boundaries.py -q`:
    `217 passed, 1 warning`.
  - `python -m compileall appcore\video_creation_downloads.py web\routes\video_creation.py tests\test_video_creation_downloads.py tests\test_architecture_boundaries.py -q` passed.

Local MySQL:

- Not used. All verification for this change used static checks or no-db route/helper tests.
