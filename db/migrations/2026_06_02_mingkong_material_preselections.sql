-- Docs-anchor: docs/superpowers/specs/2026-06-02-mingkong-material-preselection-design.md

CREATE TABLE IF NOT EXISTS mingkong_material_preselections (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  material_key CHAR(64) NOT NULL,
  product_code VARCHAR(255) DEFAULT NULL,
  mk_product_id BIGINT DEFAULT NULL,
  product_name VARCHAR(500) DEFAULT NULL,
  product_english_name VARCHAR(500) DEFAULT NULL,
  product_url VARCHAR(1000) DEFAULT NULL,
  product_main_image_url VARCHAR(1000) DEFAULT NULL,
  video_name VARCHAR(500) DEFAULT NULL,
  video_path VARCHAR(1000) DEFAULT NULL,
  video_cover_url VARCHAR(1000) DEFAULT NULL,
  media_product_id INT DEFAULT NULL,
  media_item_id INT DEFAULT NULL,
  selected_countries_json JSON NOT NULL,
  operator_note TEXT DEFAULT NULL,
  source_snapshot_at DATETIME DEFAULT NULL,
  created_by INT DEFAULT NULL,
  updated_by INT DEFAULT NULL,
  processed_by INT DEFAULT NULL,
  processed_parent_task_id BIGINT DEFAULT NULL,
  processed_at DATETIME DEFAULT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_mk_material_preselections_material_key (material_key),
  KEY idx_mk_material_preselections_processed (processed_at),
  KEY idx_mk_material_preselections_updated (updated_at),
  KEY idx_mk_material_preselections_media_item (media_item_id),
  KEY idx_mk_material_preselections_product_code (product_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

UPDATE users
SET permissions = JSON_SET(
  CASE
    WHEN JSON_VALID(COALESCE(CAST(permissions AS CHAR), '{}'))
    THEN COALESCE(permissions, JSON_OBJECT())
    ELSE JSON_OBJECT()
  END,
  '$.mk_material_preselection', JSON_EXTRACT('true', '$')
)
WHERE username = 'guqian';
