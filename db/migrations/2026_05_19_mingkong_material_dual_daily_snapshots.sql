-- Allow Mingkong material archives to keep two snapshots per day (06:00 and 18:00).
-- Docs-anchor: docs/superpowers/specs/2026-05-18-mingkong-daily-material-snapshot-top100-design.md

SET @ddl := IF(
  EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'mingkong_material_sync_runs' AND COLUMN_NAME = 'snapshot_at'),
  'SELECT 1',
  'ALTER TABLE mingkong_material_sync_runs ADD COLUMN snapshot_at DATETIME NULL AFTER snapshot_date'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'mingkong_material_sync_runs' AND COLUMN_NAME = 'snapshot_slot'),
  'SELECT 1',
  'ALTER TABLE mingkong_material_sync_runs ADD COLUMN snapshot_slot VARCHAR(8) NULL AFTER snapshot_at'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'mingkong_material_products' AND COLUMN_NAME = 'snapshot_at'),
  'SELECT 1',
  'ALTER TABLE mingkong_material_products ADD COLUMN snapshot_at DATETIME NULL AFTER snapshot_date'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'mingkong_material_products' AND COLUMN_NAME = 'snapshot_slot'),
  'SELECT 1',
  'ALTER TABLE mingkong_material_products ADD COLUMN snapshot_slot VARCHAR(8) NULL AFTER snapshot_at'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'mingkong_material_daily_snapshots' AND COLUMN_NAME = 'snapshot_at'),
  'SELECT 1',
  'ALTER TABLE mingkong_material_daily_snapshots ADD COLUMN snapshot_at DATETIME NULL AFTER snapshot_date'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'mingkong_material_daily_snapshots' AND COLUMN_NAME = 'snapshot_slot'),
  'SELECT 1',
  'ALTER TABLE mingkong_material_daily_snapshots ADD COLUMN snapshot_slot VARCHAR(8) NULL AFTER snapshot_at'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'mingkong_material_daily_top100' AND COLUMN_NAME = 'snapshot_at'),
  'SELECT 1',
  'ALTER TABLE mingkong_material_daily_top100 ADD COLUMN snapshot_at DATETIME NULL AFTER snapshot_date'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'mingkong_material_daily_top100' AND COLUMN_NAME = 'snapshot_slot'),
  'SELECT 1',
  'ALTER TABLE mingkong_material_daily_top100 ADD COLUMN snapshot_slot VARCHAR(8) NULL AFTER snapshot_at'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'mingkong_material_daily_top100' AND COLUMN_NAME = 'previous_snapshot_at'),
  'SELECT 1',
  'ALTER TABLE mingkong_material_daily_top100 ADD COLUMN previous_snapshot_at DATETIME NULL AFTER previous_snapshot_date'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'mingkong_material_daily_top100' AND COLUMN_NAME = 'previous_snapshot_slot'),
  'SELECT 1',
  'ALTER TABLE mingkong_material_daily_top100 ADD COLUMN previous_snapshot_slot VARCHAR(8) NULL AFTER previous_snapshot_at'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'mingkong_material_daily_top100' AND COLUMN_NAME = 'comparison_interval_seconds'),
  'SELECT 1',
  'ALTER TABLE mingkong_material_daily_top100 ADD COLUMN comparison_interval_seconds INT NULL AFTER previous_snapshot_slot'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

UPDATE mingkong_material_sync_runs
SET snapshot_at = COALESCE(snapshot_at, started_at, CAST(CONCAT(snapshot_date, ' 06:00:00') AS DATETIME))
WHERE snapshot_at IS NULL;

UPDATE mingkong_material_sync_runs
SET snapshot_slot = CASE WHEN HOUR(snapshot_at) < 12 THEN '0600' ELSE '1800' END
WHERE snapshot_slot IS NULL OR snapshot_slot = '';

UPDATE mingkong_material_products p
JOIN mingkong_material_sync_runs r ON r.id = p.run_id
SET p.snapshot_at = COALESCE(p.snapshot_at, r.snapshot_at),
    p.snapshot_slot = COALESCE(NULLIF(p.snapshot_slot, ''), r.snapshot_slot)
WHERE p.snapshot_at IS NULL OR p.snapshot_slot IS NULL OR p.snapshot_slot = '';

UPDATE mingkong_material_products p
JOIN mingkong_material_sync_runs r ON r.snapshot_date = p.snapshot_date
SET p.snapshot_at = COALESCE(p.snapshot_at, r.snapshot_at),
    p.snapshot_slot = COALESCE(NULLIF(p.snapshot_slot, ''), r.snapshot_slot)
