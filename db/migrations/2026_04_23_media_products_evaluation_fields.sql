-- 素材管理：产品备注、AI 评估信息与上下架状态
-- listing_status 默认值：DEFAULT '上架'

SET @ddl := IF(
  EXISTS(
    SELECT 1
    FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'media_products'
      AND COLUMN_NAME = 'remark'
  ),
  'SELECT 1',
  'ALTER TABLE media_products ADD COLUMN remark TEXT NULL COMMENT ''备注说明'' AFTER ad_supported_langs'
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
      AND COLUMN_NAME = 'ai_score'
  ),
  'SELECT 1',
  'ALTER TABLE media_products ADD COLUMN ai_score DECIMAL(5,2) NULL COMMENT ''AI评分'' AFTER remark'
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
      AND COLUMN_NAME = 'ai_evaluation_result'
  ),
  'SELECT 1',
  'ALTER TABLE media_products ADD COLUMN ai_evaluation_result VARCHAR(255) NULL COMMENT ''AI评估结果'' AFTER ai_score'
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
      AND COLUMN_NAME = 'ai_evaluation_detail'
  ),
  'SELECT 1',
  'ALTER TABLE media_products ADD COLUMN ai_evaluation_detail TEXT NULL COMMENT ''AI评估详情'' AFTER ai_evaluation_result'
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
      AND COLUMN_NAME = 'listing_status'
  ),
  'SELECT 1',
  'ALTER TABLE media_products ADD COLUMN listing_status ENUM(''上架'',''下架'') NOT NULL DEFAULT ''上架'' COMMENT ''是否可推送/生产素材'' AFTER ai_evaluation_detail'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
