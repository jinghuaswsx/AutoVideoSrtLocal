-- 为 media_products 增加明空系统 ID 字段 mk_id
-- 规则：INT UNSIGNED（容纳 1-8 位十进制），允许 NULL（老数据不回填），全局 UNIQUE
ALTER TABLE media_products
  ADD COLUMN mk_id INT UNSIGNED NULL AFTER product_code,
  ADD UNIQUE KEY uk_media_products_mk_id (mk_id);
