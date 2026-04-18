-- db/migrations/2026_04_18_add_push_management.sql
-- 推送管理：push_logs 表 + products.ad_supported_langs + items.pushed_at / latest_push_id

-- 1. 产品级：已适配的投放语种（逗号分隔，如 "de,fr,ja"）
ALTER TABLE media_products
  ADD COLUMN ad_supported_langs VARCHAR(255) DEFAULT NULL AFTER source;

-- 2. 素材级：推送状态
ALTER TABLE media_items
  ADD COLUMN pushed_at DATETIME DEFAULT NULL,
  ADD COLUMN latest_push_id INT DEFAULT NULL,
  ADD KEY idx_pushed_at (pushed_at);

-- 3. 推送历史
CREATE TABLE IF NOT EXISTS media_push_logs (
  id INT AUTO_INCREMENT PRIMARY KEY,
  item_id INT NOT NULL,
  operator_user_id INT NOT NULL,
  status ENUM('success','failed') NOT NULL,
  request_payload JSON NOT NULL,
  response_body TEXT DEFAULT NULL,
  error_message TEXT DEFAULT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_item (item_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
