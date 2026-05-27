-- Product ad-launch dates for 新品投放分析.
-- Docs-anchor: docs/superpowers/specs/2026-05-27-new-product-launch-analysis-design.md#数据模型

CREATE TABLE IF NOT EXISTS product_ad_launch_dates (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  product_id INT NOT NULL,
  ad_launch_date DATE NOT NULL COMMENT 'Beijing natural date when product first entered ad data',
  source VARCHAR(32) NOT NULL COMMENT 'ad_match or created_at_fallback',
  source_level VARCHAR(32) NOT NULL COMMENT 'campaign/adset/ad/product_created_at',
  source_table VARCHAR(64) NOT NULL,
  source_row_id BIGINT DEFAULT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_product_ad_launch_product (product_id),
  KEY idx_product_ad_launch_date_source (ad_launch_date, source),
  KEY idx_product_ad_launch_source_updated (source, updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Fixed product ad-launch dates for new/old product ad analysis';
