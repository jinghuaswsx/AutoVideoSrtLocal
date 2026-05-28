CREATE TABLE IF NOT EXISTS material_evaluation_runs (
  id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  run_id VARCHAR(64) NOT NULL,
  product_id INT NOT NULL,
  media_item_id INT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'queued',
  product_url_override TEXT NULL,
  progress_json JSON NULL,
  result_json JSON NULL,
  error_message VARCHAR(1000) NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  started_at DATETIME NULL,
  completed_at DATETIME NULL,
  UNIQUE KEY uk_material_eval_run_id (run_id),
  KEY idx_material_eval_runs_product_created (product_id, created_at),
  KEY idx_material_eval_runs_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
