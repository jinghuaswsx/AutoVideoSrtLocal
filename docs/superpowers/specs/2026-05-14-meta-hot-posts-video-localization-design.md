# Meta Hot Posts Video Localization Design

Last updated: 2026-05-14

## Background

Meta hot posts currently store the upstream `video_url` and render video cards through a Facebook iframe. This works for previewing public posts, but it depends on Facebook availability and does not give the selection workflow a stable local video asset.

This design adds a conservative local cache for hot-post videos. It follows the project rules for Meta hot posts and does not use Windows local MySQL for verification.

## Goals

- Download every hot-post video that has `video_url` and no local cached video yet.
- Render cards with the local MP4 first when available.
- Keep Facebook iframe fallback for videos that are not downloaded, failed, or not yet processed.
- Avoid triggering upstream rate limits by using one worker, no concurrency, and a minimum 10 second pause after each video attempt.
- Make the job resumable and auditable through persisted status fields.

## Non-goals

- Do not bypass Facebook or wedev authentication, permissions, geo restrictions, or platform controls.
- Do not add parallel downloading.
- Do not auto-retry aggressively. Failed rows stay visible with a reason and can be retried later.
- Do not upload these videos to TOS in this change; the first safe step is local filesystem storage.

## Data Model

`meta_hot_posts` gains local-video cache fields:

- `local_video_path`: relative path under the configured local cache root.
- `local_video_status`: `pending`, `downloading`, `downloaded`, `failed`, or `skipped`.
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
5. Mark success with the relative local path, or mark failure/skipped with a bounded error message.
6. Sleep at least 10 seconds before the next row, regardless of success or failure.

The default command is intentionally serial. Operators can run a limited batch repeatedly until there are no pending videos.

## Singleton Takeover

Video localization uses a takeover singleton. The APScheduler job runs once immediately when the web service starts, then continues every 10 minutes. At the beginning of every new run, the scheduler checks for an existing `meta_hot_posts_video_localization_tick` run that is still marked `running`. If one exists, the new run immediately marks that older run `failed`, resets all rows still in `local_video_status='downloading'` to `failed`, records the takeover in the run summary, and then starts its own download batch.

This is intentionally different from product analysis and message translation, which still skip or take over only after their stale-run timeout. Video downloads are external and long-running, so a new invocation should own the queue instead of waiting behind a stale logical run.

## Rendering

The list API includes `local_video_url` and cache status fields. The card media renderer uses this order:

1. Local MP4 via a protected Flask route.
2. Existing Facebook iframe fallback.
3. Image fallback.
4. Empty media box.

The local media route only serves files resolved inside the hot-post video cache directory and supports normal browser video playback.

## Safety

- Each new run takes over any older `running` video-localization run before downloading, so the persisted task state remains a singleton.
- Service startup schedules an immediate video-localization run instead of waiting for the first 10-minute interval.
- The downloader itself never starts parallel downloads.
- The minimum per-item delay is clamped to 10 seconds.
- Existing downloaded files are not fetched again unless explicitly reset later.
- Failed attempts are recorded and capped by a max-attempts filter.
- Paths are stored relative to a known cache root and served through safe path resolution.

## Verification

- Unit tests cover selection fields, status transitions, delay clamping, failure handling, and card rendering preference.
- Route tests cover local-video route safety and missing-file behavior.
- Manual verification can run a tiny batch such as one item, then inspect the card preview.
