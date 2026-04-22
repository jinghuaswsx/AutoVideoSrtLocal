# Local File Management Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make local disk the default file-management path, while keeping TOS only for third-party/public URL exchange that is actually required.

**Architecture:** User uploads, task sources, intermediate files, and final artifacts stay local by default. When a provider or external consumer explicitly needs a public pull URL, the system stages that file to TOS on demand instead of treating TOS as the primary storage backend. Legacy `pure_tos` creation endpoints stop creating new tasks, but historical compatibility reads stay available.

**Tech Stack:** Flask, Python, pytest, local filesystem storage, existing TOS client helpers

## Implementation Status (2026-04-22)

- [x] Subtitle removal: upload/bootstrap/result flow is local-primary; provider submit still stages public source URLs on demand.
- [x] Voice library matching: switched from signed TOS upload to local upload reservation; sample audio preview is served from local authenticated routes.
- [x] New pure-TOS creation: `/api/tos-upload/*`, `/api/de-translate/complete`, `/api/fr-translate/complete`, `/api/multi-translate/complete` now reject new task creation.
- [x] Runtime artifact handling: final outputs no longer auto-upload to TOS by default; historical `tos_uploads` reads remain compatible.
- [x] Medias/copywriting/subtitle-removal UI residue: removed stale TOS-only readiness/failure messaging where upload flow is already local.

### Verified Commands

- `pytest tests/test_tos_upload_routes.py::test_tos_upload_bootstrap_rejects_new_task_creation tests/test_tos_upload_routes.py::test_tos_upload_complete_rejects_new_task_creation tests/test_tos_upload_routes.py::test_de_translate_complete_rejects_new_pure_tos_creation tests/test_tos_upload_routes.py::test_de_translate_start_accepts_local_multipart_and_marks_local_primary tests/test_tos_upload_routes.py::test_fr_translate_start_accepts_local_multipart_and_marks_local_primary tests/test_tos_upload_routes.py::test_fr_translate_complete_rejects_new_pure_tos_creation tests/test_multi_translate_routes.py::test_multi_translate_complete_rejects_new_pure_tos_creation -q`
- `pytest tests/test_voice_library_routes.py::test_filters_no_language_returns_languages_and_empty_options tests/test_voice_library_routes.py::test_match_upload_url_returns_local_upload_reservation tests/test_voice_library_routes.py::test_match_local_upload_writes_reserved_file tests/test_voice_library_routes.py::test_match_start_returns_task_id tests/test_voice_library_routes.py::test_match_start_rejects_missing_local_upload tests/test_voice_library_routes.py::test_match_status_returns_task_state tests/test_voice_library_routes.py::test_match_sample_audio_serves_owned_file tests/test_voice_match_tasks.py -q`
- `pytest tests/test_subtitle_removal_routes.py::test_subtitle_removal_bootstrap_allows_local_upload_without_tos tests/test_subtitle_removal_routes.py::test_subtitle_removal_complete_upload_prepares_first_frame tests/test_subtitle_removal_routes.py::test_subtitle_removal_complete_upload_keeps_source_local_until_submit tests/test_subtitle_removal_routes.py::test_subtitle_removal_submit_stages_public_source_on_demand tests/test_subtitle_removal_runtime.py::test_runtime_success_downloads_result_and_finishes_locally tests/test_subtitle_removal_runtime.py::test_runtime_resumes_existing_result_upload_without_re_submitting_provider tests/test_pipeline_runner.py::test_upload_artifacts_to_tos_keeps_new_artifacts_local_by_default -q`
- `python -m py_compile appcore/runtime.py appcore/subtitle_removal_runtime.py appcore/voice_match_tasks.py web/routes/subtitle_removal.py web/routes/voice_library.py web/routes/tos_upload.py web/routes/de_translate.py web/routes/fr_translate.py web/routes/multi_translate.py web/routes/medias.py`

---

## Audit Summary

### Keep TOS As Public Exchange Only

