# TOS Backup Storage Design

Last updated: 2026-04-28

## Goal

Build a disaster-recovery storage layer for selected AutoVideoSrtLocal business assets so the server can be replaced quickly after disk failure. The system must keep a local copy and a TOS copy for every protected file, support a system-level `local_primary` / `tos_primary` switch, and run a daily 01:00 backup job for the previous day's file references and MySQL dump.

## Scope

Protected files are limited to user/business assets that must survive server disk loss:

- Original uploaded video files referenced by `projects.state_json.video_path`.
- Material-management video files in `media_items.object_key`.
- Material-management video cover files in `media_items.cover_object_key`.
- Product cover files in `media_product_covers.object_key`.
- Product detail images in `media_product_detail_images.object_key`.
- Raw source video files in `media_raw_sources.video_object_key`.
- Raw source cover files in `media_raw_sources.cover_object_key`.
- Raw source translation cover files in `media_raw_source_translations.cover_object_key`.

The feature does not protect code, virtual environments, logs, build outputs, transient cache files, or arbitrary temporary files.

## Storage Mode

Add one system-level switch:

```env
FILE_STORAGE_MODE=local_primary
```

Allowed values:

- `local_primary`: business logic continues to read and write local files first. The backup layer uploads missing TOS copies and downloads from TOS only when the local file is missing.
- `tos_primary`: business logic treats TOS as the authoritative remote copy. Reads still materialize a local file before returning a path to existing pipeline code, but when the local file is missing the system downloads from TOS immediately. If TOS is missing but local exists, the system uploads local to TOS.

Both modes maintain the invariant for active protected references:

```text
local file exists <=> TOS object exists
```

When one side is missing and the other side exists, the system repairs the missing side. When both are missing, the system records a sync error and leaves the database reference unchanged for operator review.

## TOS Bucket And Endpoint

The dedicated backup bucket is:

```env
TOS_BACKUP_BUCKET=autovideosrtlocal
```

Backup storage has its own endpoint settings instead of reusing historical ASR/media bucket behavior:

```env
TOS_BACKUP_PUBLIC_ENDPOINT=tos-cn-shanghai.volces.com
TOS_BACKUP_PRIVATE_ENDPOINT=tos-cn-shanghai.ivolces.com
TOS_BACKUP_USE_PRIVATE_ENDPOINT=false
TOS_BACKUP_REGION=cn-shanghai
TOS_BACKUP_PREFIX=FILES
TOS_BACKUP_DB_PREFIX=DB
TOS_BACKUP_ENV=test
```

`TOS_BACKUP_ENV` separates test and production objects in the same bucket. The test server should use `test`; production should use `prod`.

## DIRECT Network Path

TOS traffic must not consume proxy traffic. The implementation uses two layers:

1. Code-level proxy bypass:
   - Before creating the backup TOS client, ensure `NO_PROXY` and `no_proxy` contain `volces.com`, `.volces.com`, `ivolces.com`, and `.ivolces.com`.
   - This prevents Python SDK HTTP calls from using common proxy environment variables for TOS domains.

2. Server proxy rules:
   - Add `DOMAIN-SUFFIX,volces.com,DIRECT`.
   - Add `DOMAIN-SUFFIX,ivolces.com,DIRECT`.
   - If fake-ip is enabled, add `*.volces.com` and `*.ivolces.com` to fake-ip-filter.

The current server uses TUN mode, so the DIRECT requirement must hold after traffic enters the TUN stack. The deployment check must verify the active Clash/Mihomo rules include the DIRECT suffix rules above, the fake-ip filter prevents synthetic DNS answers for TOS domains, and `curl` / Python SDK probes to the selected endpoint do not match a proxy policy group. The backup job logs the selected endpoint type (`public` or `private`) and endpoint host on every run.

## Object Key Mapping

Every protected local file maps to a human-readable TOS object key derived from its absolute local path:

```text
{TOS_BACKUP_PREFIX}/{TOS_BACKUP_ENV}/{normalized absolute local path without drive colon or leading slash}
```

Path normalization rules:

- Convert backslashes to forward slashes.
- Resolve relative paths against the project root before mapping.
- Remove a Windows drive colon by turning `G:\path\file.mp4` into `G/path/file.mp4`.
- Remove leading `/` from POSIX absolute paths.
- Reject empty paths and paths containing traversal after resolution.

Examples:

```text
Local: /opt/autovideosrt-test/output/media_store/1/medias/23/a.jpg
TOS:   FILES/test/opt/autovideosrt-test/output/media_store/1/medias/23/a.jpg

Local: /opt/autovideosrt-test/uploads/task123.mp4
TOS:   FILES/test/opt/autovideosrt-test/uploads/task123.mp4

Local: G:\Code\AutoVideoSrtLocal\output\media_store\1\medias\23\a.jpg
TOS:   FILES/test/G/Code/AutoVideoSrtLocal/output/media_store/1/medias/23/a.jpg
```

This gives a direct visual correspondence: knowing the local path is enough to locate the TOS object.

## Reference Collection

The backup layer exposes a collector that returns protected file references as records:

```python
{
    "source": "media_item.video",
    "record_id": 123,
    "local_path": "/opt/autovideosrt-test/output/media_store/...",
    "object_key": "FILES/test/opt/autovideosrt-test/output/media_store/...",
}
```

Reference sources:

- `projects` rows whose `state_json.video_path` is present and whose type is a video-uploading workflow.
- `media_items.object_key`.
- `media_items.cover_object_key`.
- `media_product_covers.object_key`.
- `media_product_detail_images.object_key`.
- `media_raw_sources.video_object_key`.
- `media_raw_sources.cover_object_key`.
- `media_raw_source_translations.cover_object_key`.

