CREATE TABLE IF NOT EXISTS media_raw_source_translations (
  id INT AUTO_INCREMENT PRIMARY KEY,
  product_id INT NOT NULL,
  source_ref_id INT NOT NULL,
  lang VARCHAR(8) NOT NULL,
  cover_object_key VARCHAR(500) NOT NULL,
  bulk_task_id VARCHAR(64) DEFAULT NULL,
  auto_translated TINYINT(1) NOT NULL DEFAULT 0,
  manually_edited_at TIMESTAMP NULL DEFAULT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    ON UPDATE CURRENT_TIMESTAMP,
  deleted_at DATETIME DEFAULT NULL,
  UNIQUE KEY uniq_source_lang (source_ref_id, lang),
  KEY idx_product_lang_deleted (product_id, lang, deleted_at),
  KEY idx_bulk_task (bulk_task_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
