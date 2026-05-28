-- Cached product and per-language ad summaries for /medias product list.
-- Docs anchor: docs/superpowers/specs/2026-05-28-medias-product-ad-status-cache-design.md

CREATE TABLE IF NOT EXISTS media_product_ad_summary_cache (
  product_id INT NOT NULL PRIMARY KEY,
  order_revenue_usd DECIMAL(14,4) NOT NULL DEFAULT 0,
  shipping_revenue_usd DECIMAL(14,4) NOT NULL DEFAULT 0,
  total_revenue_usd DECIMAL(14,4) NOT NULL DEFAULT 0,
  ad_spend_usd DECIMAL(14,4) NOT NULL DEFAULT 0,
  active_7d_ad_spend_usd DECIMAL(14,4) NOT NULL DEFAULT 0,
  overall_roas DECIMAL(12,4) DEFAULT NULL,
  delivery_status ENUM('active','stopped','never') NOT NULL DEFAULT 'never',
  computed_at DATETIME NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_media_product_ad_summary_status (delivery_status),
  KEY idx_media_product_ad_summary_computed (computed_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS media_product_lang_ad_summary_cache (
  product_id INT NOT NULL,
  lang VARCHAR(16) NOT NULL,
  item_count INT NOT NULL DEFAULT 0,
  pushed_video_count INT NOT NULL DEFAULT 0,
  ad_spend_usd DECIMAL(14,4) NOT NULL DEFAULT 0,
  purchase_value_usd DECIMAL(14,4) NOT NULL DEFAULT 0,
  ad_roas DECIMAL(12,4) DEFAULT NULL,
  active_7d_ad_spend_usd DECIMAL(14,4) NOT NULL DEFAULT 0,
  computed_at DATETIME NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (product_id, lang),
  KEY idx_media_product_lang_ad_summary_lang (lang),
  KEY idx_media_product_lang_ad_summary_computed (computed_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
