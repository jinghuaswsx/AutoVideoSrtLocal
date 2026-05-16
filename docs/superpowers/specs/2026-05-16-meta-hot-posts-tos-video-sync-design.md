# Meta Hot Posts TOS Video Sync Design

Last updated: 2026-05-16

## Background

Meta hot-post video localization stores downloaded MP4 paths in `meta_hot_posts.local_video_path`.
Those paths are relative to `config.OUTPUT_DIR`, for example `meta_hot_posts/videos/meta_hot_post_20.mp4`.
The mobile Meta hot-post page can switch video playback from the protected local route to a TOS signed URL.

Production investigation on 2026-05-16 found that downloaded local files existed, but the generated backup
object keys were absent in TOS. The backup reference collector was adding the raw relative value, so generic
TOS/NAS backup code looked for `meta_hot_posts/videos/...` under the app working directory instead of
`output/meta_hot_posts/videos/...`.

## Goals

- Resolve Meta hot-post `local_video_path` values against `config.OUTPUT_DIR` before handing them to the
  shared protected-file backup logic.
- Keep the TOS object key generation shared through `appcore.tos_backup_storage.backup_object_key_for_local_path`.
- Add a dedicated APScheduler task that incrementally reconciles localized Meta hot-post videos to TOS.
- Add a manual script so operators can backfill the current localized-video backlog to TOS using the same
  reconcile semantics as `tos_backup`.
- Surface Meta hot-post videos as their own module in TOS file management scans.

## Non-goals

- Do not connect to Windows local MySQL.
- Do not introduce a second object-key scheme for Meta videos.
- Do not change the local-video download queue or Facebook fallback behavior.
- Do not restart services as part of implementation.

## Sync Behavior

The dedicated sync selects rows where:

- `local_video_status = 'downloaded'`
- `local_video_path` is non-empty

For each row, it safely resolves the relative path under `config.OUTPUT_DIR` and calls
`tos_backup_storage.reconcile_local_file(path)`.

The summary follows existing backup conventions:

- `files_checked`
- `actions`
- `failed`
- `errors`

The manual script uses `limit=0` to mean "all localized videos"; the scheduled job uses a bounded batch so new
downloads are uploaded without waiting for the daily full backup.

## Scheduled Task

- Code: `meta_hot_posts_tos_video_sync_tick`
- Runner: `appcore.meta_hot_posts.tos_sync.run_scheduled_tos_video_sync`
- Schedule: every 10 minutes
- Log table: `scheduled_task_runs`

## Verification

- Unit tests cover OUTPUT_DIR path resolution in protected-file references.
- Unit tests cover the Meta video TOS sync summary and scheduled run wrapper.
- Scheduler tests cover APScheduler registration.
- Scheduled-task definition tests cover the Web admin task registry.
