# Mingkong Card Material Ad Status Design

Last updated: 2026-05-20

## Context

`/xuanpin/mk` has two Mingkong material-card views:

- `视频素材库`
- `昨天消耗前100`

Operators need each card to show whether the product and the exact video material are
already present in the local material library and have advertising activity. The status
must be cheap to render. Card-list APIs must not scan `media_products`, `media_items`,
`media_item_mk_bindings`, `media_push_logs`, or Meta ad fact tables per request.

## Anchors

- `AGENTS.md#文档驱动代码`: new behavior is documented before code changes.
- `docs/superpowers/specs/2026-05-18-mingkong-daily-material-snapshot-top100-design.md#api-and-ui`:
  both card tabs read local archived rows and share the same card renderer.
- `docs/superpowers/specs/2026-05-13-media-video-material-bindings-design.md#scope`:
  material-level ad plan status is based on a local material item having successful
  push evidence.
- `docs/superpowers/specs/2026-04-18-push-management-design.md#33-状态机动态计算`:
  successful push evidence is `media_items.pushed_at IS NOT NULL` or a successful
  `media_push_logs` row.
- `web/static/CLAUDE.md#Ocean Blue 设计系统`: card icons and buttons must stay inside
  the existing Ocean Blue visual system and avoid emoji in buttons.

## Scope

In scope:

1. Add a materialized status cache table for Mingkong card badges.
2. Add a scheduled refresh task that maintains product-level and video-material-level
   status cache rows.
3. Enrich `/xuanpin/api/mk-material-library` and `/xuanpin/api/mk-yesterday-top100`
   response items from that cache only.
4. Render two top-right card icons only when their status is true.
5. Add a search icon button after the product code line that opens
   `/medias/?q=<product_code-rjc>`.

Out of scope:

- Do not query live status tables from card-list requests.
- Do not add new Mingkong API calls.
- Do not change material import, push creation, or Meta sync behavior.
- Do not connect to Windows local MySQL for verification.

## Status Semantics

### Product Status

For each Mingkong card row, normalize its product code for local material-library search:

```text
media_search_code = lower(product_code without trailing -rjc/_rjc) + "-rjc"
```

The product icon is shown only when both are true in the cache:

- `media_products.product_code = media_search_code` exists and is not deleted.
- The matched local product has Meta ad spend evidence in recent synced ad fact data.

The refresh task may use `meta_ad_daily_campaign_metrics.product_id` and/or recent
realtime campaign code evidence to compute this, but the card-list request only reads
the cache result.

### Video Material Status

For each Mingkong card row, normalize `video_path` with the same path normalization used
by Mingkong material bindings.

The video-material icon is shown only when both are true in the cache:

- A local `media_items` row is bound to that Mingkong `video_path` through
  `media_item_mk_bindings` and is not deleted.
- That local item has successful push evidence: `media_items.pushed_at IS NOT NULL` or
  a successful `media_push_logs` row.

## Data Model

Add `mingkong_material_ad_status_cache`.

Required fields:

- `id`
- `status_scope`: `product` or `material`
- `lookup_hash`: SHA-256 of the normalized lookup key.
- `lookup_key`: normalized product code or normalized Mingkong video path.
- `product_code`
- `media_product_id`
- `media_item_id`
- `has_local_match`
- `has_running_ad`
- `ad_spend_usd`
- `latest_activity_at`
- `summary_json`
- `refreshed_at`
- `created_at`
- `updated_at`

Unique key:

- `(status_scope, lookup_hash)`

Indexes:

- `(status_scope, product_code)`
- `(media_product_id)`
- `(media_item_id)`
- `(refreshed_at)`

## Scheduled Task

Register one APScheduler task:

- Code: `mingkong_material_ad_status_refresh`
- Name: `明空素材投放状态缓存`
- Schedule: every 10 minutes.
- Source type: `apscheduler`.
- Source ref: `mingkong_material_ad_status_refresh`
- Runner: `appcore.mingkong_materials.refresh_ad_status_cache`
- Log table: `scheduled_task_runs`

The task:

1. Reads distinct product codes and video paths from the current Mingkong local material
   archive tables.
2. Upserts product status cache rows keyed by `media_search_code`.
3. Upserts material status cache rows keyed by normalized Mingkong `video_path`.
4. Stores summary counters in `scheduled_task_runs.summary_json`.
5. On failure, records a failed `scheduled_task_runs` row.

## API Payload

Each card item from both archive APIs includes:

- `media_search_code`
- `media_search_url`
- `has_local_product_running_ad`
- `has_local_material_running_ad`
- `product_ad_status`
- `material_ad_status`

`product_ad_status` and `material_ad_status` are small objects with ids, booleans,
latest activity time, spend, and `refreshed_at` for diagnostics. The frontend only needs
the two top-level booleans and `media_search_url`.

## UI

`renderMkVideoMaterialCard()` renders an absolute top-right status cluster:

- Product status icon: shown when `has_local_product_running_ad` is true.
- Material status icon: shown when `has_local_material_running_ad` is true.

The icons use inline SVG symbols already available in the page style rather than emoji.
No placeholder is shown when a status is false.

The second line of the product header keeps the rank and product code, and appends a
small search icon link button. The button opens:

```text
/medias/?q=<media_search_code>
```

For example:

```text
/medias/?q=multifunctional-roadside-safety-light-rjc
```

## Verification

Focused automated checks:

- Migration test covers `mingkong_material_ad_status_cache`.
- Service tests cover product-code normalization, status cache upsert, cache payload
  enrichment, and that list APIs enrich from cache fields.
- Scheduler tests cover task registration.
- Template tests cover status icon rendering hooks and the `/medias/?q=` search link.

Manual checks:

- Unauthenticated `GET /xuanpin/mk` returns 302.
- Logged-in admin `GET /xuanpin/mk` returns 200.
- After a status refresh, both material-card tabs show icons only for cached true states.
- Product-code search button opens the素材管理 search page with the `-rjc` code.
