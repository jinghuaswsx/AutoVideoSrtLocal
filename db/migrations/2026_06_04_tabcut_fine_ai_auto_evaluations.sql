CREATE TABLE IF NOT EXISTS tabcut_fine_ai_auto_evaluations (
  id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  video_id VARCHAR(128) NOT NULL,
  item_id VARCHAR(128) NOT NULL,
  product_url VARCHAR(1000) NULL,
  video_name VARCHAR(500) NULL,
  video_path VARCHAR(1000) NOT NULL,
  evaluation_run_id VARCHAR(64) NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'pending',
  attempts INT NOT NULL DEFAULT 0,
  last_error VARCHAR(1000) NULL,
  started_at DATETIME NULL,
  finished_at DATETIME NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_tabcut_fine_ai_auto_video_item (video_id, item_id),
  KEY idx_tabcut_fine_ai_auto_status (status, updated_at),
  KEY idx_tabcut_fine_ai_auto_eval_run (evaluation_run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
