-- DDL for TABCUT search indexes
-- Tabcut videos search indexes
SET @ddl1 := IF(
  EXISTS(
    SELECT 1 FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'tabcut_videos'
      AND INDEX_NAME = 'idx_tabcut_videos_author_name'
  ),
  'SELECT 1',
  'ALTER TABLE tabcut_videos ADD INDEX idx_tabcut_videos_author_name (author_name)'
);
PREPARE stmt1 FROM @ddl1;
EXECUTE stmt1;
DEALLOCATE PREPARE stmt1;

SET @ddl2 := IF(
  EXISTS(
    SELECT 1 FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'tabcut_videos'
      AND INDEX_NAME = 'idx_tabcut_videos_video_desc'
  ),
  'SELECT 1',
  'ALTER TABLE tabcut_videos ADD INDEX idx_tabcut_videos_video_desc (video_desc(255))'
);
PREPARE stmt2 FROM @ddl2;
EXECUTE stmt2;
DEALLOCATE PREPARE stmt2;

SET @ddl3 := IF(
  EXISTS(
    SELECT 1 FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'tabcut_videos'
      AND INDEX_NAME = 'idx_tabcut_videos_primary_item_name'
  ),
  'SELECT 1',
  'ALTER TABLE tabcut_videos ADD INDEX idx_tabcut_videos_primary_item_name (primary_item_name(255))'
);
PREPARE stmt3 FROM @ddl3;
EXECUTE stmt3;
DEALLOCATE PREPARE stmt3;

-- Tabcut goods search indexes
SET @ddl4 := IF(
  EXISTS(
    SELECT 1 FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'tabcut_goods'
      AND INDEX_NAME = 'idx_tabcut_goods_item_name'
  ),
  'SELECT 1',
  'ALTER TABLE tabcut_goods ADD INDEX idx_tabcut_goods_item_name (item_name(255))'
);
PREPARE stmt4 FROM @ddl4;
EXECUTE stmt4;
DEALLOCATE PREPARE stmt4;

SET @ddl5 := IF(
  EXISTS(
    SELECT 1 FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'tabcut_goods'
      AND INDEX_NAME = 'idx_tabcut_goods_seller_name'
  ),
  'SELECT 1',
  'ALTER TABLE tabcut_goods ADD INDEX idx_tabcut_goods_seller_name (seller_name)'
);
PREPARE stmt5 FROM @ddl5;
EXECUTE stmt5;
DEALLOCATE PREPARE stmt5;
