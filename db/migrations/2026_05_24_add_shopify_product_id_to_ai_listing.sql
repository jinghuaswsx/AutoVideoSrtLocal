-- Migration to add shopify_product_id and shopify_published_at to ai_listing_tasks
3: ALTER TABLE `ai_listing_tasks` 
4: ADD COLUMN `shopify_product_id` VARCHAR(64) DEFAULT NULL COMMENT 'Shopify 生成的商品 ID' AFTER `error_message`,
5: ADD COLUMN `shopify_published_at` DATETIME DEFAULT NULL COMMENT 'Shopify 上架发布时间' AFTER `shopify_product_id`;
