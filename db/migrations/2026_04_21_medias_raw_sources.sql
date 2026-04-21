-- 新增「原始去字幕素材」表，并在 media_items 上加 source_raw_id 溯源列

CREATE TABLE IF NOT EXISTS media_raw_sources (
  id INT AUTO_INCREMENT PRIMARY KEY,
  product_id INT NOT NULL,
  user_id   INT NOT NULL,
  display_name     VARCHAR(255) DEFAULT NULL,
  video_object_key VARCHAR(500) NOT NULL,
  cover_object_key VARCHAR(500) NOT NULL,
  duration_seconds FLOAT  DEFAULT NULL,
  file_size        BIGINT DEFAULT NULL,
  width            INT    DEFAULT NULL,
  height           INT    DEFAULT NULL,
  sort_order       INT    NOT NULL DEFAULT 0,
  created_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at       DATETIME DEFAULT NULL,
  KEY idx_product_deleted (product_id, deleted_at),
  KEY idx_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

SET @ddl := IF(
  EXISTS(
    SELECT 1
    FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'media_items'
      AND COLUMN_NAME = 'source_raw_id'
  ),
  'SELECT 1',
  'ALTER TABLE media_items ADD COLUMN source_raw_id INT NULL AFTER cover_object_key'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(
    SELECT 1
    FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'media_items'
      AND INDEX_NAME = 'idx_source_raw'
  ),
  'SELECT 1',
  'ALTER TABLE media_items ADD KEY idx_source_raw (source_raw_id)'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
