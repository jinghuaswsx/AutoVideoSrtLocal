# TOS File Management Design

Last updated: 2026-05-15

## Goal

Add a superadmin-only "TOS文件管理" page that inventories protected local server
files, maps them to configured TOS channels, shows total counts/sizes by module,
and can trigger controlled WJ TOS sync/dry-run actions.

## Constraints

- Do not connect to Windows local MySQL. Inventory scan and persistence must
  use existing `appcore.db` patterns and run on the server environment.
- Do not commit secrets. Do not read or print the local `TOS` credential file.
- Do not change existing backup logic or scheduled jobs. This feature is an
  admin-facing visibility and manual control layer.
- First release focuses on "protected business files" only, not full bucket
  orphan detection.

## Design

### Data Model

Three new tables track scan state and file mappings:

1. `tos_file_scan_runs`: One row per scan execution, with summary statistics
2. `tos_file_mappings`: One row per local protected file per channel, with sync state
3. `tos_file_sync_runs`: One row per manual sync/dry-run operation

### Module Classification

The `ProtectedFileRef.sources` tuple is mapped to modules using this priority:

| Source                      | Module Code  | Module Name       | File Type |
|-----------------------------|--------------|-------------------|-----------|
| `project_video`             | `projects`   | 项目源视频        | `source_video` |
| `media_item`                | `media_items`| 素材库视频        | `video` |
| `media_item_cover`          | `media_items`| 素材库视频        | `cover` |
| `product_cover`             | `product_images` | 产品封面/详情图 | `cover` |
| `legacy_product_cover`      | `product_images` | 产品封面/详情图 | `cover` |
| `product_detail_image`      | `product_images` | 产品封面/详情图 | `detail_image` |
| `raw_source_video`          | `raw_sources` | 原始素材        | `video` |
| `raw_source_cover`          | `raw_sources` | 原始素材        | `cover` |
| `raw_source_translation_cover` | `raw_sources` | 原始素材翻译封面 | `cover` |

When a file has multiple sources, pick the first one by the priority above
and keep all source labels in `source_labels_json`.

### Service Layer (`appcore.tos_file_management`)

Core functions:

- `build_inventory_rows(target_channel_code)`: Collect refs, classify modules,
  check local file existence/size, check target object existence/size via TOS
  `head_object`, and return a list of `TosFileInventoryRow` dataclass instances.
- `run_inventory_scan(target_channel_code, triggered_by)`: Run a full inventory
  scan, persist a `tos_file_scan_runs` row and upsert `tos_file_mappings` rows.
- `latest_scan_summary(target_channel_code)`: Return the most recent scan summary
  with total counts/sizes and module-level breakdown.
- `list_mappings(filters)`: Paginate through `tos_file_mappings` with optional
  filters by module, status, or search query.
- `upsert_mapping(row, scan_run_id)`: Insert or update a single mapping row using
  `target_channel_code` + `local_path_hash` as unique key.
- `run_channel_sync(target_channel_code, dry_run, module_code, triggered_by)`:
  Run a sync operation (wrapper around `tos_channel_migration.run_channel_backup`).

### Web Routes (`web.routes.tos_file_management`)

Blueprint routes:

- `GET /admin/tos-files`: Render the admin page (requires superadmin)
- `POST /admin/tos-files/scan`: Trigger inventory scan (form: `channel`)
- `POST /admin/tos-files/sync`: Trigger sync/dry-run (form: `channel`, `dry_run`, `module_code`)
- `GET /admin/tos-files/api/files`: JSON API for table pagination
- `GET /admin/tos-files/export.csv`: Optional CSV export (post-MVP)

### UI Layout

1. **Top Bar**: TOS channel switcher (`tos_main` / `tos_wj`), last scan time,
   quick actions (Scan, Dry-run Sync, Real Sync).
2. **Summary Cards**:
   - Total protected files
   - Total local size (GB)
   - Target existing objects
   - Missing local files
   - Missing target objects
   - Failed files
3. **Module Breakdown Table**: Module name, file count, total GB, target existing
   count/GB, missing count, failed count, sync rate.
4. **File List Table (paginated)**: Module, file type/source labels, local path,
   local size, target object key, status, error, last seen/synced time.

### Sync Status Values

| Status           | Meaning |
|------------------|---------|
| `unknown`        | Not yet scanned |
| `synced`         | Local exists and target object exists (size match or acceptable mismatch) |
| `missing_target` | Local exists but target object is absent |
| `missing_local`  | Local file is absent |
| `failed`         | Scan/sync failed for this file |

### Scheduled Task Registration

Add a manual task definition to `appcore.scheduled_tasks`:

- Code: `tos_file_inventory_scan`
- Name: `TOS文件管理资产扫描`
- Group: `management`
- Description: `扫描受保护业务文件并更新 TOS 文件映射表；默认手动触发。`

Do NOT enable periodic execution by default.

## Out of Scope (Future Phases)

- Full TOS bucket inventory and orphan detection
- Background async scan with progress polling
- Per-file retry buttons
- Module-level partial sync (first release only supports full protected-ref sync)

## Verification

- Unit tests for inventory row building with mocked `collect_protected_file_refs`
- Unit tests for scan persistence with mocked `db.execute/query`
- Unit tests for route auth and rendering
- Manual server verification: `/admin/tos-files` loads for superadmin
