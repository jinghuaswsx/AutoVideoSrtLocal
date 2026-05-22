# Mingkong Video Material Detail Page Design

Last updated: 2026-05-22

## Context

`/xuanpin/mk` shows Mingkong video materials as dense cards in `视频素材库` and
`昨天消耗前100`. The card preview is optimized for scanning, so the cover and video
share a small tabbed frame. Operators need a per-material page that can be shared in
conversation, opened from the card, and used to inspect the material with larger media
and historical spend snapshots.

## Anchors

- `AGENTS.md#文档驱动代码`: new behavior is documented before code changes.
- `docs/superpowers/specs/2026-05-18-mingkong-daily-material-snapshot-top100-design.md#Material Identity`:
  the stable material identity is `material_key`, derived from product code, Mingkong
  product id, and normalized video path.
- `docs/superpowers/specs/2026-05-20-mingkong-card-material-ad-status-design.md#UI`:
  Mingkong material cards share the local archive renderer and may expose material
  actions.
- `web/static/CLAUDE.md#Ocean Blue 设计系统`: the detail page follows the existing
  management UI visual system and avoids new decorative palettes.

## Scope

First version only:

1. Add an admin-only route `GET /xuanpin/mk/videos/<material_key>`.
2. Use `material_key` as the route identity and load the latest archived snapshot row
   for that material.
3. Render a dedicated detail page with:
   - product name, product code, Mingkong product link, material filename, author,
     upload time, ad count, 90-day spend, and local library status when available;
   - cover image and video player shown side by side in larger frames;
   - a historical sync section listing each snapshot time, 90-day spend, delta from
     the previous snapshot, ad count, and snapshot slot.
4. Add a card-level entry link from both material card tabs to the new detail route.
5. Keep all media URLs routed through existing `/xuanpin/api/mk-media` and
   `/xuanpin/api/mk-video` proxies, or existing local cover object URLs.

Out of scope for the first version:

- Do not add new database tables or migrations.
- Do not call live Mingkong APIs from the detail page.
- Do not add charting libraries; use a table and small inline bars only if needed.
- Do not change card sorting, pagination, import, translation, or AI evaluation behavior.

## Data Contract

`appcore.mingkong_materials.get_material_detail(material_key)` returns `None` if the
material is not archived. Otherwise it returns:

- `material`: serialized latest row using the same shape as material card APIs.
- `history`: chronological rows for the same `material_key`.
- `summary`: counts and min/max spend values for display.

History rows include:

- `snapshot_date`
- `snapshot_at`
- `snapshot_slot`
- `cumulative_90_spend`
- `spend_delta`
- `video_ads_count`

The route passes this data directly to `web/templates/mk_video_material_detail.html`.

## UI Behavior

- The page keeps the selection-center header and top tabs with `明空选品` active.
- A compact back link returns to `/xuanpin/mk#videos`.
- The media section uses two equal columns on desktop:
  - left: cover image;
  - right: video player with controls.
- The cover and video frames use a fixed `270px` by `480px` centered portrait viewport
  inside each media panel, so the detail page does not stretch media across the full
  right-side panel.
- Detail-page CSS is emitted directly inside `layout.html`'s `extra_style` stylesheet
  block; the template must not add a nested `<style>` tag because that prevents the
  media-size CSS variables from applying in browsers.
- On narrow screens the two media panes stack.
- The card entry opens in a new tab so operators do not lose the scanned list position.
- On material cards, the filename action column shows `详情` above the filename-copy
  icon so the copy icon does not sit on the same baseline as the title text.
- The history spend table uses a compact left-aligned reading width instead of
  stretching columns across the full page. Numeric headers and cells align right.

## Verification

Run focused tests:

```bash
pytest tests/test_mingkong_materials.py tests/test_xuanpin_routes.py tests/test_mk_selection_routes.py -q
```

Manual browser smoke for the first version:

1. Open `/xuanpin/mk`.
2. Confirm a material card shows a `详情` entry.
3. Open the entry and confirm cover/video are visible side by side on desktop.
4. Confirm the history table renders snapshot rows.