For logical media object keys, local paths resolve through `appcore.local_media_storage.local_path_for()`. For `projects.state_json.video_path`, the path is used directly after resolution.

Daily incremental reference collection selects rows touched during the previous Beijing-time day:

- Prefer `updated_at` where the table has it.
- Fall back to `created_at` where `updated_at` is absent.
- Include rows soft-deleted during the window only for reporting; the job does not delete business files from TOS.

Full reconciliation ignores the date window and scans all active protected references.

## Sync Semantics

For each protected file reference:

1. Compute the local path.
2. Compute the TOS object key from that local path.
3. Check local existence.
4. Check TOS object existence.
5. Repair:
   - local exists and TOS missing: upload local to TOS.
   - TOS exists and local missing: download TOS to local.
   - both exist: mark synced.
   - both missing: record failed.

Deletes are intentionally conservative. The sync job does not delete business file objects from TOS automatically, even if the database row is soft-deleted. DB dump retention is the only automatic remote deletion in this feature.

## Runtime Reads And Writes

The first implementation focuses on the high-value storage paths already centralized in the codebase:

- `appcore.local_media_storage.write_bytes()`
- `appcore.local_media_storage.write_stream()`
- `appcore.local_media_storage.download_to()`
- `appcore.local_media_storage.exists()`
- project original video materialization for `projects.state_json.video_path`

Behavior:

- Writes create the local file and then ensure the TOS copy exists.
- Reads that need a local file materialize from TOS when local is missing.
- `exists()` returns true when either side exists in `tos_primary`; in `local_primary`, callers that need the file can still trigger materialization through `download_to()`.

Existing pipeline code can continue to work with local filesystem paths.

## Daily Job

Register an APScheduler cron job:

```text
hour=1, minute=0, timezone=Asia/Shanghai
```

The job backs up the previous Beijing-time day:

```text
start = yesterday 00:00:00
end   = today 00:00:00
```

Job steps:

1. Acquire a DB advisory lock to avoid duplicate runs across workers.
2. Collect previous-day protected references.
3. Reconcile collected file references.
4. Retry references that failed in the latest previous run.
5. Run MySQL dump.
6. Upload the compressed dump to TOS.
7. Delete DB dumps older than 7 days from `DB/{env}/`.
8. Insert a `scheduled_task_runs` row with counts and errors.

## MySQL Dump

Use the installed `mysqldump` executable on the server. Do not initialize or depend on local Windows MySQL.

Dump command shape:

```text
mysqldump --single-transaction --quick --routines --triggers --events --host ... --port ... --user ... DB_NAME
```

The process writes a gzip file locally under a runtime temp directory, uploads it to:

```text
DB/{env}/YYYY-MM-DD/autovideosrtlocal_YYYY-MM-DD_010000.sql.gz
```

Retention:

- Keep the latest 7 calendar days of dump files under `DB/{env}/`.
- Delete dump objects older than 7 days after a new dump upload succeeds.
- Never delete business files under `FILES/{env}/` as part of DB retention.

## Operator Interfaces

Add scripts for operations:

- `python -m scripts.tos_backup_sync --mode incremental --date YYYY-MM-DD`
- `python -m scripts.tos_backup_sync --mode full`
- `python -m scripts.tos_backup_sync --mode db-dump --date YYYY-MM-DD`
- `python -m scripts.tos_backup_sync --mode check --limit 100`
- `python -m scripts.tos_backup_sync --mode network-check`

The scripts print a concise summary:

- references checked
- uploaded count
- downloaded count
- already synced count
- failed count
- DB dump object key when applicable
- endpoint and DIRECT/proxy-bypass probe status for `network-check`

## Failure Handling

- TOS network errors mark the item failed and continue with the next file.
- Missing local and missing TOS records are failures, not silent skips.
- Download creates parent directories before writing.
- Upload uses the deterministic key for the local path and overwrites the backup object when repairing.
- DB dump failure does not block file sync results from being recorded.
- A failure in one file does not abort the whole daily run.

## Testing

Unit tests cover:

- Local path to TOS object key mapping on POSIX and Windows paths.
- Backup TOS client uses `TOS_BACKUP_BUCKET` and endpoint settings.
- Proxy bypass updates `NO_PROXY` / `no_proxy` without removing existing entries.
- Reconcile uploads when local exists and TOS is missing.
- Reconcile downloads when TOS exists and local is missing.
- Reconcile records failure when both are missing.
- Reference collector includes media item files, covers, product detail images, raw source files, raw source covers, and project original videos.
- Daily window uses previous Beijing-time day.
- DB dump object key and 7-day retention deletion.
- Scheduler registers the 01:00 daily job.

Integration tests use monkeypatched TOS clients and local temp files. No test should touch the real TOS bucket or a local MySQL server.

## Acceptance Criteria

- Setting `FILE_STORAGE_MODE=local_primary` keeps current local-first behavior while ensuring TOS backup objects are created for protected files.
- Setting `FILE_STORAGE_MODE=tos_primary` allows a fresh server with missing local protected files to restore them from TOS on access or full reconcile.
- Every protected local file path has a deterministic, human-readable TOS object key in `autovideosrtlocal`.
- Daily 01:00 job backs up previous-day protected files and uploads a gzip MySQL dump under `DB/{env}/YYYY-MM-DD/`.
- DB dump retention keeps 7 days and removes older dumps only after a successful new dump upload.
- TOS traffic is configured for DIRECT routing and bypasses proxy environment variables for Volcengine TOS domains.
- On the TUN-mode server, deployment documentation includes the required Clash/Mihomo DIRECT rules and fake-ip-filter entries, plus a command to run the network check.
