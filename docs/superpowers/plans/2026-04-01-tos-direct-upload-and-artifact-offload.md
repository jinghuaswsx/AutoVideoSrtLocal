# TOS Direct Upload And Artifact Offload Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move source video upload and final artifact download off the app server by using the project-dedicated `auto-video-srt` TOS bucket, while keeping a safe public-endpoint fallback until TOS private connectivity is confirmed.

**Architecture:** Split TOS usage into two paths. Phase A uses browser direct upload to TOS plus server-side signed downloads for final artifacts, which immediately removes the 5 Mbps server bandwidth bottleneck. Phase B optionally switches server-to-TOS traffic to the `ivolces` private path after infrastructure validation; until then, the application keeps a public-endpoint fallback so processing remains functional.

**Tech Stack:** Flask, Socket.IO, existing `tos` Python SDK, Browser-side TOS SDK or pre-signed upload flow, MySQL-backed task state, existing task workbench UI.

---

## Current Verified State

- The production server is a VolcEngine ECS instance in `cn-shanghai`, inside a VPC/subnet.
- The app already uses TOS for ASR temporary audio and already uploads some final artifacts (`soft_video`, `hard_video`, `srt`) after pipeline completion.
- The app does **not** yet upload `capcut_archive` to TOS.
- The current upload page still posts the entire source video to `/api/tasks`, so browser upload traffic fully traverses the app server.
- The production runtime currently points at bucket `ad-kaogujia-video`; product direction is to move this workflow to the project-specific bucket `auto-video-srt`.
- Public TOS access is healthy from the server:
  - Python SDK `list_objects_type2()` succeeds against the current public endpoint.
  - `curl -I https://tos-cn-shanghai.volces.com` succeeds.
- TOS bucket CORS is not configured right now:
  - `GetBucketCORS` returns `NoSuchCORSConfiguration`.
- The server previously routed `ivolces` through `verge-mihomo` fake-IP/TUN; a server-side mitigation was applied:
  - Added `*.ivolces.com` to `fake-ip-filter`.
  - Added `DOMAIN-SUFFIX,ivolces.com,DIRECT`.
  - Restarted `verge-mihomo`.
- After bypassing Mihomo fake-IP, DNS now behaves differently:
  - Bucket private hostnames such as `auto-video-srt.tos-cn-shanghai.ivolces.com` resolve from VPC DNS.
  - Generic `tos-cn-shanghai.ivolces.com` does not resolve cleanly from current DNS servers.
  - Resolved private IPs in `198.18.0.x` still time out on `443`, so private connectivity is **not** yet usable by the app.
- Because public TOS is working and private TOS is not yet confirmed, implementation must not hard-switch the whole app to `ivolces`.

## Locked Product Decisions

- Use `auto-video-srt` as the dedicated bucket for this project.
- Final generated videos, subtitle files, and CapCut archives should be uploaded to TOS and downloaded by users from TOS signed URLs.
- Source video upload should become browser direct upload to TOS.
- Server-side processing should later prefer TOS private access if cloud-side connectivity is proven; until then, it must fall back to public access.
- This task is deferred behind other priorities, so the main requirement for now is a clear resumable implementation document.

## Non-Goals

- Do not implement CDN acceleration in this task.
- Do not redesign the task workbench beyond the upload/download flow changes required for TOS.
- Do not remove the existing local-file pipeline until the TOS-backed path is fully verified.
- Do not assume private `ivolces` access is available just because the server is in the same region.

## Required External Preconditions

- The `auto-video-srt` bucket must exist in `cn-shanghai`.
- The bucket must have browser upload CORS configured for the production site origin.
- An STS flow must be available for browser uploads, or a deliberately chosen pre-signed PUT fallback must be approved.
- Cloud-side validation is still needed for private connectivity to `*.tos-cn-shanghai.ivolces.com:443`.

## File Map

**New files:**
- `appcore/tos_clients.py`
  - Centralizes public/private TOS client creation, endpoint selection, signed URL generation, and fallback behavior.
- `web/routes/tos_upload.py`
  - Provides browser direct-upload bootstrap and completion endpoints.
- `tests/test_tos_clients.py`
  - Covers endpoint selection, fallback behavior, and signed URL generation.
- `tests/test_tos_upload_routes.py`
  - Covers upload bootstrap, completion, and invalid-object handling.

**Modified files:**
- `config.py`
  - Add separate public/private endpoint settings, bucket switch to `auto-video-srt`, and STS-related settings.
- `pipeline/storage.py`
  - Refactor existing TOS helpers to use centralized clients instead of one global endpoint.
- `appcore/runtime.py`
  - Upload all final artifacts, including CapCut archives, to TOS and persist artifact metadata.
