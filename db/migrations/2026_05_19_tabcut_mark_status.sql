-- Add local ok/bad annotations for Tabcut video and goods selections.
-- Docs-anchor: docs/superpowers/specs/2026-05-19-tabcut-mark-status-design.md

SET @ddl := IF(
  EXISTS(
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'tabcut_videos'
      AND COLUMN_NAME = 'mark_status'
  ),
  'SELECT 1',
  'ALTER TABLE tabcut_videos
     ADD COLUMN is_marked TINYINT(1) NOT NULL DEFAULT 0 AFTER raw_json,
     ADD COLUMN mark_status VARCHAR(16) NULL AFTER is_marked,
     ADD COLUMN marked_at DATETIME DEFAULT NULL AFTER mark_status,
     ADD COLUMN marked_by INT DEFAULT NULL AFTER marked_at,
     ADD KEY idx_tabcut_videos_mark_status (mark_status, last_seen_at)'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(
    SELECT 1 FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'tabcut_goods'
      AND COLUMN_NAME = 'mark_status'
  ),
  'SELECT 1',
  'ALTER TABLE tabcut_goods
     ADD COLUMN is_marked TINYINT(1) NOT NULL DEFAULT 0 AFTER raw_json,
     ADD COLUMN mark_status VARCHAR(16) NULL AFTER is_marked,
     ADD COLUMN marked_at DATETIME DEFAULT NULL AFTER mark_status,
     ADD COLUMN marked_by INT DEFAULT NULL AFTER marked_at,
     ADD KEY idx_tabcut_goods_mark_status (mark_status, last_seen_at)'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
