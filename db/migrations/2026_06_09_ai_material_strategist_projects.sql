-- AI material strategist project runs.
-- Docs anchor: docs/superpowers/specs/2026-06-09-ai-material-strategist-project-design.md

CREATE TABLE IF NOT EXISTS ai_material_strategist_projects (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  project_name VARCHAR(255) NOT NULL,
  status ENUM('running','success','failed','interrupted') NOT NULL DEFAULT 'running',
  user_id INT DEFAULT NULL,
  provider_code VARCHAR(64) NOT NULL DEFAULT 'openrouter',
  model_id VARCHAR(128) NOT NULL DEFAULT 'google/gemini-3.5-flash',
  data_window_json JSON NULL,
  data_snapshot_json JSON NULL,
  ranking_prompt_json JSON NULL,
  ranking_result_json JSON NULL,
  summary_json JSON NULL,
  progress_json JSON NULL,
  share_token VARCHAR(80) NULL,
  share_enabled_at DATETIME NULL,
  error_message MEDIUMTEXT NULL,
  started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  finished_at DATETIME NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_ai_material_project_share_token (share_token),
  KEY idx_ai_material_project_user_created (user_id, created_at),
  KEY idx_ai_material_project_status_created (status, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS ai_material_strategist_product_results (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  project_id BIGINT NOT NULL,
  rank_no INT NOT NULL,
  product_id INT NOT NULL,
  product_code VARCHAR(255) NOT NULL,
  product_name VARCHAR(500) NOT NULL,
  score DECIMAL(12,4) NOT NULL DEFAULT 0,
  metrics_json JSON NULL,
  country_summary_json JSON NULL,
  local_materials_json JSON NULL,
  mingkong_materials_json JSON NULL,
  ai_result_json JSON NULL,
  action_items_json JSON NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_ai_material_project_product (project_id, product_id),
  KEY idx_ai_material_project_rank (project_id, rank_no),
  KEY idx_ai_material_product (product_id),
  CONSTRAINT fk_ai_material_product_project
    FOREIGN KEY (project_id) REFERENCES ai_material_strategist_projects(id)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
