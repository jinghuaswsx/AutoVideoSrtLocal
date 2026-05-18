# Mingkong Video Material Library Subtabs Design

## Goal

Restore the Mingkong-sourced video material browsing workflow inside `/xuanpin/mk`.
The page must expose two local subtabs:

- `产品库`: the existing Dianxiaomi ranking product table.
- `视频素材库`: a card list of high-spend Mingkong/wedev videos fetched from `/api/marketing/medias`.

This is not the local translated material library under `素材管理 -> 视频素材管理`.

## Data Source

The video material library uses existing wedev credentials and the Mingkong marketing media API:

1. Read the selected `dianxiaomi_rankings` snapshot, defaulting to the latest available date.
2. Derive the Shopify handle from each product URL.
3. Query `GET /api/marketing/medias?q=<handle>` with the synced wedev credentials.
4. Pick the best matching Mingkong product only from search-result products whose
   Mingkong-side `product_code`/`code`/`handle` or product-link tail exactly equals
   the requested product code. Do not strip `-rjc` from Mingkong search-result
   links as a match fallback.
5. If multiple exact Mingkong results remain, rank them by visible video spend,
   ad count, video count, and newer Mingkong id.
6. Flatten visible videos into cards, sorted per product by spend and ad count.

The UI must not depend on `dianxiaomi_rankings.mk_product_id` or the old denormalized Mingkong spend columns. The Dianxiaomi full-listing archive owns raw Listing sales rows only; Mingkong matching and spend data are enrichment state.

## UI

`/xuanpin/mk` keeps the existing selection-center top tabs. Under the top tabs it adds a second segmented tab bar:

- `产品库` shows the existing table unchanged.
- `视频素材库` shows Mingkong video cards with cover/video preview, product name, rank, sales, 90-day spend, ad count, uploader, upload time, and the existing `加入素材库` / `做小语种` actions where metadata is available.

Every product row in `产品库` also exposes a `素材库` button. Clicking it switches to `视频素材库` and loads card results by searching the Mingkong backend with that row's Shopify product code/handle. This row entry must work even when the ranking row has no stored `mk_product_id`.

The existing product detail modal card renderer remains available for rows that have a direct `mk_product_id`.

## API

Add `GET /xuanpin/api/mk-video-materials` for admins.

Query parameters:

- `page`: product-source page, default `1`.
- `page_size`: number of Dianxiaomi products to scan, default `24`, max `60`.
- `keyword`: optional product-name or handle filter.
- `product_code`: optional direct Mingkong search term for a single product row. When present, search `/api/marketing/medias?q=<product_code>` directly and do not require a local ranking row match.
- `snapshot`: optional `YYYY-MM-DD`; omitted means latest snapshot.
- `max_videos_per_product`: for normal paged scans, default `3`, max `5`; for direct
  `product_code` single-product jumps, default `24`, max `100`, so the product
  material view mirrors Mingkong's own edit dialog instead of truncating to the
  first few cards.

Response:

- `items`: flattened video-card rows.
- `stats`: scan counts and skipped counts for operator visibility.
- `page`, `page_size`, `has_more_products`.

## Verification

- Service tests cover live Mingkong search flattening without relying on stored `mk_product_id`.
- Route/page tests cover the new API and the two Mingkong subtabs.
- Focused verification runs the Mingkong selection and xuanpin route tests.
