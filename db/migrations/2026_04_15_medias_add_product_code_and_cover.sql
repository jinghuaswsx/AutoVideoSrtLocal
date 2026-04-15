-- db/migrations/2026_04_15_medias_add_product_code_and_cover.sql
ALTER TABLE media_products
  ADD COLUMN product_code VARCHAR(64) NULL AFTER name,
  ADD COLUMN cover_object_key VARCHAR(255) NULL AFTER source,
  ADD UNIQUE KEY uk_media_products_product_code (product_code);
