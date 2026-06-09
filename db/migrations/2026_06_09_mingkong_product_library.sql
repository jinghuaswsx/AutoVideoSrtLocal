-- Docs-anchor: docs/superpowers/specs/2026-06-09-mingkong-product-library-foundation-design.md

CREATE TABLE IF NOT EXISTS mingkong_product_library_sync_runs (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  finished_at DATETIME DEFAULT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'running',
  window_start DATETIME DEFAULT NULL,
  window_end DATETIME DEFAULT NULL,
  products_seen INT NOT NULL DEFAULT 0,
  variants_seen INT NOT NULL DEFAULT 0,
  erp_skus_seen INT NOT NULL DEFAULT 0,
  procurement_links_seen INT NOT NULL DEFAULT 0,
  combo_components_seen INT NOT NULL DEFAULT 0,
  summary_json JSON DEFAULT NULL,
  error_message TEXT DEFAULT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_mk_product_library_runs_status_started (status, started_at),
  KEY idx_mk_product_library_runs_window (window_start, window_end)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS mingkong_products (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  product_code VARCHAR(255) DEFAULT NULL,
  mk_shopify_product_id VARCHAR(64) NOT NULL,
  mk_shop_id VARCHAR(64) DEFAULT NULL,
  mk_handle VARCHAR(512) DEFAULT NULL,
  mk_product_url VARCHAR(1000) DEFAULT NULL,
  mk_title VARCHAR(512) DEFAULT NULL,
  mk_title_cn VARCHAR(512) DEFAULT NULL,
  mk_main_image_url VARCHAR(1000) DEFAULT NULL,
  source_url TEXT NULL,
  shopify_created_at DATETIME DEFAULT NULL,
  shopify_updated_at DATETIME DEFAULT NULL,
  first_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_synced_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  raw_json JSON DEFAULT NULL,
  UNIQUE KEY uk_mk_products_shopify_product (mk_shopify_product_id),
  KEY idx_mk_products_product_code (product_code),
  KEY idx_mk_products_handle (mk_handle),
  KEY idx_mk_products_last_synced (last_synced_at),
  KEY idx_mk_products_shopify_created (shopify_created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS mingkong_product_variants (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  mingkong_product_id BIGINT NOT NULL,
  mk_shopify_product_id VARCHAR(64) NOT NULL,
  mk_shopify_variant_id VARCHAR(64) NOT NULL,
  variant_title VARCHAR(512) DEFAULT NULL,
  shopify_sku VARCHAR(128) DEFAULT NULL,
  pair_key VARCHAR(128) DEFAULT NULL,
  shopify_price DECIMAL(12,2) DEFAULT NULL,
  shopify_compare_at_price DECIMAL(12,2) DEFAULT NULL,
  shopify_inventory_quantity INT DEFAULT NULL,
  shopify_weight_grams DECIMAL(10,2) DEFAULT NULL,
  dxm_product_id VARCHAR(64) DEFAULT NULL,
  dxm_parent_id VARCHAR(64) DEFAULT NULL,
  dxm_sku VARCHAR(128) DEFAULT NULL,
  dxm_sku_code VARCHAR(64) DEFAULT NULL,
  dxm_product_sku VARCHAR(128) DEFAULT NULL,
  dxm_name VARCHAR(512) DEFAULT NULL,
  dxm_name_en VARCHAR(512) DEFAULT NULL,
  dxm_img_url VARCHAR(1000) DEFAULT NULL,
  dxm_source_url TEXT NULL,
  relation_flag TINYINT(1) NOT NULL DEFAULT 0,
  group_state INT NOT NULL DEFAULT 0,
  is_combo TINYINT(1) NOT NULL DEFAULT 0,
  raw_json JSON DEFAULT NULL,
  last_synced_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_mk_variant_shopify_variant (mk_shopify_variant_id),
  KEY idx_mk_variant_product (mingkong_product_id),
  KEY idx_mk_variant_pair_key (pair_key),
  KEY idx_mk_variant_dxm_sku (dxm_sku),
  KEY idx_mk_variant_combo (is_combo, dxm_product_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS mingkong_combo_components (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  mingkong_variant_id BIGINT DEFAULT NULL,
  combo_dxm_product_id VARCHAR(64) NOT NULL,
  combo_dxm_sku VARCHAR(128) NOT NULL,
  component_dxm_product_id VARCHAR(64) NOT NULL,
  component_sku VARCHAR(128) NOT NULL,
  component_name VARCHAR(512) DEFAULT NULL,
  component_img_url VARCHAR(1000) DEFAULT NULL,
  component_quantity INT NOT NULL DEFAULT 0,
  raw_json JSON DEFAULT NULL,
  last_synced_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_mk_combo_component (combo_dxm_product_id, component_dxm_product_id),
  KEY idx_mk_combo_parent_sku (combo_dxm_sku),
  KEY idx_mk_combo_component_sku (component_sku),
  KEY idx_mk_combo_variant (mingkong_variant_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS mingkong_procurement_links (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  mingkong_variant_id BIGINT DEFAULT NULL,
  pairing_row_id VARCHAR(64) NOT NULL,
  sku VARCHAR(128) NOT NULL,
  sku_code VARCHAR(64) DEFAULT NULL,
  dxm_product_id VARCHAR(64) DEFAULT NULL,
  dxm_name VARCHAR(512) DEFAULT NULL,
  purchase_1688_url TEXT NULL,
  source_url TEXT NULL,
  alibaba_product_id VARCHAR(64) DEFAULT NULL,
  sku_id_alibaba VARCHAR(64) DEFAULT NULL,
  supplier_id VARCHAR(64) DEFAULT NULL,
  supplier_name VARCHAR(512) DEFAULT NULL,
  pairing_state INT DEFAULT NULL,
  confidence VARCHAR(64) NOT NULL DEFAULT 'exact_sku',
  raw_json JSON DEFAULT NULL,
  last_synced_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_mk_proc_pairing_row (pairing_row_id),
  KEY idx_mk_proc_sku (sku),
  KEY idx_mk_proc_alibaba_product (alibaba_product_id),
  KEY idx_mk_proc_variant (mingkong_variant_id),
  KEY idx_mk_proc_state (pairing_state)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

ALTER TABLE mingkong_products
  MODIFY source_url TEXT NULL;

ALTER TABLE mingkong_product_variants
  MODIFY dxm_source_url TEXT NULL;

ALTER TABLE mingkong_procurement_links
  MODIFY purchase_1688_url TEXT NULL,
  MODIFY source_url TEXT NULL;
