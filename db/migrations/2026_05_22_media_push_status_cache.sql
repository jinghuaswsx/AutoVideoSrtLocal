CREATE TABLE IF NOT EXISTS media_push_status_cache (
  item_id INT NOT NULL PRIMARY KEY,
  product_id INT DEFAULT NULL,
  task_id INT DEFAULT NULL,
  lang VARCHAR(16) NOT NULL DEFAULT 'en',
  latest_push_id INT DEFAULT NULL,
  pushed_at DATETIME DEFAULT NULL,
  skip_push TINYINT(1) NOT NULL DEFAULT 0,
  status VARCHAR(32) NOT NULL,
  readiness_json JSON NOT NULL,
  cache_version INT NOT NULL DEFAULT 1,
  computed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_media_push_status_cache_status (status),
  KEY idx_media_push_status_cache_lang_status (lang, status),
  KEY idx_media_push_status_cache_product (product_id),
  KEY idx_media_push_status_cache_computed (computed_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
