-- 2026-05-04: 小秘云仓 (xmyc) 仓库 SKU 全量缓存 + 与 media_products 的关联

CREATE TABLE IF NOT EXISTS xmyc_storage_skus (
  id BIGINT NOT NULL AUTO_INCREMENT,
  xmyc_id VARCHAR(64) NULL,
  sku_code VARCHAR(64) NOT NULL,
  sku VARCHAR(128) NOT NULL,
  goods_name VARCHAR(500) NULL,
  unit_price DECIMAL(12,2) NULL,
  stock_available INT NULL,
  warehouse VARCHAR(255) NULL,
  shelf_code VARCHAR(64) NULL,
  product_id INT NULL,
  match_type ENUM('auto','manual') NULL,
  matched_by INT NULL,
  matched_at DATETIME NULL,
  raw_json JSON NULL,
  synced_at DATETIME NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_xmyc_storage_skus_sku (sku),
  KEY idx_xmyc_storage_skus_sku_code (sku_code),
  KEY idx_xmyc_storage_skus_product_id (product_id),
  KEY idx_xmyc_storage_skus_xmyc_id (xmyc_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
-- No explicit COLLATE: inherit database default (utf8mb4_0900_ai_ci on
-- MySQL 8) so JOINs against dianxiaomi_order_lines / media_products do
-- not hit "Illegal mix of collations" at runtime.
