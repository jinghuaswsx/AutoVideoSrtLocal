# Translation Pure TOS Design

## Goal

Make the English, German, and French video translation modules use a pure TOS
file flow for all new tasks:

- users upload source files to TOS over the public endpoint
- the server stores TOS object keys instead of relying on user-downloadable
  local files
- the server downloads required files from TOS through the private endpoint for
  processing
- final artifacts are uploaded back to TOS through the private endpoint
- users download final artifacts from TOS over the public endpoint

This removes new-task reliance on server public-bandwidth file transfer.

## Confirmed Requirements

1. Scope is the video translation modules for English, German, and French.
2. All new-task upload and download flows must go through TOS.
3. User upload and download traffic must use the TOS public endpoint.
4. Server upload and download traffic must use the TOS private endpoint.
5. Source uploads must land in TOS first, and the server should persist the
   `source_tos_key`.
6. When the server needs a source file, it should materialize it locally by
   downloading it from TOS through the private endpoint.
7. Final user-downloadable artifacts such as translated videos and CapCut
   packages must be uploaded to TOS first, then downloaded by the user from
   TOS.
8. New tasks must no longer rely on Flask `send_file` for final downloads.
9. Existing old tasks do not need automatic backfill or compatibility upgrades.
   This work only guarantees the pure-TOS path for new tasks.

## Out Of Scope

- Auto-backfilling legacy tasks that only have local artifacts
- A one-time migration script for historical source videos or outputs
- Non-translation modules unless they already share the exact helper being
  updated
- Large changes to artifact schema beyond what is needed for pure-TOS download
  ownership

## Options Considered

### Option 1: Pure TOS For New Tasks Only (Recommended)

All new English, German, and French translation tasks use the same TOS-first
upload, processing, and download flow. Old tasks are left as-is.

Why this is recommended:

- exactly matches the requested boundary
- removes server public-bandwidth pressure for new traffic
- keeps rollout complexity under control by avoiding legacy migration in the
  same change
- aligns with existing partial TOS work already present in the repository

### Option 2: Pure TOS Plus Legacy Auto-Backfill

New tasks use pure TOS, while old tasks lazily upload local artifacts to TOS
when accessed.

Why this is not recommended:

- mixes migration logic into runtime routes
- increases test surface and failure modes
- was explicitly ruled out by the user

### Option 3: Upload To TOS But Keep Local Download Fallback

New tasks upload sources and outputs to TOS, but download routes still fall
back to local files when TOS objects are missing.

Why this is not recommended:

- keeps server public-bandwidth downloads alive
- allows silent regression back to the old path
- conflicts with the requirement to completely abandon local user-download
  logic for new tasks

## Recommended Design

### 1. Canonical File Ownership

For all new translation tasks, TOS becomes the canonical store for user-facing
source and final files.

Canonical ownership rules:

- source video:
  - canonical location: TOS object referenced by `source_tos_key`
  - local copy: ephemeral processing materialization only
- final downloadable artifacts:
  - canonical location: TOS objects referenced in `task.tos_uploads`
  - local copy: transient pipeline output only

The server may still write local working files because ffmpeg, CapCut export,
and related pipeline steps require filesystem access. However, those files are
not treated as the external delivery channel anymore.

### 2. Upload Flow Unification

New-task creation should be unified around:

1. `bootstrap`
2. browser uploads directly to TOS with the public signed URL
3. `complete`
4. server validates the uploaded object and creates the task record

English flow:

- continue using `/api/tos-upload/bootstrap`
- continue using `/api/tos-upload/complete`
- stop treating the local multipart `/api/tasks` upload flow as a valid new-task
  creation path for translation uploads

German and French flows:

- keep module-specific `bootstrap` and `complete` endpoints
- remove or stop wiring the legacy `file.save(...)` upload branches for new
  translation tasks
- make the front-end entry points consistently use the TOS direct-upload flow

`complete` contract requirements:

- task id must match the object key naming convention
- object key must match the server-generated expected key
- object must exist in TOS before the task is created
- server stores:
  - `source_tos_key`
  - original filename
  - object size
  - content type when available
  - task display name

### 3. Source Materialization For Processing

Translation runtime continues to require a local `video_path` for ffmpeg and
downstream processing. The pure-TOS contract is:

- task records always retain a `video_path` pointing to the expected local
  working location
- if the file is missing locally and `source_tos_key` exists, the server
  downloads the object from TOS before starting pipeline work
- server-side TOS reads must use the private endpoint whenever available

This should be implemented consistently across English, German, and French by
using the same source-materialization semantics already present in helper code:

- source retrieval is keyed by `source_tos_key`
- local file restoration happens immediately before processing
- failure to restore source becomes a task error, not a user-download fallback

### 4. Final Artifact Upload Contract

The final translation outputs that are user-downloadable must be uploaded to
TOS as part of the pipeline completion flow for new tasks.

Required artifact kinds:

- `soft_video`
- `hard_video`
- `srt`
- `capcut_archive`

Artifact upload rules:

- uploads happen after outputs are generated locally
- server uses the TOS private endpoint for these uploads
- each uploaded artifact records:
  - `tos_key`
  - `artifact_kind`
  - `variant`
  - `file_size`
  - `uploaded_at`
