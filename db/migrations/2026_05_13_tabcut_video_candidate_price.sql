-- Persist Tabcut video candidate primary item price for backend filtering.
-- Spec: docs/superpowers/specs/2026-05-13-tabcut-video-price-filter-design.md

ALTER TABLE tabcut_video_candidates
  ADD COLUMN primary_item_price_min DECIMAL(18, 4) NULL AFTER primary_item_id,
  ADD COLUMN primary_item_price_max DECIMAL(18, 4) NULL AFTER primary_item_price_min,
  ADD COLUMN price_currency VARCHAR(16) NULL AFTER primary_item_price_max,
  ADD KEY idx_tabcut_video_candidates_price (biz_date, region, primary_item_price_min);
