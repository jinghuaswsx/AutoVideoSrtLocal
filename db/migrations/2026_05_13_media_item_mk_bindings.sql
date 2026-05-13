CREATE TABLE IF NOT EXISTS media_item_mk_bindings (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  media_item_id INT NOT NULL,
  mk_product_id INT NULL,
  mk_product_name VARCHAR(500) NULL,
  mk_video_path VARCHAR(1000) NOT NULL,
  mk_video_name VARCHAR(500) NULL,
  mk_video_image_path VARCHAR(1000) NULL,
  mk_video_metadata_json JSON NULL,
  bound_by INT NULL,
  bound_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_media_item (media_item_id),
  KEY idx_mk_product_id (mk_product_id),
  KEY idx_mk_video_path (mk_video_path(191)),
  KEY idx_mk_video_name (mk_video_name(191))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
