-- 产品 ID（product_code / video_translate_profiles.product_id）扩容 64 → 128
-- 部分 Shopify handle / 业务编码超过 64 字符，导致新建/保存失败
ALTER TABLE media_products
  MODIFY COLUMN product_code VARCHAR(128) NULL;

ALTER TABLE media_video_translate_profiles
  MODIFY COLUMN product_id VARCHAR(128) NULL COMMENT 'NULL = 用户级默认';
