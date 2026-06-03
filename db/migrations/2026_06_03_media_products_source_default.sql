-- db/migrations/2026_06_03_media_products_source_default.sql
ALTER TABLE media_products ALTER COLUMN source SET DEFAULT '明空';
UPDATE media_products SET source = '明空' WHERE source IS NULL OR TRIM(source) = '';
