# Mingkong Video Material Local Library Merge Notes

Last updated: 2026-05-18

## Context

The original local-library idea for `/xuanpin/mk` was option A:

- store Mingkong video metadata locally;
- cache cover images locally;
- keep MP4 downloads on demand through the existing video cache path.

The latest code already implements most of the metadata side through the daily Top300
snapshot work:

- `appcore/mingkong_materials.py`
- `tools/mingkong_material_daily_snapshot.py`
- `db/migrations/2026_05_18_mingkong_material_daily_snapshots.sql`
- `GET /xuanpin/api/mk-material-library`
- `GET /xuanpin/api/mk-yesterday-top100`
- `web/templates/mk_selection.html`

Therefore this design must not create another parallel `xuanpin_mk_*` material index.
The right merge path is to keep the existing `mingkong_material_*` tables as the single
local Mingkong material library and fill the remaining gaps from option A.

## Current Local Flow

1. A scheduled job `mingkong_material_daily_snapshot` runs daily at 06:00 Beijing time.
2. It reads the latest Dianxiaomi Listing ranking snapshot and takes the first 300
   products by `rank_position`.
3. For each product code, it queries Mingkong `/api/marketing/medias`.
4. It selects the matching Mingkong product, flattens all visible videos, and upserts
   rows into `mingkong_material_daily_snapshots`.
5. It derives yesterday-spend Top100 into `mingkong_material_daily_top100`.
6. `/xuanpin/mk` now reads the local archived APIs for the video material tab and the
   yesterday Top100 tab. The page no longer uses the old live
   `/xuanpin/api/mk-video-materials` path as its primary listing source.

## Conflicts Avoided

Do not add these previously proposed objects:

- `xuanpin_mk_video_materials`
- `xuanpin_mk_video_material_sync_runs`
- `appcore/mk_video_material_index.py`
- `tools/sync_mk_video_material_index.py`
- `mk_video_material_index_sync`

They duplicate the existing `mingkong_material_daily_snapshots`,
`mingkong_material_sync_runs`, `appcore/mingkong_materials.py`, runner, and scheduler
entry.

The existing live API `/xuanpin/api/mk-video-materials` can remain as a fallback or
diagnostic route, but the page-level material library should continue to use
`/xuanpin/api/mk-material-library`.

## Remaining Merge Work

### 1. Local Cover Cache

The current snapshot stores `video_image_path`, but card covers still render through:

```text
/xuanpin/api/mk-media?path=<video_image_path>
```

Add cover cache fields to the existing local material tables instead of creating new
tables. Recommended fields:

- `local_cover_object_key`
- `cover_cached_at`
- `cover_cache_error`

At minimum add them to `mingkong_material_daily_snapshots`. If the Top100 table remains
a denormalized display table, add the same fields there too, or return them by joining
back to `mingkong_material_daily_snapshots` by `(snapshot_date, material_key)`.

The daily sync should try to cache each cover into the existing local media/object
storage. Cover failures must not block metadata archiving.

### 2. API Payload Compatibility

`list_material_library()` and `list_yesterday_top100()` should return either:

- `local_cover_object_key`, plus a frontend helper that builds the local media URL; or
- `local_cover_url`, already ready for the template to render.

The existing `video_image_path` should stay in the payload so the UI can fall back to
the Mingkong media proxy when no local cover exists.

### 3. Frontend Rendering

`renderMkVideoMaterialCard()` should prefer the local cover:

1. render `local_cover_url` or local object URL when present;
2. fall back to `/xuanpin/api/mk-media?path=<video_image_path>`;
3. keep MP4 preview through `/xuanpin/api/mk-video?path=<video_path>`.

This keeps option A's performance benefit without changing the existing on-demand video
download behavior.

### 4. Run Failure State

`run_daily_snapshot()` currently finishes `scheduled_task_runs` as failed when an
exception escapes, but the matching `mingkong_material_sync_runs` row can be left in
`running` status if the failure happens after the run row is created.

On exceptions after `run_id` exists, update:

- `mingkong_material_sync_runs.status = 'failed'`
- `error_message`
- `finished_at`
- latest processed/material/failure counters when available

This prevents the local library status from showing a stale running sync.

### 5. Matching Rule Alignment

The live material selection rule prioritizes exact product-link match, then stronger
spend/ad signals. The current snapshot selector scores:

```text
(exact, video_count, spend, ads, id)
```

To avoid drift between live browsing and archived snapshots, align it with the intended
rule:

```text
(exact, spend, ads, video_count, id)
```

This is especially important when Mingkong returns multiple candidate products and one
has many low-value videos while another has fewer but clearly higher-spend material.

### 6. Snapshot Date UX

The API already supports a `snapshot` parameter, but the current video material tab
loads the latest material snapshot by default and does not pass the page's Dianxiaomi
date dropdown value.

For the first merge, keeping "latest local material snapshot" is acceptable because the
main problem is speed. If operators need date inspection later, add a separate material
snapshot selector or pass an explicit material snapshot date. Do not silently imply that
the Dianxiaomi product snapshot dropdown controls the material archive unless the dates
are actually wired together.

## Non-Goals

- Do not bulk-download Mingkong MP4 files during the daily sync.
- Do not replace the existing material import or task creation flows.
- Do not use Windows local MySQL for verification.
- Do not add a second scheduler entry for the same Top300 material scan.

## Verification

Focused no-local-MySQL tests should cover:

- migration fields for local cover cache;
- cover-cache success and failure serialization;
- local cover preferred over Mingkong proxy in `mk_selection.html`;
- `run_daily_snapshot()` marking `mingkong_material_sync_runs` failed on fatal errors;
- selector tie-break alignment with the live material behavior;
- existing local APIs still delegating through admin-only routes.

Server/test-environment verification should confirm:

- `/xuanpin/mk` video material tab no longer triggers bulk live Mingkong searches;
- cards render local covers when cached;
- cards still fall back to `/xuanpin/api/mk-media` when a cover cache miss occurs;
- video preview still uses `/xuanpin/api/mk-video` on demand;
- the scheduled task and run logs show a clear success or failure state.
