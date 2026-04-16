# Media TOS Bucket Migration Design

## Goal

Move all material-management images, videos, and covers to the dedicated TOS
bucket `auto-video-srt-product-video-manage`, provide a one-time migration flow
for existing data, and separate destructive cleanup into a second confirmed
phase.

## Confirmed Requirements

1. All future material-management uploads must use the dedicated bucket
   `auto-video-srt-product-video-manage`.
2. Existing material objects must be migrated by downloading to local temporary
   storage and re-uploading to the new bucket.
3. After migration is verified, old TOS objects and local temporary files must
   be cleaned up in a separate step.
4. Material-management API contracts and stored `object_key` values should stay
   stable if possible.
5. The migration must be safe to rerun and must not stop the whole batch on a
   single-object failure.

## Scope

### In Scope

- Material-management TOS reads and writes in the web app.
- Existing database references used by the material-management module:
  - `media_items.object_key`
  - `media_items.cover_object_key`
  - `media_product_covers.object_key`
  - compatibility scan for legacy `media_products.cover_object_key`
- One-time migration tooling with dry-run, apply, and cleanup modes.
- Cleanup of migration temporary files and material-management local cache
  files after migration is validated.

### Out of Scope

- General pipeline uploads that still belong to the non-media TOS bucket.
- Schema changes that add per-record bucket names.
- Dual-bucket long-term compatibility logic.

## Options Considered

### Option 1: Explicit Migration Script (Recommended)

Switch material-management code to the new bucket and add a dedicated migration
command that scans database references, downloads each source object locally,
uploads the same key to the new bucket, verifies the result, and reports
successes and failures. Old-bucket deletion and local cleanup run later as a
separate confirmed action.

Why this is recommended:

- clear cutover boundary
- easiest to validate before deletion
- rerunnable and resumable without muddying runtime code paths
- matches the requested two-phase cleanup workflow

### Option 2: Dual-Bucket Read Compatibility

Write all new objects to the new bucket while runtime reads fall back to the
old bucket until a background migration finishes.

Why this is not recommended:

- adds permanent complexity to runtime media reads
- delays cleanup and increases tech debt
- solves a rollout problem the user did not ask for

### Option 3: Config Switch First, Then Bulk Copy

Point runtime code at the new bucket immediately and migrate objects after the
switch.

Why this is not recommended:

- existing rows only store object keys, not bucket names
- old objects may become unreadable during the migration window
- risky for online traffic and hard to recover cleanly

## Recommended Design

### 1. Bucket Ownership

- Keep the existing general TOS bucket logic unchanged.
- Make the material-management module consistently resolve its media bucket to
  `auto-video-srt-product-video-manage`.
- Preserve the existing `TOS_MEDIA_BUCKET` configuration entry, but update its
  default value to the dedicated bucket so deployment stays configuration-driven
  and backwards-compatible with existing environment loading.

### 2. Database Strategy

- Do not add a new bucket column.
- Keep stored `object_key` values unchanged.
- Migrate by copying objects to the new bucket under the same key.

This works because current material records reference keys such as
`<user_id>/medias/<product_id>/<filename>`. Once runtime reads point at the new
bucket, existing rows continue to resolve correctly as long as the same keys
exist in the new bucket.

### 3. Runtime Code Changes

- Extend the media-specific TOS helpers so migration code can target an explicit
  bucket for `head`, `download`, `upload`, and `delete` operations.
- Keep current material-management routes using the normal media-bucket helpers;
  they should not need dual-bucket conditionals.
- Add a focused data-access method in the media module to enumerate all distinct
  material object references for migration and cleanup.

### 4. Migration Command

Provide a dedicated command, for example:

```bash
python -m scripts.migrate_media_tos_bucket --dry-run
python -m scripts.migrate_media_tos_bucket --apply
python -m scripts.migrate_media_tos_bucket --cleanup-remote
python -m scripts.migrate_media_tos_bucket --cleanup-local
```

Command behavior:

- `--dry-run`
  - scan all in-scope database references
  - normalize and deduplicate object keys
  - report totals, empty values, and obvious issues without changing data
- `--apply`
  - for each unique object key, download from the old media bucket to a local
    temporary directory
  - upload the file to the new media bucket using the same object key
  - verify that the new object exists and matches source size metadata when
    available
  - mark the object as migrated in the report
- `--cleanup-remote`
  - delete only the successfully migrated objects from the old media bucket
- `--cleanup-local`
  - delete migration temporary files
  - clear material-management local cache files such as
    `output/media_thumbs/...` so later reads repopulate from the new bucket

## Migration Workflow

### Apply Phase

1. Load configuration for old media bucket and new dedicated bucket.
2. Scan database references and build a unique object inventory.
3. For each object:
   - skip empty or malformed keys and report them
   - if the object already exists in the new bucket and passes verification,
     treat it as already migrated
   - otherwise download from the old bucket into a temporary local file
   - upload to the new bucket using the same key
   - verify existence and metadata in the new bucket
4. Emit a summary with:
   - total referenced keys
   - unique keys
   - migrated keys
   - skipped keys
   - missing source keys
   - failed keys

### Cleanup Phase

Cleanup must only happen after migration verification and operator confirmation.

1. Delete old-bucket objects only for keys that were confirmed as migrated.
2. Remove migration temporary files.
3. Remove local material cache files so the application repopulates from the
   dedicated bucket.

## Failure Handling And Idempotency

- A single-object failure must not abort the full batch.
- The script must log the failing key and reason, continue, and return a
  non-zero exit code if any failures remain.
- Re-running `--apply` should skip keys already present in the new bucket when
  verification passes.
- Cleanup should only target keys recorded as migrated successfully; it must not
  assume the whole batch succeeded.

## Verification Plan

### Automated Verification

- Add tests for inventory scanning, deduplication, migration skipping,
  successful copy flow, missing-source handling, and cleanup selection.
- Update media route and OpenAPI tests to ensure signed download URLs still come
  from the configured media bucket after the default changes.

### Operational Verification

1. Run `--dry-run` and inspect totals before any data movement.
2. Run `--apply` and review the generated report.
3. Spot-check several products:
   - play URL generation
   - product cover retrieval
   - item cover retrieval
   - page reload after clearing `output/media_thumbs`
4. Only after spot checks pass, run remote cleanup and local cleanup.

## Risks

- Some database references may point to objects already missing from the old
  bucket; these must be reported, not hidden.
- Local cache cleanup can temporarily remove thumbnails until the app lazily
  repopulates them from the new bucket.
- If old and new bucket credentials are misconfigured, migration may partially
  succeed; clear per-object reporting is required to support reruns.
