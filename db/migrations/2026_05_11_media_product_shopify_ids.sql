CREATE TABLE IF NOT EXISTS media_product_shopify_ids (
  id              INT AUTO_INCREMENT PRIMARY KEY,
  product_id      INT          NOT NULL,
  domain          VARCHAR(255) NOT NULL,
  shopify_product_id VARCHAR(64) NOT NULL,
  created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_product_domain_spid (product_id, domain),
  KEY idx_media_product_spid_product (product_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Per-domain Shopify product ID cache';
