# Mingkong Video Material Library Subtabs Design

## Goal

Restore the Mingkong-sourced video material browsing workflow inside `/xuanpin/mk`.
The page must expose two local subtabs:

- `产品库`: the existing Dianxiaomi ranking product table.
- `视频素材库`: a card list of high-spend Mingkong/wedev videos fetched from `/api/marketing/medias`.

This is not the local translated material library under `素材管理 -> 视频素材管理`.

## Data Source

The video material library uses existing wedev credentials and the Mingkong marketing media API:

1. Read the latest `dianxiaomi_rankings` snapshot.
2. Derive the Shopify handle from each product URL.
3. Query `GET /api/marketing/medias?q=<handle>` with the synced wedev credentials.
4. Pick the best matching Mingkong product by exact product link tail first, then by visible video spend, ad count, and newer Mingkong id.
5. Flatten visible videos into cards, sorted per product by spend and ad count.

The UI must not depend on `dianxiaomi_rankings.mk_product_id` or the old denormalized Mingkong spend columns, because the current Top1000 ranking sync does not populate those fields.

## UI

`/xuanpin/mk` keeps the existing selection-center top tabs. Under the top tabs it adds a second segmented tab bar:

- `产品库` shows the existing table unchanged.
- `视频素材库` shows Mingkong video cards with cover/video preview, product name, rank, sales, 90-day spend, ad count, uploader, upload time, and the existing `加入素材库` / `做小语种` actions where metadata is available.

The existing product detail modal card renderer remains available for rows that have a direct `mk_product_id`.

## API

Add `GET /xuanpin/api/mk-video-materials` for admins.

Query parameters:

- `page`: product-source page, default `1`.
- `page_size`: number of Dianxiaomi products to scan, default `24`, max `60`.
- `keyword`: optional product-name or handle filter.
- `max_videos_per_product`: default `3`, max `5`.

Response:

- `items`: flattened video-card rows.
- `stats`: scan counts and skipped counts for operator visibility.
- `page`, `page_size`, `has_more_products`.

## Verification

- Service tests cover live Mingkong search flattening without relying on stored `mk_product_id`.
- Route/page tests cover the new API and the two Mingkong subtabs.
- Focused verification runs the Mingkong selection and xuanpin route tests.