- `appcore/task_state.py`
  - Persist `source_tos_key`, `source_object_info`, and richer `tos_uploads`.
- `web/routes/task.py`
  - Stop accepting large multipart source uploads directly; create tasks from completed TOS objects instead.
- `web/routes/projects.py`
  - Prefer signed TOS download links for all final artifacts.
- `web/templates/_task_workbench_scripts.html`
  - Replace current XHR upload with browser direct upload flow and completion callback.
- `web/templates/_task_workbench.html`
  - Adjust upload copy and progress states for TOS direct upload.
- `tests/test_pipeline_runner.py`
  - Cover CapCut artifact upload and task-state persistence.
- `tests/test_web_routes.py`
  - Cover task creation from TOS object, final download link behavior, and upload-page boot logic.
- `server.md`
  - Record the TOS operational requirements and the current `ivolces` validation caveat once implementation is complete.

## Phase Ordering

- Phase A: Public TOS path only
  - Browser direct upload to TOS
  - Final artifact download from TOS
  - Server processing still uses public TOS object access where necessary
- Phase B: Private TOS optimization
  - Only after cloud networking confirms `ivolces` connectivity
  - Switch server-side upload/download path selection to prefer private bucket hostnames

## Task 1: Split Public And Private TOS Configuration

**Files:**
- Modify: `config.py`
- Create: `appcore/tos_clients.py`
- Test: `tests/test_tos_clients.py`

- [ ] Add explicit config keys in `config.py`:
  - `TOS_BUCKET=auto-video-srt`
  - `TOS_PUBLIC_ENDPOINT=tos-cn-shanghai.volces.com`
  - `TOS_PRIVATE_ENDPOINT=tos-cn-shanghai.ivolces.com`
  - `TOS_USE_PRIVATE_ENDPOINT=false`
  - `TOS_BROWSER_UPLOAD_PREFIX=uploads/`
  - `TOS_FINAL_ARTIFACT_PREFIX=artifacts/`
  - `TOS_SIGNED_URL_EXPIRES=3600`

- [ ] Create `appcore/tos_clients.py` with these responsibilities:
  - Build a public TOS client.
  - Build a private TOS client.
  - Generate signed GET URLs using the public endpoint only.
  - Choose server-side upload/download endpoint with fallback:
    - Prefer private bucket hostname only when `TOS_USE_PRIVATE_ENDPOINT=true` and a quick health check passes.
    - Otherwise use the public endpoint.

- [ ] Write focused tests in `tests/test_tos_clients.py` for:
  - public signed URLs always use public endpoint
  - private preference can be disabled by config
  - private health-check failure falls back to public

- [ ] Run:
  - `pytest tests/test_tos_clients.py -q`

## Task 2: Upload All Final Artifacts To TOS

**Files:**
- Modify: `appcore/runtime.py`
- Modify: `appcore/task_state.py`
- Modify: `pipeline/storage.py`
- Test: `tests/test_pipeline_runner.py`

- [ ] Refactor `_upload_artifacts_to_tos()` to use `appcore.tos_clients`.
- [ ] Extend artifact upload coverage to include:
  - `soft_video`
  - `hard_video`
  - `srt`
  - `capcut_archive`
- [ ] Persist richer artifact metadata in task state:
  - `tos_key`
  - `artifact_kind`
  - `variant`
  - `file_size`
  - `uploaded_at`
- [ ] Keep local output files for existing preview and fallback behavior.
- [ ] Add/extend pipeline tests so CapCut archives are uploaded and persisted the same way as final videos.

- [ ] Run:
  - `pytest tests/test_pipeline_runner.py -q`

## Task 3: Switch Final Downloads To TOS Signed URLs

**Files:**
- Modify: `web/routes/projects.py`
- Modify: `web/routes/task.py`
- Modify: `web/templates/_task_workbench_scripts.html`
- Test: `tests/test_web_routes.py`

- [ ] Standardize a single helper for resolving final artifact downloads from task state.
- [ ] For final artifacts, prefer TOS signed URLs over local `send_file`.
- [ ] Keep local-file fallback only when a TOS upload record is missing.
- [ ] Ensure CapCut download buttons use the same TOS-backed path as videos and subtitles.
- [ ] Update frontend rendering so download buttons do not care whether the backing file is local or TOS-hosted.

- [ ] Run:
  - `pytest tests/test_web_routes.py -k "download or capcut or tos" -q`

## Task 4: Add Browser Direct Upload Bootstrap

**Files:**
- Create: `web/routes/tos_upload.py`
- Modify: `web/routes/task.py`
- Modify: `web/templates/_task_workbench_scripts.html`
- Modify: `web/templates/_task_workbench.html`
- Test: `tests/test_tos_upload_routes.py`
- Test: `tests/test_web_routes.py`

