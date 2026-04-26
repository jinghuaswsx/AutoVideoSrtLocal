-- B 子系统：新品审核决策字段
-- 复用 media_products 表，加 6 列承载新品审核决策状态
-- 启动时 appcore.db_migrations.apply_pending() 自动 apply

SET @ddl := IF(
  EXISTS(
    SELECT 1
    FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'media_products'
      AND COLUMN_NAME = 'npr_decision_status'
  ),
  'SELECT 1',
  'ALTER TABLE media_products ADD COLUMN npr_decision_status ENUM(''pending'',''approved'',''rejected'') NULL DEFAULT NULL COMMENT ''新品审核决策状态'' AFTER listing_status'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(
    SELECT 1
    FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'media_products'
      AND COLUMN_NAME = 'npr_decided_countries'
  ),
  'SELECT 1',
  'ALTER TABLE media_products ADD COLUMN npr_decided_countries JSON NULL DEFAULT NULL COMMENT ''决策上架国家清单(大写ISO)'' AFTER npr_decision_status'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(
    SELECT 1
    FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'media_products'
      AND COLUMN_NAME = 'npr_decided_at'
  ),
  'SELECT 1',
  'ALTER TABLE media_products ADD COLUMN npr_decided_at DATETIME NULL DEFAULT NULL COMMENT ''决策时间'' AFTER npr_decided_countries'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(
    SELECT 1
    FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'media_products'
      AND COLUMN_NAME = 'npr_decided_by'
  ),
  'SELECT 1',
  'ALTER TABLE media_products ADD COLUMN npr_decided_by INT NULL DEFAULT NULL COMMENT ''决策人user_id'' AFTER npr_decided_at'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(
    SELECT 1
    FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'media_products'
      AND COLUMN_NAME = 'npr_rejected_reason'
  ),
  'SELECT 1',
  'ALTER TABLE media_products ADD COLUMN npr_rejected_reason VARCHAR(500) NULL DEFAULT NULL COMMENT ''不上架理由'' AFTER npr_decided_by'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @ddl := IF(
  EXISTS(
    SELECT 1
    FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'media_products'
      AND COLUMN_NAME = 'npr_eval_clip_path'
  ),
  'SELECT 1',
  'ALTER TABLE media_products ADD COLUMN npr_eval_clip_path VARCHAR(512) NULL DEFAULT NULL COMMENT ''15s截短产物本地路径'' AFTER npr_rejected_reason'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
