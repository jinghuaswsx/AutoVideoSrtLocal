# Meta Hot Posts Video Localization Design

Last updated: 2026-05-18

## Background

Meta hot posts currently store the upstream `video_url` and render video cards through a Facebook iframe. This works for previewing public posts, but it depends on Facebook availability and does not give the selection workflow a stable local video asset.

This design adds a conservative local cache for hot-post videos. It follows the project rules for Meta hot posts and does not use Windows local MySQL for verification.

## Goals

- Download every hot-post video that has `video_url` and no local cached video yet.
- Render cards with the local MP4 first when available.
- Persist local-video duration and first-frame cover metadata during localization so list pages only load ready image covers.
- Provide a script to backfill duration and cover metadata for videos already downloaded before this change.
- Keep Facebook iframe fallback for videos that are not downloaded, failed, or not yet processed.
- Avoid triggering upstream rate limits by using one worker, no concurrency, and a minimum 30 second pause after each video attempt.
- Make the job resumable and auditable through persisted status fields.

## Non-goals

- Do not bypass Facebook or wedev authentication, permissions, geo restrictions, or platform controls.
- Do not add parallel downloading.
- Do not auto-retry aggressively. Failed rows stay visible with a reason and can be retried later.
- Do not compute video duration or first-frame covers during page rendering.

## Data Model

`meta_hot_posts` gains local-video cache fields:

- `local_video_path`: relative path under the configured local cache root.
- `local_video_duration_seconds`: ffprobe-derived local video duration in seconds.
- `local_video_cover_path`: relative path under `OUTPUT_DIR` for the first-frame JPEG cover.
- `local_video_status`: `pending`, `downloading`, `downloaded`, `failed`, or `unavailable`.
- `local_video_error`: last failure or skip reason.
- `local_video_downloaded_at`: timestamp for successful downloads.
- `local_video_attempts`: number of download attempts.

Rows with a `video_url` and no downloaded local video are eligible. The worker also treats legacy rows with `NULL` status as pending.

## Download Flow

The downloader selects a small batch ordered by newest/most useful rows, then processes one row at a time:

1. Mark row as `downloading` and increment attempts.
2. Use `yt-dlp` as the extraction engine because hot-post `video_url` values are often Facebook Reel/page URLs, not direct MP4 links.
3. Save the video as an MP4 under `output/meta_hot_posts/videos/`.
4. Verify the output file exists and is non-empty.
5. Probe the downloaded local file for duration and extract its first frame to `output/meta_hot_posts/video_covers/`.
6. Mark success with the relative local video path, duration, and local cover path, or mark failure/skipped with a bounded error message.
7. Sleep at least 30 seconds before the next row, regardless of success or failure.

The default command is intentionally serial. Operators can run a limited batch repeatedly until there are no pending videos.

Existing downloaded rows are backfilled by `tools/meta_hot_posts_local_video_metadata_backfill.py`. The script scans `local_video_status='downloaded'` rows missing either `local_video_duration_seconds` or `local_video_cover_path`, resolves the local MP4 under `OUTPUT_DIR`, probes duration, extracts the first-frame cover, and writes the metadata without touching online-only videos.

## Singleton Takeover

Video localization uses a startup takeover singleton. The APScheduler job schedules a startup run a few seconds after the web service starts, then continues every 10 minutes. The startup run checks for an existing `meta_hot_posts_video_localization_tick` run that is still marked `running`. If one exists, startup immediately marks that older run `failed`, resets all rows still in `local_video_status='downloading'` to `failed`, records the takeover in the run summary, and then starts its own download batch. Regular interval runs do not take over a running row; they skip until the active batch finishes.

Failed video downloads use a conservative retry policy: a failed row is not eligible for retry until its last failure is at least 12 hours old. A row is attempted at most 5 times; after the fifth failed attempt it is marked `local_video_status='unavailable'` and no longer enters the download queue.

This is intentionally different from product analysis and message translation, which still skip or take over only after their stale-run timeout. Video downloads are external and long-running, so a new invocation should own the queue instead of waiting behind a stale logical run.

## Alerting

The video localization task may legitimately produce failed run rows for low-volume batches, upstream throttling, unavailable videos, or startup takeover cleanup. Those rows remain in `scheduled_task_runs` for audit, but they should not page the operations chat by consecutive-failure count alone.

Feishu and the Web scheduled-task alert banner only surface this task when the current Beijing-day download attempts are high enough and the failure rate is severe:

- Daily download attempts must be greater than 20.
- Daily failed attempts divided by download attempts must be greater than 80%.
- Otherwise the failed run is recorded but alert dispatch is suppressed.
- Recovery notifications are also suppressed for this task, because low-volume failures may never have produced a failure alert.

## Rendering

The list API includes `local_video_url`, `local_video_cover_url`, `video_duration_seconds`, TOS URL fields, and cache status fields. The card media renderer initially loads only a persisted image cover:

1. Persisted local/TOS cover image when available.
2. Existing post image fallback.
3. Empty media box.

Clicking the play affordance then loads the actual video playback surface:

1. Local MP4 via a protected Flask route.
2. TOS signed MP4 when the page is switched to the TOS source and the object is available.
3. Existing Facebook iframe fallback.
4. Empty media box.

The local media routes only serve files resolved inside `OUTPUT_DIR` and support normal browser video and image delivery.

## Safety

- Each startup run takes over any older `running` video-localization run before downloading, so the persisted task state remains a singleton after deploys/restarts.
- Service startup schedules an initial video-localization run instead of waiting for the first 10-minute interval.
- The downloader itself never starts parallel downloads.
- The minimum per-item delay is clamped to 30 seconds.
- Existing downloaded files are not fetched again unless explicitly reset later.
- Existing downloaded files missing metadata are handled by the explicit backfill script instead of page-time probing.
- Failed attempts are recorded and capped by a max-attempts filter.
- Paths are stored relative to a known cache root and served through safe path resolution.

## Verification

- Unit tests cover selection fields, status transitions, delay clamping, metadata extraction, backfill behavior, failure handling, and card rendering preference.
- Route tests cover local-video and local-cover route safety and missing-file behavior.
- Manual verification can run a tiny batch such as one item, then inspect the card preview.
