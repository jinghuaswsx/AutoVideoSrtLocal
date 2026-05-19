-- Product-level Dianxiaomi assets. Ranking rows keep only snapshot facts.
-- Docs-anchor: docs/superpowers/specs/2026-05-19-mingkong-product-assets-dedup-top500-design.md

CREATE TABLE IF NOT EXISTS dianxiaomi_product_assets (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  asset_key VARCHAR(96) NOT NULL,
  product_id VARCHAR(64) NULL,
  product_code VARCHAR(255) NULL,
  product_url VARCHAR(1000) NULL,
  product_name VARCHAR(500) NULL,
  product_main_image_url VARCHAR(1000) NULL,
  product_main_image_object_key VARCHAR(512) NULL,
  product_detail_images_json JSON NULL,
  product_assets_error VARCHAR(1000) NULL,
  product_cn_name VARCHAR(255) NULL,
  mk_first_material_name VARCHAR(500) NULL,
  mk_first_material_path VARCHAR(1000) NULL,
  mk_first_material_url VARCHAR(1000) NULL,
  mk_material_error VARCHAR(1000) NULL,
  last_synced_at DATETIME NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_dpa_asset_key (asset_key),
  UNIQUE KEY uk_dpa_product_code (product_code),
  KEY idx_dpa_product_id (product_id),
  KEY idx_dpa_product_url (product_url(191))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT INTO dianxiaomi_product_assets
  (asset_key, product_id, product_code, product_url, product_name,
   product_main_image_url, product_main_image_object_key, product_detail_images_json,
   product_assets_error, product_cn_name, mk_first_material_name,
   mk_first_material_path, mk_first_material_url, mk_material_error, last_synced_at)
SELECT
  asset_key,
  NULLIF(MAX(product_id), ''),
  NULLIF(MAX(product_code), ''),
  NULLIF(MAX(product_url), ''),
  NULLIF(MAX(product_name), ''),
  NULLIF(MAX(product_main_image_url), ''),
  NULLIF(MAX(product_main_image_object_key), ''),
  NULLIF(MAX(product_detail_images_json_text), ''),
  NULLIF(MAX(product_assets_error), ''),
  NULLIF(MAX(product_cn_name), ''),
  NULLIF(MAX(mk_first_material_name), ''),
  NULLIF(MAX(mk_first_material_path), ''),
  NULLIF(MAX(mk_first_material_url), ''),
  NULLIF(MAX(mk_material_error), ''),
  COALESCE(MAX(product_assets_synced_at), NOW())
FROM (
  SELECT
    CASE
      WHEN resolved_product_code <> '' THEN CONCAT('code:', SHA2(resolved_product_code, 256))
      WHEN product_url IS NOT NULL AND product_url <> '' THEN CONCAT('url:', SHA2(product_url, 256))
      ELSE CONCAT('product_id:', SHA2(product_id, 256))
    END AS asset_key,
    COALESCE(product_id, '') AS product_id,
    resolved_product_code AS product_code,
    COALESCE(product_url, '') AS product_url,
    COALESCE(product_name, '') AS product_name,
    COALESCE(product_main_image_url, '') AS product_main_image_url,
    COALESCE(product_main_image_object_key, '') AS product_main_image_object_key,
    COALESCE(CAST(product_detail_images_json AS CHAR), '') AS product_detail_images_json_text,
    COALESCE(product_assets_error, '') AS product_assets_error,
    COALESCE(product_cn_name, '') AS product_cn_name,
    COALESCE(mk_first_material_name, '') AS mk_first_material_name,
    COALESCE(mk_first_material_path, '') AS mk_first_material_path,
    COALESCE(mk_first_material_url, '') AS mk_first_material_url,
    COALESCE(mk_material_error, '') AS mk_material_error,
    product_assets_synced_at
  FROM (
    SELECT
      base_rows.*,
      CASE
        WHEN raw_product_code LIKE '%-rjc' OR raw_product_code LIKE '%_rjc'
          THEN LEFT(raw_product_code, CHAR_LENGTH(raw_product_code) - 4)
        ELSE raw_product_code
      END AS resolved_product_code
    FROM (
      SELECT
        dianxiaomi_rankings.*,
        LOWER(
          CASE
            WHEN product_code IS NOT NULL AND product_code <> '' THEN product_code
            WHEN product_url LIKE '%/products/%' THEN
              SUBSTRING_INDEX(
                SUBSTRING_INDEX(
                  SUBSTRING_INDEX(
                    SUBSTRING_INDEX(product_url, '/products/', -1),
                    '?',
                    1
                  ),
                  '#',
                  1
                ),
                '/',
                1
              )
            ELSE ''
          END
        ) AS raw_product_code
      FROM dianxiaomi_rankings
      WHERE (product_code IS NOT NULL AND product_code <> '')
         OR (product_url IS NOT NULL AND product_url <> '')
         OR (product_id IS NOT NULL AND product_id <> '')
    ) AS base_rows
  ) AS resolved_rows
) AS source_rows
GROUP BY asset_key
ON DUPLICATE KEY UPDATE
  product_id=COALESCE(NULLIF(VALUES(product_id), ''), product_id),
  product_code=COALESCE(NULLIF(VALUES(product_code), ''), product_code),
  product_url=COALESCE(NULLIF(VALUES(product_url), ''), product_url),
  product_name=COALESCE(NULLIF(VALUES(product_name), ''), product_name),
  product_main_image_url=COALESCE(NULLIF(VALUES(product_main_image_url), ''), product_main_image_url),
  product_main_image_object_key=COALESCE(NULLIF(VALUES(product_main_image_object_key), ''), product_main_image_object_key),
  product_detail_images_json=COALESCE(VALUES(product_detail_images_json), product_detail_images_json),
  product_assets_error=VALUES(product_assets_error),
  product_cn_name=COALESCE(NULLIF(VALUES(product_cn_name), ''), product_cn_name),
  mk_first_material_name=COALESCE(NULLIF(VALUES(mk_first_material_name), ''), mk_first_material_name),
  mk_first_material_path=COALESCE(NULLIF(VALUES(mk_first_material_path), ''), mk_first_material_path),
  mk_first_material_url=COALESCE(NULLIF(VALUES(mk_first_material_url), ''), mk_first_material_url),
  mk_material_error=VALUES(mk_material_error),
  last_synced_at=COALESCE(VALUES(last_synced_at), last_synced_at),
  updated_at=NOW();
