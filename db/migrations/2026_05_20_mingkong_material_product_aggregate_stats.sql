-- Product-level Mingkong aggregate stats for /xuanpin/mk#products.
-- Docs-anchor: docs/superpowers/specs/2026-05-20-mingkong-product-local-aggregate-stats-design.md

SET @ddl := IF(
  EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'mingkong_material_products' AND COLUMN_NAME = 'video_count'),
  'SELECT 1',
  'ALTER TABLE mingkong_material_products ADD COLUMN video_count INT NOT NULL DEFAULT 0 AFTER material_count'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'mingkong_material_products' AND COLUMN_NAME = 'path_video_count'),
  'SELECT 1',
  'ALTER TABLE mingkong_material_products ADD COLUMN path_video_count INT NOT NULL DEFAULT 0 AFTER video_count'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'mingkong_material_products' AND COLUMN_NAME = 'total_90_spend'),
  'SELECT 1',
  'ALTER TABLE mingkong_material_products ADD COLUMN total_90_spend DECIMAL(14,2) NOT NULL DEFAULT 0 AFTER path_video_count'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'mingkong_material_products' AND COLUMN_NAME = 'total_ads'),
  'SELECT 1',
  'ALTER TABLE mingkong_material_products ADD COLUMN total_ads INT NOT NULL DEFAULT 0 AFTER total_90_spend'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

UPDATE mingkong_material_products p
JOIN (
  SELECT run_id, product_code, COUNT(*) AS path_count,
         COALESCE(SUM(cumulative_90_spend), 0) AS path_spend,
         COALESCE(SUM(video_ads_count), 0) AS path_ads
  FROM mingkong_material_daily_snapshots
  GROUP BY run_id, product_code
) s ON s.run_id = p.run_id AND s.product_code = p.product_code
SET p.video_count = CASE WHEN p.video_count = 0 THEN s.path_count ELSE p.video_count END,
    p.path_video_count = CASE WHEN p.path_video_count = 0 THEN s.path_count ELSE p.path_video_count END,
    p.total_90_spend = CASE WHEN p.total_90_spend = 0 THEN s.path_spend ELSE p.total_90_spend END,
    p.total_ads = CASE WHEN p.total_ads = 0 THEN s.path_ads ELSE p.total_ads END
WHERE p.status = 'success';

SET @ddl := IF(
  EXISTS(SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'mingkong_material_products' AND INDEX_NAME = 'idx_mk_material_products_latest_stats'),
  'SELECT 1',
  'ALTER TABLE mingkong_material_products ADD KEY idx_mk_material_products_latest_stats (snapshot_at, status, product_code)'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
