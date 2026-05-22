SET @ddl := IF(
  EXISTS(SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'mingkong_material_daily_snapshots' AND INDEX_NAME = 'idx_mk_material_search_at_product_code'),
  'SELECT 1',
  'ALTER TABLE mingkong_material_daily_snapshots ADD KEY idx_mk_material_search_at_product_code (snapshot_at, product_code)'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'mingkong_material_daily_snapshots' AND INDEX_NAME = 'idx_mk_material_search_at_video_name'),
  'SELECT 1',
  'ALTER TABLE mingkong_material_daily_snapshots ADD KEY idx_mk_material_search_at_video_name (snapshot_at, video_name)'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'mingkong_material_daily_snapshots' AND INDEX_NAME = 'idx_mk_material_search_at_product_name'),
  'SELECT 1',
  'ALTER TABLE mingkong_material_daily_snapshots ADD KEY idx_mk_material_search_at_product_name (snapshot_at, product_name)'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'mingkong_material_daily_snapshots' AND INDEX_NAME = 'idx_mk_material_search_at_mk_product_name'),
  'SELECT 1',
  'ALTER TABLE mingkong_material_daily_snapshots ADD KEY idx_mk_material_search_at_mk_product_name (snapshot_at, mk_product_name)'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'mingkong_material_daily_snapshots' AND INDEX_NAME = 'idx_mk_material_search_at_video_path'),
  'SELECT 1',
  'ALTER TABLE mingkong_material_daily_snapshots ADD KEY idx_mk_material_search_at_video_path (snapshot_at, video_path(191))'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'mingkong_material_daily_snapshots' AND INDEX_NAME = 'idx_mk_material_search_date_product_code'),
  'SELECT 1',
  'ALTER TABLE mingkong_material_daily_snapshots ADD KEY idx_mk_material_search_date_product_code (snapshot_date, product_code)'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'mingkong_material_daily_snapshots' AND INDEX_NAME = 'idx_mk_material_search_date_video_name'),
  'SELECT 1',
  'ALTER TABLE mingkong_material_daily_snapshots ADD KEY idx_mk_material_search_date_video_name (snapshot_date, video_name)'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