- `pipeline/asr.py`: 豆包 ASR 只接受可公网访问的音频 URL，本地音频仍需临时上传后再识别。
- `pipeline/storage.py`: 已经是“公网交换层”封装，供第三方 provider 回拉使用，应保留。
- `pipeline/copywriting.py`: 豆包多模态视频/图片输入仍依赖 TOS URL，不应误删。
- `web/routes/video_creation.py`: Seedance 输入视频/图片/音频时仍需外部可拉取 URL，应继续按需上传到 TOS。
- `web/routes/subtitle_removal.py` + `appcore/subtitle_removal_runtime.py`: 上传与结果保存应本地优先，但提交到 provider 时仍要按需生成公网源地址。
- `appcore/subtitle_removal_runtime_vod.py` / `appcore/subtitle_removal_vod_scheduler.py`: VOD 托管链路本身就是公网交换场景，保留。
- `web/routes/openapi_materials.py`: 对外 OpenAPI / push payload 面向外部系统，下载地址继续保留为签名 URL。

### Already Local-Primary Or Compatible

- `web/routes/task.py`: 英语主翻译上传入口已是本地 `multipart`，并标记 `delivery_mode="local_primary"`。
- `web/routes/de_translate.py`: 主入口已本地上传，兼容 `bootstrap/complete` 仍会创建 `pure_tos` 任务。
- `web/routes/fr_translate.py`: 主入口已本地上传，兼容 `bootstrap/complete` 仍会创建 `pure_tos` 任务。
- `web/routes/multi_translate.py`: 主入口已本地上传，兼容 `bootstrap/complete` 仍会创建 `pure_tos` 任务。
- `web/services/artifact_download.py`: 非 `pure_tos` 任务已是本地优先下载，历史 `pure_tos` 任务继续兼容 TOS 回落。
- `web/routes/image_translate.py` + `appcore/local_media_storage.py`: 当前主存储已是本地 media store，仅保留历史 TOS 兼容读取。

### Remaining Rectification Targets

- `web/routes/voice_library.py` + `appcore/voice_match_tasks.py` + `web/static/voice_library.js`
  仍使用签名直传和 sample audio 回传 TOS，属于应改成本地的内部中转链路。
- `web/routes/tos_upload.py`
  仍允许新建 `pure_tos` 翻译任务，应降级为禁用的新建入口。
- `web/routes/de_translate.py` / `web/routes/fr_translate.py` / `web/routes/multi_translate.py`
  兼容 `bootstrap/complete` 仍能创建新 `pure_tos` 任务，应改为拒绝新建。
- `appcore/runtime.py`
  正常完成后仍默认把最终产物上传到 TOS，应改为本地优先，按需导出。
- `web/templates/medias_list.html` + `web/static/medias.js`
  上传已走本地，但页面仍暴露 `MEDIAS_TOS_READY` 和“TOS 未配置/上传失败”文案，需清理。
- `web/templates/_subtitle_removal_scripts.html`
  前端提示文案仍写“上传到对象存储失败”，需改成中性/本地上传表述。

## File Map

- Modify: `web/routes/subtitle_removal.py`
- Modify: `appcore/subtitle_removal_runtime.py`
- Modify: `web/routes/voice_library.py`
- Modify: `appcore/voice_match_tasks.py`
- Modify: `web/static/voice_library.js`
- Modify: `web/routes/tos_upload.py`
- Modify: `web/routes/de_translate.py`
- Modify: `web/routes/fr_translate.py`
- Modify: `web/routes/multi_translate.py`
- Modify: `appcore/runtime.py`
- Modify: `web/routes/medias.py`
- Modify: `web/templates/medias_list.html`
- Modify: `web/static/medias.js`
- Modify: `tests/test_subtitle_removal_routes.py`
- Modify: `tests/test_subtitle_removal_runtime.py`
- Modify: `tests/test_voice_library_routes.py`
- Modify: `tests/test_voice_match_tasks.py`
- Modify: `tests/test_tos_upload_routes.py`
- Modify: `tests/test_web_routes.py`
- Modify: `tests/test_multi_translate_routes.py`

### Task 1: Subtitle Removal Uses Local Uploads And On-Demand TOS Staging

**Files:**
- Modify: `tests/test_subtitle_removal_routes.py`
- Modify: `tests/test_subtitle_removal_runtime.py`
- Modify: `web/routes/subtitle_removal.py`
- Modify: `appcore/subtitle_removal_runtime.py`

