-- 推送管理：审核信息筛选按 AI 评估结果过滤，补产品维度索引。

SET @ddl := IF(
  EXISTS(
    SELECT 1
    FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'media_products'
      AND INDEX_NAME = 'idx_media_products_ai_eval_deleted'
  ),
  'SELECT 1',
  'ALTER TABLE media_products ADD KEY idx_media_products_ai_eval_deleted (ai_evaluation_result, deleted_at)'
);
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