- these records are stored under `task.tos_uploads`

Variant handling:

- English already uses variant-aware outputs and should continue recording
  variant-qualified slots like `normal:soft_video`
- German and French should align with the same artifact-record structure so the
  shared download helper can remain simple and consistent

### 5. Download Contract For New Tasks

For new translation tasks, download routes should no longer stream final files
from local disk to the user.

New route behavior:

- resolve the requested artifact kind
- read the corresponding `tos_uploads` record
- generate a public signed download URL
- redirect the browser to that URL

Allowed user-facing behavior:

- HTTP redirect to TOS public signed URL when artifact exists
- explicit error when artifact does not exist in TOS

Disallowed for new tasks:

- local `send_file` fallback for final translation downloads
- using server public bandwidth as the delivery path for final artifacts

CapCut special handling:

- before upload, the server may still rewrite project paths for the current
  user and rebuild the final archive locally
- after the archive is finalized, it must be uploaded to TOS through the
  private endpoint
- the user then downloads from TOS through a public signed URL

### 6. Failure Handling

#### Browser Upload Failure

- if the browser PUT to TOS fails, task creation does not proceed
- the UI should show upload failure directly from the direct-upload workflow

#### `complete` Validation Failure

- reject task creation if object key mismatches expected naming
- reject task creation if the object does not exist in TOS

#### Source Restoration Failure

- if source materialization from `source_tos_key` fails, the task enters an
  error state with a clear source-download failure message
- do not ask the user to download from the server as a fallback

#### Final Artifact Upload Failure

- if a final artifact cannot be uploaded to TOS, that artifact is not
  considered downloadable
- download routes must return a clear error rather than reading the local file
- the pipeline may still finish the local processing steps, but delivery is
  blocked until TOS upload succeeds

#### Cleanup And Deletion

- when a task is deleted, cleanup must continue collecting and deleting:
  - `source_tos_key`
  - all `task.tos_uploads[*].tos_key`
- this prevents orphaned objects under the new pure-TOS flow

### 7. Runtime Endpoint Semantics

Public endpoint usage:

- browser upload signed URLs
- browser download signed URLs

Private endpoint usage:

- server download of source objects before processing
- server upload of final artifacts after processing
- any server-side cleanup or existence checks related to those objects

This design relies on the existing TOS client selection logic:

- public client for signed URLs returned to the browser
- private/server client for internal reads and writes when private endpoint
  probing succeeds

### 8. Route And Helper Changes

Expected code-level shape:

- English:
  - keep TOS bootstrap/complete flow as the intended upload path
  - tighten download behavior so new tasks only redirect to TOS
- German:
  - finish removal of local upload creation path
  - ensure start/materialization uses `source_tos_key`
  - ensure final download path uses TOS-only behavior for new tasks
- French:
  - same as German
- shared helper:
  - `web.services.artifact_download` becomes authoritative for translation
    final downloads
  - remove local final-download fallback for new pure-TOS tasks

Because old tasks are out of scope, the simplest path is to use new-task state
shape to decide behavior. Any task created through the new pure-TOS flow should
have `source_tos_key` and expected TOS output metadata; for those tasks, local
final download fallback should be disabled.

## Testing Plan

### Automated Tests

Add or update tests for:

1. English upload page and upload endpoints continue using TOS bootstrap and
   complete flow
2. German bootstrap and complete routes create tasks from TOS objects without
   local file upload save
3. French bootstrap and complete routes create tasks from TOS objects without
   local file upload save
4. task start/materialization downloads source video from `source_tos_key`
   before processing
5. export/upload step records all final artifacts into `tos_uploads`
6. download routes for new tasks return redirect responses to signed TOS URLs
7. download routes for new pure-TOS tasks do not stream local files when TOS
   upload metadata is missing
8. delete flows collect both source and final artifact TOS keys for cleanup

### Operational Verification

1. Create a new English task through the direct-upload page
2. Create a new German task through the direct-upload page
3. Create a new French task through the direct-upload page
4. For each task, confirm:
   - source file exists in TOS after upload
   - server can start processing after restoring source locally
   - final artifacts are uploaded to TOS
   - browser downloads redirect to TOS rather than proxying through Flask

## Risks

- Some English routes may still expose legacy multipart endpoints; these must
  not remain wired as the preferred new-task path after the change.
- CapCut archive generation remains partially local by necessity, so the change
  must ensure the final public delivery still switches to TOS.
- If private endpoint probing fails in an environment, server traffic may fall
  back to the public endpoint based on current helper behavior. That is a
  deployment/configuration risk and should be surfaced during verification.
- Because legacy tasks are not migrated, mixed historical behavior will still
  exist in the database until a future migration project is executed.

## Success Criteria

The work is successful when all new English, German, and French translation
tasks satisfy the following:

- source upload is browser -> TOS public endpoint
- source storage is persisted as `source_tos_key`
- source processing retrieval is server -> TOS private endpoint
- final artifact upload is server -> TOS private endpoint
- final user download is browser -> TOS public signed URL
- Flask no longer serves final downloadable translation artifacts from local
  disk for those new tasks