- [x] Add or update failing tests so upload bootstrap and complete no longer require TOS, and result handling defaults to local files.
- [x] Run focused tests and confirm the failure is caused by old eager-TOS behavior.
  Run: `pytest tests/test_subtitle_removal_routes.py tests/test_subtitle_removal_runtime.py -q`
- [x] Change route flow so bootstrap/complete only manage local files and metadata, and keep source staging for submit-time provider needs.
- [x] Change runtime flow so submit still resolves a public source URL when needed, but result download finishes locally without default `result_tos_key`.
- [~] Re-run focused tests until green.
  Status: 关键场景单测已逐条通过；整文件聚焦回归在当前环境下耗时过长，需要分批跑或延长超时。

### Task 2: Voice Library Stops Using TOS As Internal Transit Storage

**Files:**
- Modify: `tests/test_voice_library_routes.py`
- Modify: `tests/test_voice_match_tasks.py`
- Modify: `web/routes/voice_library.py`
- Modify: `appcore/voice_match_tasks.py`
- Modify: `web/static/voice_library.js`

- [ ] Add or update failing tests for local upload bootstrap, local task input, and local preview URL delivery.
- [ ] Run focused tests and confirm they fail against the old signed-upload flow.
  Run: `pytest tests/test_voice_library_routes.py tests/test_voice_match_tasks.py -q`
- [ ] Replace signed-upload/object-key flow with local upload reservation + local file path ownership checks.
- [ ] Replace sample audio signed URL output with a local authenticated preview endpoint.
- [ ] Re-run focused tests until green.

### Task 3: Disable New Pure-TOS Task Creation Paths

**Files:**
- Modify: `tests/test_tos_upload_routes.py`
- Modify: `tests/test_web_routes.py`
- Modify: `tests/test_multi_translate_routes.py`
- Modify: `web/routes/tos_upload.py`
- Modify: `web/routes/de_translate.py`
- Modify: `web/routes/fr_translate.py`
- Modify: `web/routes/multi_translate.py`

- [ ] Add or update failing tests so legacy bootstrap/complete endpoints no longer create new `pure_tos` tasks.
- [ ] Run focused tests and confirm old compatibility behavior is still active.
  Run: `pytest tests/test_tos_upload_routes.py tests/test_web_routes.py tests/test_multi_translate_routes.py -q`
- [ ] Change compatibility endpoints to reject new task creation with a clear migration message.
- [ ] Keep template-level assertions intact so current pages remain local-upload only.
- [ ] Re-run focused tests until green.

### Task 4: Stop Default Artifact Backup Uploads To TOS

**Files:**
- Modify: `appcore/runtime.py`
- Add or modify: relevant runtime tests if needed

- [ ] Add or update a failing test around default artifact-upload side effects if coverage is missing.
- [ ] Remove unconditional `_upload_artifacts_to_tos(...)` from normal local-primary completion flow, or gate it behind explicit compatibility intent.
- [ ] Re-run affected runtime tests.

### Task 5: Clean User-Facing TOS Residue In Medias UI

**Files:**
- Modify: `web/routes/medias.py`
- Modify: `web/templates/medias_list.html`
- Modify: `web/static/medias.js`

- [ ] Add or update tests for local upload URLs and ensure the page no longer depends on `tos_ready`.
- [ ] Remove stale `MEDIAS_TOS_READY`, `TOS 未配置`, and `TOS 上传失败` messaging where uploads are already local.
- [ ] Re-run affected medias tests.

### Verification

- [ ] Run subtitle-removal focused regression.
  Run: `pytest tests/test_subtitle_removal_routes.py tests/test_subtitle_removal_runtime.py -q`
- [ ] Run voice-library focused regression.
  Run: `pytest tests/test_voice_library_routes.py tests/test_voice_match_tasks.py -q`
- [ ] Run compatibility route regression.
  Run: `pytest tests/test_tos_upload_routes.py tests/test_web_routes.py tests/test_multi_translate_routes.py -q`
- [ ] Run any extra targeted tests touched during implementation.

### Notes

- Historical read compatibility for existing `pure_tos` tasks stays in place unless a specific test proves the code can be simplified safely.
- TOS staging remains valid where external APIs or external consumers need a public URL, including ASR, public OpenAPI payloads, and provider pull flows.
