-- Separate the stable AI素材军师 from the newer 投放素材AI分析 workflow.
-- Docs anchor: docs/superpowers/specs/2026-06-09-ai-material-strategist-project-design.md#2026-06-10-功能拆分纠偏

CREATE TABLE IF NOT EXISTS ad_material_ai_analysis_projects (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  project_name VARCHAR(255) NOT NULL,
  status ENUM('running','success','failed','interrupted') NOT NULL DEFAULT 'running',
  user_id INT DEFAULT NULL,
  provider_code VARCHAR(64) NOT NULL DEFAULT 'google_wj',
  model_id VARCHAR(128) NOT NULL DEFAULT 'gemini-3.5-flash',
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
  UNIQUE KEY uk_ad_material_ai_analysis_share_token (share_token),
  KEY idx_ad_material_ai_analysis_user_created (user_id, created_at),
  KEY idx_ad_material_ai_analysis_status_created (status, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS ad_material_ai_analysis_product_results (
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
  UNIQUE KEY uk_ad_material_ai_analysis_project_product (project_id, product_id),
  KEY idx_ad_material_ai_analysis_project_rank (project_id, rank_no),
  KEY idx_ad_material_ai_analysis_product (product_id),
  CONSTRAINT fk_ad_material_ai_analysis_product_project
    FOREIGN KEY (project_id) REFERENCES ad_material_ai_analysis_projects(id)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT INTO ad_material_ai_analysis_projects (
  id, project_name, status, user_id, provider_code, model_id,
  data_window_json, data_snapshot_json, ranking_prompt_json,
  ranking_result_json, summary_json, progress_json, share_token,
  share_enabled_at, error_message, started_at, finished_at, created_at, updated_at
)
SELECT
  id, project_name, status, user_id, 'google_wj', 'gemini-3.5-flash',
  data_window_json, data_snapshot_json, ranking_prompt_json,
  ranking_result_json, summary_json, progress_json, share_token,
  share_enabled_at, error_message, started_at, finished_at, created_at, updated_at
FROM ai_material_strategist_projects
WHERE project_name LIKE '投放素材AI分析%'
   OR provider_code = 'google_wj'
ON DUPLICATE KEY UPDATE
  project_name = VALUES(project_name),
  status = VALUES(status),
  user_id = VALUES(user_id),
  provider_code = VALUES(provider_code),
  model_id = VALUES(model_id),
  data_window_json = VALUES(data_window_json),
  data_snapshot_json = VALUES(data_snapshot_json),
  ranking_prompt_json = VALUES(ranking_prompt_json),
  ranking_result_json = VALUES(ranking_result_json),
  summary_json = VALUES(summary_json),
  progress_json = VALUES(progress_json),
  share_token = VALUES(share_token),
  share_enabled_at = VALUES(share_enabled_at),
  error_message = VALUES(error_message),
  started_at = VALUES(started_at),
  finished_at = VALUES(finished_at),
  created_at = VALUES(created_at),
  updated_at = VALUES(updated_at);

INSERT INTO ad_material_ai_analysis_product_results (
  id, project_id, rank_no, product_id, product_code, product_name, score,
  metrics_json, country_summary_json, local_materials_json,
  mingkong_materials_json, ai_result_json, action_items_json,
  created_at, updated_at
)
SELECT
  r.id, r.project_id, r.rank_no, r.product_id, r.product_code, r.product_name, r.score,
  r.metrics_json, r.country_summary_json, r.local_materials_json,
  r.mingkong_materials_json, r.ai_result_json, r.action_items_json,
  r.created_at, r.updated_at
FROM ai_material_strategist_product_results r
JOIN ad_material_ai_analysis_projects p ON p.id = r.project_id
ON DUPLICATE KEY UPDATE
  rank_no = VALUES(rank_no),
  product_code = VALUES(product_code),
  product_name = VALUES(product_name),
  score = VALUES(score),
  metrics_json = VALUES(metrics_json),
  country_summary_json = VALUES(country_summary_json),
  local_materials_json = VALUES(local_materials_json),
  mingkong_materials_json = VALUES(mingkong_materials_json),
  ai_result_json = VALUES(ai_result_json),
  action_items_json = VALUES(action_items_json),
  updated_at = VALUES(updated_at);

DELETE r
FROM ai_material_strategist_product_results r
JOIN ad_material_ai_analysis_projects p ON p.id = r.project_id;

DELETE s
FROM ai_material_strategist_projects s
JOIN ad_material_ai_analysis_projects a ON a.id = s.id
WHERE s.project_name LIKE '投放素材AI分析%'
   OR s.provider_code = 'google_wj';

ALTER TABLE ai_material_strategist_projects
  MODIFY provider_code VARCHAR(64) NOT NULL DEFAULT 'openrouter',
  MODIFY model_id VARCHAR(128) NOT NULL DEFAULT 'google/gemini-3.5-flash';

INSERT INTO llm_use_case_bindings (
  use_case_code, provider_code, model_id, extra_config, enabled, updated_by, updated_at
) VALUES
  ('medias.ai_material_strategist_rank_products', 'openrouter', 'google/gemini-3.5-flash', NULL, 1, NULL, CURRENT_TIMESTAMP),
  ('medias.ai_material_strategist_product_analysis', 'openrouter', 'google/gemini-3.5-flash', NULL, 1, NULL, CURRENT_TIMESTAMP),
  ('medias.ad_material_ai_analysis_rank_products', 'google_wj', 'gemini-3.5-flash', NULL, 1, NULL, CURRENT_TIMESTAMP),
  ('medias.ad_material_ai_analysis_product_analysis', 'google_wj', 'gemini-3.5-flash', NULL, 1, NULL, CURRENT_TIMESTAMP)
ON DUPLICATE KEY UPDATE
  provider_code = VALUES(provider_code),
  model_id = VALUES(model_id),
  enabled = VALUES(enabled),
  updated_at = VALUES(updated_at);
