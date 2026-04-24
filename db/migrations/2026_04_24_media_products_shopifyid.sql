SET @migration_sql = IF(
  (
    SELECT COUNT(*)
    FROM information_schema.columns
    WHERE table_schema = DATABASE()
      AND table_name = 'media_products'
      AND column_name = 'shopifyid'
  ) = 0,
  'ALTER TABLE media_products ADD COLUMN shopifyid VARCHAR(32) NULL AFTER product_code',
  'SELECT 1'
);
PREPARE migration_stmt FROM @migration_sql;
EXECUTE migration_stmt;
DEALLOCATE PREPARE migration_stmt;