- [ ] Add a bootstrap endpoint that returns upload metadata for one source file:
  - object key under `uploads/<user_id>/<uuid>/<original_name>`
  - bucket name
  - region
  - public browser endpoint
  - STS credentials or pre-signed upload info
  - maximum object age / cleanup hint

- [ ] Add a completion endpoint that accepts:
  - `object_key`
  - `original_filename`
  - `file_size`
  - optional media metadata produced client-side

- [ ] In the completion endpoint:
  - verify the object exists in TOS
  - create the task record
  - persist `source_tos_key`
  - stop requiring the browser to upload the source file to `/api/tasks`

- [ ] Replace the current upload XHR in `_task_workbench_scripts.html` with:
  - bootstrap request
  - browser upload to TOS
  - completion callback to create task
  - existing redirect to project detail page

- [ ] Keep upload progress UI semantics intact so the page still shows percentage and error states.

- [ ] Run:
  - `pytest tests/test_tos_upload_routes.py tests/test_web_routes.py -k "upload" -q`

## Task 5: Let The Server Process From TOS Source Objects

**Files:**
- Modify: `web/routes/task.py`
- Modify: `appcore/runtime.py`
- Modify: `appcore/task_state.py`
- Test: `tests/test_pipeline_runner.py`
- Test: `tests/test_web_routes.py`

- [ ] When a task is created from a TOS object, store both:
  - the TOS source key
  - the local working path that will be materialized before processing

- [ ] Before processing starts, materialize the source video from TOS into the task workspace if the local file is absent.
- [ ] Use `appcore.tos_clients` so the materialization path can prefer private access later without changing pipeline logic.
- [ ] Preserve compatibility with any existing tasks that were uploaded through the old local-file route.

- [ ] Run:
  - `pytest tests/test_pipeline_runner.py tests/test_web_routes.py -k "source_tos_key or task creation" -q`

## Task 6: Validate And Gate Private TOS Optimization

**Files:**
- Modify: `appcore/tos_clients.py`
- Modify: `server.md`
- Test: `tests/test_tos_clients.py`

- [ ] Add a small private-endpoint readiness probe in `appcore.tos_clients.py`.
  - Use a lightweight object-list or head request against the private bucket hostname.
  - Cache failures briefly so the app does not stall every request on repeated private timeouts.

- [ ] Only enable server-side private uploads/downloads when:
  - `TOS_USE_PRIVATE_ENDPOINT=true`
  - readiness probe passes

- [ ] Document current private-endpoint caveats in `server.md`:
  - Mihomo direct rule already added for `ivolces.com`
  - private DNS now resolves bucket hostnames
  - `443` is still timing out and needs cloud-side validation before full enablement

- [ ] Run:
  - `pytest tests/test_tos_clients.py -q`

## Task 7: Operational Cloud Checklist

**Files:**
- Modify: `server.md`

- [ ] Before rollout, confirm in the VolcEngine console:
  - bucket `auto-video-srt` exists in `cn-shanghai`
  - CORS is configured for the production origin
  - upload credentials flow is available
  - private bucket hostname is reachable from this ECS if private mode is desired

- [ ] Record the exact bucket-domain verification commands in `server.md`, including:
  - `nslookup auto-video-srt.tos-cn-shanghai.ivolces.com 100.96.0.2`
  - `curl -I https://tos-cn-shanghai.volces.com`
  - Python SDK object list using the configured endpoint

## Task 8: End-To-End Verification And Deployment

**Files:**
- Modify: `deploy/setup.sh`
- Modify: `server.md`

- [ ] Extend deployment notes so the publish flow includes:
  - code deploy
  - server restart
  - TOS upload/download smoke checks

- [ ] Verify on the server after deployment:
  - browser can upload directly to TOS
  - task creation succeeds from uploaded object
  - pipeline can materialize and process the TOS source
  - final video download uses TOS
  - CapCut archive download uses TOS

- [ ] Run final regression commands:
  - `pytest tests/test_tos_clients.py tests/test_tos_upload_routes.py tests/test_pipeline_runner.py tests/test_web_routes.py -q`
  - `ssh -i "C:\Users\admin\.ssh\CC.pem" root@172.30.254.14 "cd /opt/autovideosrt && systemctl is-active autovideosrt"`

## Resume Notes

- Do **not** start by forcing the app onto `ivolces`.
- Start with Phase A so we can solve the user-visible bandwidth problem immediately.
- Re-check bucket choice early in implementation and switch all config/docs from `ad-kaogujia-video` to `auto-video-srt`.
- Re-open the private connectivity investigation only after the higher-priority issues are done.