WHERE p.snapshot_at IS NULL OR p.snapshot_slot IS NULL OR p.snapshot_slot = '';

UPDATE mingkong_material_daily_snapshots s
JOIN mingkong_material_sync_runs r ON r.id = s.run_id
SET s.snapshot_at = COALESCE(s.snapshot_at, r.snapshot_at),
    s.snapshot_slot = COALESCE(NULLIF(s.snapshot_slot, ''), r.snapshot_slot)
WHERE s.snapshot_at IS NULL OR s.snapshot_slot IS NULL OR s.snapshot_slot = '';

UPDATE mingkong_material_daily_snapshots s
JOIN mingkong_material_sync_runs r ON r.snapshot_date = s.snapshot_date
SET s.snapshot_at = COALESCE(s.snapshot_at, r.snapshot_at),
    s.snapshot_slot = COALESCE(NULLIF(s.snapshot_slot, ''), r.snapshot_slot)
WHERE s.snapshot_at IS NULL OR s.snapshot_slot IS NULL OR s.snapshot_slot = '';

UPDATE mingkong_material_daily_top100 t
JOIN mingkong_material_sync_runs r ON r.snapshot_date = t.snapshot_date
SET t.snapshot_at = COALESCE(t.snapshot_at, r.snapshot_at),
    t.snapshot_slot = COALESCE(NULLIF(t.snapshot_slot, ''), r.snapshot_slot)
WHERE t.snapshot_at IS NULL OR t.snapshot_slot IS NULL OR t.snapshot_slot = '';

UPDATE mingkong_material_daily_top100 t
JOIN mingkong_material_sync_runs r ON r.snapshot_date = t.previous_snapshot_date
SET t.previous_snapshot_at = COALESCE(t.previous_snapshot_at, r.snapshot_at),
    t.previous_snapshot_slot = COALESCE(NULLIF(t.previous_snapshot_slot, ''), r.snapshot_slot)
WHERE t.previous_snapshot_date IS NOT NULL
  AND (t.previous_snapshot_at IS NULL OR t.previous_snapshot_slot IS NULL OR t.previous_snapshot_slot = '');

UPDATE mingkong_material_daily_top100
SET comparison_interval_seconds = TIMESTAMPDIFF(SECOND, previous_snapshot_at, snapshot_at)
WHERE comparison_interval_seconds IS NULL
  AND previous_snapshot_at IS NOT NULL
  AND snapshot_at IS NOT NULL;

SET @ddl := IF(
  EXISTS(SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'mingkong_material_sync_runs' AND INDEX_NAME = 'uk_mk_material_run_snapshot'),
  'ALTER TABLE mingkong_material_sync_runs DROP INDEX uk_mk_material_run_snapshot',
  'SELECT 1'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'mingkong_material_daily_snapshots' AND INDEX_NAME = 'uk_mk_material_snapshot_material'),
  'ALTER TABLE mingkong_material_daily_snapshots DROP INDEX uk_mk_material_snapshot_material',
  'SELECT 1'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'mingkong_material_daily_top100' AND INDEX_NAME = 'uk_mk_material_top100_material'),
  'ALTER TABLE mingkong_material_daily_top100 DROP INDEX uk_mk_material_top100_material',
  'SELECT 1'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'mingkong_material_sync_runs' AND INDEX_NAME = 'uk_mk_material_run_snapshot_slot'),
  'SELECT 1',
  'ALTER TABLE mingkong_material_sync_runs ADD UNIQUE KEY uk_mk_material_run_snapshot_slot (snapshot_date, snapshot_slot)'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'mingkong_material_sync_runs' AND INDEX_NAME = 'idx_mk_material_run_snapshot_at'),
  'SELECT 1',
  'ALTER TABLE mingkong_material_sync_runs ADD KEY idx_mk_material_run_snapshot_at (snapshot_at)'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'mingkong_material_daily_snapshots' AND INDEX_NAME = 'uk_mk_material_snapshot_at_material'),
  'SELECT 1',
  'ALTER TABLE mingkong_material_daily_snapshots ADD UNIQUE KEY uk_mk_material_snapshot_at_material (snapshot_at, material_key)'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'mingkong_material_daily_top100' AND INDEX_NAME = 'uk_mk_material_top100_snapshot_at_material'),
  'SELECT 1',
  'ALTER TABLE mingkong_material_daily_top100 ADD UNIQUE KEY uk_mk_material_top100_snapshot_at_material (snapshot_at, material_key)'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
