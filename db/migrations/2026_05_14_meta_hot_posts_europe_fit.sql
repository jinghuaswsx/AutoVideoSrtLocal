-- Europe market fit assessments for Meta hot post materials.
-- Docs-anchor: docs/superpowers/specs/2026-05-14-meta-hot-posts-europe-fit-design.md

CREATE TABLE IF NOT EXISTS meta_hot_post_europe_assessments (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  post_id BIGINT UNSIGNED NOT NULL,
  status ENUM('pending', 'running', 'done', 'failed', 'suspended') NOT NULL DEFAULT 'pending',
  attempts INT UNSIGNED NOT NULL DEFAULT 0,
  last_error MEDIUMTEXT NULL,
  suitability_score DECIMAL(6, 2) NULL,
  recommendation VARCHAR(32) NULL,
  direct_reuse TINYINT(1) NOT NULL DEFAULT 0,
  best_countries_json JSON NULL,
  country_scores_json JSON NULL,
  strengths_json JSON NULL,
  risks_json JSON NULL,
  required_changes_json JSON NULL,
  reasoning TEXT NULL,
  llm_provider VARCHAR(64) NULL,
  llm_model VARCHAR(128) NULL,
  llm_response_json JSON NULL,
  video_optimization_json JSON NULL,
  assessed_at DATETIME NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uniq_meta_hot_post_europe_assessments_post (post_id),
  KEY idx_meta_hot_post_europe_assessments_status (status, attempts, updated_at),
  KEY idx_meta_hot_post_europe_assessments_rank (status, suitability_score, assessed_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT INTO llm_use_case_bindings (
  use_case_code,
  provider_code,
  model_id,
  extra_config,
  enabled,
  updated_by
) VALUES (
  'meta_hot_posts.europe_fit',
  'openrouter',
  'google/gemini-3-flash-preview',
  NULL,
  1,
  NULL
)
ON DUPLICATE KEY UPDATE
  provider_code = VALUES(provider_code),
  model_id = VALUES(model_id),
  enabled = VALUES(enabled);
